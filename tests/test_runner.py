"""Tests for the run loop, the record, and replaying it.

None of this calls AWS. The point of the record is that it does not have to: a run made
once on someone's account replays on a machine with no credentials and produces the same
run file — but only while the question is still the same question, which is the property
most of these tests are about.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from beval.bedrock import BedrockConverseClient, MissingSDK, _boto3_client
from beval.cli import main
from beval.client import ScriptedClient, make_converse_response
from beval.ledger import score_run
from beval.request import build_converse_body
from beval.runfile import load_run, parse_run
from beval.runner import (
    RecordedClient,
    RecordMismatch,
    RunAborted,
    load_record,
    parse_record,
    run_suite,
    run_to_json,
    write_json,
)
from beval.suite import load_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SUITE = REPO_ROOT / "suites" / "support-triage" / "suite.json"
FIXTURE_RUN = REPO_ROOT / "tests" / "fixtures" / "runs" / "support-triage-fixture.json"
FIXTURE_RECORD = REPO_ROOT / "tests" / "fixtures" / "records" / "support-triage-record.json"


def sample_suite():
    suite, problems = load_suite(SAMPLE_SUITE)
    assert suite is not None, problems
    return suite


def answering_client(suite, text=None, **kwargs):
    return ScriptedClient(
        {
            case.user: make_converse_response(
                text if text is not None else f"answer for {case.id}", 100, 20, 500, **kwargs
            )
            for case in suite.cases
        }
    )


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class RunLoopTest(unittest.TestCase):
    def test_every_case_is_asked_once_and_lands_in_the_run(self):
        suite = sample_suite()
        outcome = run_suite(suite, "fixture.model-v1", answering_client(suite), run_id="r1")
        self.assertEqual([r.case_id for r in outcome.run.responses], [c.id for c in suite.cases])
        self.assertEqual(len(outcome.record.exchanges), len(suite.cases))
        self.assertEqual(outcome.failures, ())

    def test_a_failed_case_is_left_out_rather_than_filled_in(self):
        # The scorer counts a case with no response as a failure. That only holds if
        # nothing invents a blank answer here: a blank answer is a case that was asked and
        # answered badly, which is a different fact.
        suite = sample_suite()
        client = answering_client(suite)
        del client._responses[suite.cases[0].user]
        outcome = run_suite(suite, "m", client, run_id="r1")
        self.assertEqual(len(outcome.run.responses), len(suite.cases) - 1)
        self.assertEqual([case_id for case_id, _ in outcome.failures], [suite.cases[0].id])
        scored = score_run(suite, outcome.run)
        self.assertIn(suite.cases[0].id, scored.missing_case_ids)

    def test_a_malformed_response_is_a_failure_not_a_zero(self):
        suite = sample_suite()
        broken = make_converse_response("x", 1, 1)
        del broken["usage"]
        client = ScriptedClient(
            {case.user: (broken if i == 0 else make_converse_response("ok", 1, 1))
             for i, case in enumerate(suite.cases)}
        )
        outcome = run_suite(suite, "m", client, run_id="r1")
        self.assertEqual(len(outcome.failures), 1)
        self.assertIn("usage", outcome.failures[0][1])

    def test_a_run_where_everything_failed_writes_nothing(self):
        suite = sample_suite()
        with self.assertRaises(RunAborted):
            run_suite(suite, "m", ScriptedClient({}), run_id="r1")

    def test_the_run_file_it_writes_loads_back(self):
        suite = sample_suite()
        outcome = run_suite(suite, "fixture.model-v1", answering_client(suite), run_id="r1")
        run, problems = parse_run(run_to_json(outcome.run))
        self.assertIsNotNone(run, problems)
        self.assertEqual(run.suite_id, suite.suite_id)
        self.assertEqual(len(run.responses), len(suite.cases))

    def test_optional_fields_are_omitted_rather_than_null(self):
        suite = sample_suite()
        client = ScriptedClient(
            {case.user: make_converse_response("ok", 1, 1, latency_ms=None) for case in suite.cases}
        )
        outcome = run_suite(suite, "m", client, run_id="r1")
        first = run_to_json(outcome.run)["responses"][0]
        self.assertNotIn("latency_ms", first)


class RecordTest(unittest.TestCase):
    def test_the_record_holds_the_request_that_was_sent(self):
        suite = sample_suite()
        outcome = run_suite(suite, "m", answering_client(suite), run_id="r1")
        exchange = outcome.record.exchanges[0]
        self.assertEqual(exchange.case_id, suite.cases[0].id)
        self.assertEqual(
            exchange.request["messages"][0]["content"][0]["text"], suite.cases[0].user
        )
        self.assertEqual(exchange.request["system"], [{"text": suite.defaults["system"]}])

    def test_a_record_round_trips_through_json(self):
        suite = sample_suite()
        outcome = run_suite(suite, "m", answering_client(suite), run_id="r1")
        record, problems = parse_record(json.loads(json.dumps(outcome.record.to_json())))
        self.assertIsNotNone(record, problems)
        self.assertEqual(len(record.exchanges), len(suite.cases))

    def test_the_record_says_what_produced_the_answers(self):
        suite = sample_suite()
        live = run_suite(suite, "m", answering_client(suite), run_id="r1", source="bedrock")
        self.assertEqual(live.record.to_json()["source"], "bedrock")
        hand = run_suite(suite, "m", answering_client(suite), run_id="r1", source="fixture")
        self.assertEqual(hand.record.to_json()["source"], "fixture")

    def test_a_record_without_a_source_claims_the_less(self):
        # An old record cannot prove a model produced it, so it reads as hand-written
        # rather than as a measurement. Guessing the other way would let a report call
        # invented numbers a measurement, which is the one mistake this repo cannot make.
        record, problems = parse_record(
            {
                "format_version": 1,
                "suite_id": "s",
                "model_id": "m",
                "exchanges": [{"case_id": "c", "request": {}, "response": {}}],
            }
        )
        self.assertIsNotNone(record, problems)
        self.assertEqual(record.source, "fixture")

    def test_an_unknown_source_is_refused(self):
        record, problems = parse_record(
            {
                "format_version": 1,
                "suite_id": "s",
                "model_id": "m",
                "source": "trust-me",
                "exchanges": [{"case_id": "c", "request": {}, "response": {}}],
            }
        )
        self.assertIsNone(record)
        self.assertIn("source", problems[0])

    def test_an_unknown_record_version_is_refused(self):
        record, problems = parse_record({"format_version": 99, "suite_id": "s"})
        self.assertIsNone(record)
        self.assertIn("format_version", problems[0])


class ReplayTest(unittest.TestCase):
    def test_replay_reproduces_the_run_file(self):
        suite = sample_suite()
        live = run_suite(suite, "fixture.model-v1", answering_client(suite), run_id="r1")
        replayed = run_suite(
            suite, "fixture.model-v1", RecordedClient(live.record), run_id="r1"
        )
        self.assertEqual(run_to_json(replayed.run), run_to_json(live.run))

    def test_replay_refuses_when_the_request_changed(self):
        # This is what makes a record evidence rather than a cache. Change the system
        # prompt and the recorded answer is an answer to a question nobody asked.
        suite = sample_suite()
        live = run_suite(suite, "m", answering_client(suite), run_id="r1")
        changed = dict(suite.defaults, system="a different system prompt")
        moved_suite = type(suite)(
            suite_id=suite.suite_id,
            description=suite.description,
            cases=suite.cases,
            path=suite.path,
            defaults=changed,
        )
        client = RecordedClient(live.record)
        with self.assertRaises(RecordMismatch) as caught:
            client.invoke("m", build_converse_body(moved_suite, moved_suite.cases[0]))
        self.assertIn("recorded request differs", str(caught.exception))

        # And through the loop: every case now asks a question nothing answered, so the
        # run aborts instead of writing a run file that scores zero for the wrong reason.
        with self.assertRaises(RunAborted):
            run_suite(moved_suite, "m", RecordedClient(live.record), run_id="r2")

    def test_replay_refuses_a_case_it_never_recorded(self):
        suite = sample_suite()
        live = run_suite(suite, "m", answering_client(suite), run_id="r1")
        client = RecordedClient(live.record)
        with self.assertRaises(RecordMismatch):
            client.invoke("m", {"messages": [{"role": "user", "content": [{"text": "new"}]}]})


class CommittedRecordTest(unittest.TestCase):
    """The record shipped in the repo, replayed the way CI replays it.

    It carries the same answers as `support-triage-fixture.json`, so this asserts the
    whole path — build the request, read the response, write the run file — against a run
    file that was written by hand long before the runner existed. If any of those steps
    drifts, the two stop matching.
    """

    def test_it_replays_into_the_committed_fixture_run(self):
        suite = sample_suite()
        record, problems = load_record(FIXTURE_RECORD)
        self.assertIsNotNone(record, problems)

        outcome = run_suite(
            suite, record.model_id, RecordedClient(record), run_id="replay", source=record.source
        )
        expected, problems = load_run(FIXTURE_RUN)
        self.assertIsNotNone(expected, problems)

        produced = {r["case_id"]: r for r in run_to_json(outcome.run)["responses"]}
        wanted = {r["case_id"]: r for r in json.loads(FIXTURE_RUN.read_text())["responses"]}
        self.assertEqual(sorted(produced), sorted(wanted))
        for case_id, fields in wanted.items():
            with self.subTest(case=case_id):
                # A superset, not an equality: the hand-written fixture leaves out
                # `stop_reason` on some cases, while a real response always carries one.
                # Everything the fixture does state has to come back unchanged.
                self.assertEqual(
                    {k: produced[case_id][k] for k in fields}, fields
                )

        # And the thing anyone would actually check: the same score, case for case.
        expected_scored = score_run(sample_suite(), expected)
        produced_scored = score_run(sample_suite(), outcome.run)
        self.assertEqual(
            {r.case_id: r.passed for r in produced_scored.results},
            {r.case_id: r.passed for r in expected_scored.results},
        )

    def test_it_is_labelled_as_hand_written(self):
        record, problems = load_record(FIXTURE_RECORD)
        self.assertIsNotNone(record, problems)
        self.assertEqual(record.source, "fixture")


class SdkBoundaryTest(unittest.TestCase):
    """The contract with boto3, asserted without installing boto3."""

    class FakeBedrock:
        def __init__(self):
            self.kwargs = None
            self.response = {"output": {"message": {"content": [{"text": "hi"}]}}}

        def converse(self, **kwargs):
            self.kwargs = kwargs
            return self.response

    def test_model_id_travels_beside_the_body_not_inside_it(self):
        fake = self.FakeBedrock()
        client = BedrockConverseClient(client_factory=lambda region: fake)
        body = {"messages": [{"role": "user", "content": [{"text": "q"}]}], "system": [{"text": "s"}]}
        client.invoke("some.model", body)
        self.assertEqual(fake.kwargs["modelId"], "some.model")
        self.assertEqual(fake.kwargs["messages"], body["messages"])
        self.assertEqual(fake.kwargs["system"], body["system"])
        self.assertNotIn("body", fake.kwargs)

    def test_the_response_is_returned_untouched(self):
        fake = self.FakeBedrock()
        client = BedrockConverseClient(client_factory=lambda region: fake)
        self.assertIs(client.invoke("m", {"messages": []}), fake.response)

    def test_the_region_reaches_the_factory(self):
        seen = {}
        BedrockConverseClient(
            region="ap-southeast-1",
            client_factory=lambda region: seen.setdefault("region", region) and None,
        )
        self.assertEqual(seen["region"], "ap-southeast-1")

    def test_a_missing_sdk_says_what_to_install_and_where_credentials_come_from(self):
        def no_boto3(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("No module named 'boto3'")
            return original_import(name, *args, **kwargs)

        import builtins

        original_import = builtins.__import__
        builtins.__import__ = no_boto3
        try:
            with self.assertRaises(MissingSDK) as caught:
                _boto3_client(None)
        finally:
            builtins.__import__ = original_import
        message = str(caught.exception)
        self.assertIn("pip install boto3", message)
        self.assertIn("credential chain", message)


class RunCommandTest(unittest.TestCase):
    def test_replay_from_files_writes_a_run_that_scores(self):
        suite = sample_suite()
        live = run_suite(
            suite, "fixture.model-v1", answering_client(suite), run_id="r1", source="fixture"
        )
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "record.json"
            run_path = Path(tmp) / "run.json"
            write_json(record_path, live.record.to_json())

            code, out, err = run_cli(
                ["run", str(SAMPLE_SUITE), "--replay", str(record_path), "--out", str(run_path)]
            )
            self.assertEqual(code, 0, err)
            self.assertIn("run file", out)

            record, problems = load_record(record_path)
            self.assertIsNotNone(record, problems)

            written = json.loads(run_path.read_text())
            self.assertEqual(written["source"], "fixture")

            code, out, err = run_cli(["score", str(SAMPLE_SUITE), str(run_path)])
            self.assertEqual(code, 0, err)
            # Replaying hand-written answers does not turn them into a measurement, and
            # the report has to say so — not just the file.
            self.assertIn("not a call to a model", out)

    def test_replaying_a_real_recording_stays_a_measurement(self):
        # The other direction, and the reason a replay does not simply hardcode `fixture`:
        # answers a model really returned are still real when they are replayed.
        suite = sample_suite()
        live = run_suite(
            suite, "some.model", answering_client(suite), run_id="r1", source="bedrock"
        )
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "record.json"
            run_path = Path(tmp) / "run.json"
            write_json(record_path, live.record.to_json())
            code, _, err = run_cli(
                ["run", str(SAMPLE_SUITE), "--replay", str(record_path), "--out", str(run_path)]
            )
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(run_path.read_text())["source"], "bedrock")

    def test_replay_needs_no_model_flag(self):
        suite = sample_suite()
        live = run_suite(suite, "fixture.model-v1", answering_client(suite), run_id="r1")
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "record.json"
            write_json(record_path, live.record.to_json())
            code, out, err = run_cli(["run", str(SAMPLE_SUITE), "--replay", str(record_path)])
            self.assertEqual(code, 0, err)
            self.assertIn("fixture.model-v1", out)

    def test_a_model_flag_that_contradicts_the_record_is_refused(self):
        suite = sample_suite()
        live = run_suite(suite, "fixture.model-v1", answering_client(suite), run_id="r1")
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "record.json"
            write_json(record_path, live.record.to_json())
            code, _, err = run_cli(
                ["run", str(SAMPLE_SUITE), "--replay", str(record_path), "--model", "other.model"]
            )
            self.assertEqual(code, 2)
            self.assertIn("other.model", err)

    def test_run_without_model_or_replay_is_a_usage_error(self):
        code, _, err = run_cli(["run", str(SAMPLE_SUITE)])
        self.assertEqual(code, 2)
        self.assertIn("--model", err)

    def test_a_record_for_another_suite_is_refused(self):
        suite = sample_suite()
        live = run_suite(suite, "m", answering_client(suite), run_id="r1")
        data = live.record.to_json()
        data["suite_id"] = "some-other-suite"
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "record.json"
            write_json(record_path, data)
            code, _, err = run_cli(["run", str(SAMPLE_SUITE), "--replay", str(record_path)])
            self.assertEqual(code, 1)
            self.assertIn("some-other-suite", err)


if __name__ == "__main__":
    unittest.main()
