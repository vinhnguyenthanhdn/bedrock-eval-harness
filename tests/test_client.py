"""Tests for the SDK boundary: what goes out, and how the answer is read back.

This file is the substitute for a real Bedrock call. Every assertion names a field path
from the Converse reference (see `docs/converse-request.md`), and the failures it is built
to catch are the ones a real call would otherwise catch first and charge for: a request
with the wrong shape, and a number read out of the wrong field.
"""

import unittest
from pathlib import Path

from beval.client import (
    METRICS_LATENCY,
    STOP_REASON_TRUNCATED,
    USAGE_INPUT,
    USAGE_OUTPUT,
    ResponseShapeError,
    ScriptedClient,
    make_converse_response,
    read_response,
)
from beval.request import build_converse_body
from beval.suite import load_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SUITE = REPO_ROOT / "suites" / "support-triage" / "suite.json"


def sample_suite():
    suite, problems = load_suite(SAMPLE_SUITE)
    assert suite is not None, problems
    return suite


class ReadResponseTest(unittest.TestCase):
    def test_reads_every_field_from_its_documented_path(self):
        raw = {
            "output": {"message": {"role": "assistant", "content": [{"text": "billing"}]}},
            "usage": {USAGE_INPUT: 210, USAGE_OUTPUT: 28, "totalTokens": 238},
            "metrics": {METRICS_LATENCY: 640},
            "stopReason": "end_turn",
        }
        response = read_response("refund-past-window", raw)
        self.assertEqual(response.case_id, "refund-past-window")
        self.assertEqual(response.output_text, "billing")
        self.assertEqual(response.input_tokens, 210)
        self.assertEqual(response.output_tokens, 28)
        self.assertEqual(response.latency_ms, 640)
        self.assertEqual(response.stop_reason, "end_turn")

    def test_snake_case_usage_is_not_accepted(self):
        # The run file says `input_tokens`; the service says `inputTokens`. Accepting both
        # would hide the day the rename is done in the wrong direction, and the symptom
        # would be a cost report of zero rather than an error.
        raw = make_converse_response("ok", 10, 2)
        raw["usage"] = {"input_tokens": 10, "output_tokens": 2}
        with self.assertRaises(ResponseShapeError) as caught:
            read_response("c", raw)
        self.assertIn("usage.inputTokens", str(caught.exception))

    def test_missing_usage_raises_instead_of_costing_zero(self):
        raw = make_converse_response("ok", 10, 2)
        del raw["usage"]
        with self.assertRaises(ResponseShapeError):
            read_response("c", raw)

    def test_zero_tokens_is_a_value_not_a_missing_field(self):
        response = read_response("c", make_converse_response("", 0, 0))
        self.assertEqual((response.input_tokens, response.output_tokens), (0, 0))

    def test_negative_and_non_integer_counts_are_refused(self):
        for bad in (-1, 1.5, "10", True, None):
            with self.subTest(bad=bad):
                raw = make_converse_response("ok", 10, 2)
                raw["usage"][USAGE_INPUT] = bad
                with self.assertRaises(ResponseShapeError):
                    read_response("c", raw)

    def test_missing_latency_is_none_not_zero(self):
        # Zero would be a measurement, and it would drag every percentile down. The ledger
        # reports latency over the responses that carried one.
        response = read_response("c", make_converse_response("ok", 10, 2, latency_ms=None))
        self.assertIsNone(response.latency_ms)

    def test_latency_comes_from_the_service_not_the_clock(self):
        raw = make_converse_response("ok", 10, 2, latency_ms=1234)
        self.assertEqual(read_response("c", raw).latency_ms, 1234)

    def test_truncated_answer_keeps_its_stop_reason(self):
        raw = make_converse_response("half an ans", 10, 300, stop_reason=STOP_REASON_TRUNCATED)
        self.assertEqual(read_response("c", raw).stop_reason, STOP_REASON_TRUNCATED)

    def test_multiple_text_blocks_are_joined_in_order(self):
        raw = make_converse_response("", 10, 2)
        raw["output"]["message"]["content"] = [{"text": "one "}, {"text": "two"}]
        self.assertEqual(read_response("c", raw).output_text, "one two")

    def test_non_text_blocks_are_skipped_not_crashed_on(self):
        raw = make_converse_response("", 10, 2)
        raw["output"]["message"]["content"] = [
            {"toolUse": {"name": "search"}},
            {"text": "the answer"},
        ]
        self.assertEqual(read_response("c", raw).output_text, "the answer")

    def test_missing_output_path_names_the_path(self):
        raw = make_converse_response("ok", 10, 2)
        del raw["output"]["message"]
        with self.assertRaises(ResponseShapeError) as caught:
            read_response("c", raw)
        self.assertIn("output.message", str(caught.exception))

    def test_empty_content_list_is_refused(self):
        raw = make_converse_response("ok", 10, 2)
        raw["output"]["message"]["content"] = []
        with self.assertRaises(ResponseShapeError):
            read_response("c", raw)


class ScriptedClientTest(unittest.TestCase):
    def test_it_records_the_request_it_was_given(self):
        suite = sample_suite()
        case = suite.cases[0]
        client = ScriptedClient([make_converse_response("billing", 210, 28)])
        client.invoke("fixture.model-v1", build_converse_body(suite, case))
        model_id, body = client.calls[0]
        self.assertEqual(model_id, "fixture.model-v1")
        self.assertEqual(body["messages"][0]["content"][0]["text"], case.user)
        self.assertEqual(body["system"], [{"text": suite.defaults["system"]}])

    def test_a_scripted_answer_survives_the_round_trip(self):
        suite = sample_suite()
        case = suite.cases[0]
        client = ScriptedClient({case.user: make_converse_response("billing", 210, 28, 640)})
        raw = client.invoke("fixture.model-v1", build_converse_body(suite, case))
        response = read_response(case.id, raw)
        self.assertEqual(response.output_text, "billing")
        self.assertEqual(response.input_tokens, 210)
        self.assertEqual(response.latency_ms, 640)

    def test_every_sample_case_can_be_asked_and_read(self):
        # The end-to-end shape check: build a request for each case in the shipped suite,
        # answer it through the boundary, and read the answer back into run-file records.
        suite = sample_suite()
        client = ScriptedClient(
            {case.user: make_converse_response(f"answer for {case.id}", 100, 20, 500)
             for case in suite.cases}
        )
        responses = [
            read_response(case.id, client.invoke("fixture.model-v1", build_converse_body(suite, case)))
            for case in suite.cases
        ]
        self.assertEqual(len(responses), len(suite.cases))
        self.assertEqual([r.case_id for r in responses], [c.id for c in suite.cases])
        self.assertEqual(len(client.calls), len(suite.cases))

    def test_running_out_of_scripted_answers_is_an_error(self):
        # Silently repeating the last answer would make a run look complete while every
        # case after the first shared one response.
        client = ScriptedClient([make_converse_response("ok", 1, 1)])
        client.invoke("m", {"messages": []})
        with self.assertRaises(IndexError):
            client.invoke("m", {"messages": []})

    def test_an_unscripted_question_is_an_error(self):
        client = ScriptedClient({"known question": make_converse_response("ok", 1, 1)})
        body = {"messages": [{"role": "user", "content": [{"text": "unknown question"}]}]}
        with self.assertRaises(KeyError):
            client.invoke("m", body)


if __name__ == "__main__":
    unittest.main()
