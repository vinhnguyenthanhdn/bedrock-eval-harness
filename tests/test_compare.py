"""Tests for the run-to-run diff.

The case that matters most is the one where the total score does not move. Two runs at
66.7% with a different set of cases passing is the situation `compare` exists for, and it is
the situation a score-only report cannot show.
"""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from beval.cli import main
from beval.compare import compare_scored
from beval.ledger import score_run
from beval.runfile import load_run, parse_run
from beval.suite import load_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SUITE = REPO_ROOT / "suites" / "support-triage" / "suite.json"
FIXTURE_RUN = REPO_ROOT / "tests" / "fixtures" / "runs" / "support-triage-fixture.json"
FIXTURE_RUN_V2 = REPO_ROOT / "tests" / "fixtures" / "runs" / "support-triage-fixture-v2.json"
FIXTURE_PRICES = REPO_ROOT / "tests" / "fixtures" / "prices-fixture.json"


def load(path):
    suite, problems = load_suite(SAMPLE_SUITE)
    assert suite is not None, problems
    run, problems = load_run(path)
    assert run is not None, problems
    return score_run(suite, run)


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CompareLogic(unittest.TestCase):
    def test_equal_scores_can_still_hide_a_regression(self):
        diff = compare_scored(load(FIXTURE_RUN), load(FIXTURE_RUN_V2))

        # The headline number says nothing happened.
        self.assertAlmostEqual(diff.score_delta, 0.0)
        self.assertAlmostEqual(diff.baseline.score, diff.candidate.score)

        # One case went backwards and one went forwards.
        self.assertEqual(diff.regressed, ("password-reset-loop",))
        self.assertEqual(diff.fixed, ("sso-domain-transfer",))
        self.assertTrue(diff.has_regression)

    def test_a_run_compared_with_itself_changes_nothing(self):
        diff = compare_scored(load(FIXTURE_RUN), load(FIXTURE_RUN))
        self.assertEqual(diff.regressed, ())
        self.assertEqual(diff.fixed, ())
        self.assertEqual(len(diff.still_passing), 4)
        self.assertEqual(len(diff.still_failing), 2)
        self.assertFalse(diff.has_regression)

    def test_every_suite_case_lands_in_exactly_one_bucket(self):
        diff = compare_scored(load(FIXTURE_RUN), load(FIXTURE_RUN_V2))
        buckets = (
            diff.regressed
            + diff.fixed
            + diff.still_passing
            + diff.still_failing
            + diff.scored_only_in_baseline
            + diff.scored_only_in_candidate
            + diff.missing_from_both
        )
        case_ids = [case.id for case in diff.baseline.suite.cases]
        self.assertEqual(sorted(buckets), sorted(case_ids))
        self.assertEqual(len(buckets), len(set(buckets)))

    def test_case_order_follows_the_suite_not_the_run_file(self):
        # Three regressions, so the order is actually observable — with one case per bucket
        # any ordering rule at all would pass, which is not a test of ordering.
        data = json.loads(FIXTURE_RUN_V2.read_text(encoding="utf-8"))
        wrong_queue = '{"queue": "sales", "reason": "x"}'
        for response in data["responses"]:
            if response["case_id"] in ("refund-past-window", "ambiguous-charge-after-cancel"):
                response["output_text"] = wrong_queue
        suite_order = [case.id for case in load(FIXTURE_RUN).suite.cases]
        expected = tuple(
            case_id
            for case_id in suite_order
            if case_id
            in {"refund-past-window", "password-reset-loop", "ambiguous-charge-after-cancel"}
        )

        suite, _ = load_suite(SAMPLE_SUITE)
        for responses in (data["responses"], list(reversed(data["responses"]))):
            variant = dict(data, responses=responses)
            parsed, problems = parse_run(variant)
            self.assertIsNotNone(parsed, problems)
            diff = compare_scored(load(FIXTURE_RUN), score_run(suite, parsed))
            # Same verdicts either way round, and listed in the suite's order both times.
            self.assertEqual(diff.regressed, expected)
            self.assertEqual(diff.fixed, ("sso-domain-transfer",))

    def test_a_case_answered_by_only_one_run_is_not_a_flip(self):
        data = json.loads(FIXTURE_RUN_V2.read_text(encoding="utf-8"))
        # Drop a case that passes in the baseline. It has no verdict here, so calling it a
        # regression would be inventing one.
        data["responses"] = [r for r in data["responses"] if r["case_id"] != "refund-past-window"]
        trimmed, problems = parse_run(data)
        self.assertIsNotNone(trimmed, problems)

        suite, _ = load_suite(SAMPLE_SUITE)
        diff = compare_scored(load(FIXTURE_RUN), score_run(suite, trimmed))
        self.assertNotIn("refund-past-window", diff.regressed)
        self.assertEqual(diff.scored_only_in_baseline, ("refund-past-window",))

    def test_fixture_against_measurement_is_flagged(self):
        data = json.loads(FIXTURE_RUN_V2.read_text(encoding="utf-8"))
        data["source"] = "bedrock"
        data["recorded_at"] = "2026-08-16T00:00:00Z"
        measured, problems = parse_run(data)
        self.assertIsNotNone(measured, problems)

        suite, _ = load_suite(SAMPLE_SUITE)
        diff = compare_scored(load(FIXTURE_RUN), score_run(suite, measured))
        self.assertTrue(diff.compares_fixture_with_measurement)

        same_kind = compare_scored(load(FIXTURE_RUN), load(FIXTURE_RUN_V2))
        self.assertFalse(same_kind.compares_fixture_with_measurement)


class CompareCli(unittest.TestCase):
    def test_report_names_the_regressed_case_even_when_the_score_is_flat(self):
        code, out, _ = run_cli(
            ["compare", str(SAMPLE_SUITE), str(FIXTURE_RUN), str(FIXTURE_RUN_V2)]
        )
        self.assertEqual(code, 0)
        self.assertIn("REGRESSED", out)
        self.assertIn("password-reset-loop", out)
        self.assertIn("+0.0 points", out)

    def test_fail_on_regression_exits_one_and_says_which_case(self):
        code, _, err = run_cli(
            [
                "compare",
                str(SAMPLE_SUITE),
                str(FIXTURE_RUN),
                str(FIXTURE_RUN_V2),
                "--fail-on-regression",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("password-reset-loop", err)

    def test_fail_on_regression_exits_zero_when_nothing_regressed(self):
        code, out, _ = run_cli(
            [
                "compare",
                str(SAMPLE_SUITE),
                str(FIXTURE_RUN),
                str(FIXTURE_RUN),
                "--fail-on-regression",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("no case changed verdict", out)

    def test_without_the_flag_a_regression_still_exits_zero(self):
        code, _, _ = run_cli(
            ["compare", str(SAMPLE_SUITE), str(FIXTURE_RUN), str(FIXTURE_RUN_V2)]
        )
        self.assertEqual(code, 0)

    def test_run_recorded_against_another_suite_is_refused(self):
        data = json.loads(FIXTURE_RUN_V2.read_text(encoding="utf-8"))
        data["suite_id"] = "some-other-suite"
        foreign = REPO_ROOT / "tests" / "fixtures" / "runs" / "tmp-foreign-suite.json"
        foreign.write_text(json.dumps(data), encoding="utf-8")
        try:
            code, _, err = run_cli(
                ["compare", str(SAMPLE_SUITE), str(FIXTURE_RUN), str(foreign)]
            )
        finally:
            foreign.unlink()
        self.assertEqual(code, 1)
        self.assertIn("some-other-suite", err)

    def test_unreadable_run_is_reported_not_crashed(self):
        missing = REPO_ROOT / "tests" / "fixtures" / "runs" / "does-not-exist.json"
        code, _, err = run_cli(
            ["compare", str(SAMPLE_SUITE), str(FIXTURE_RUN), str(missing)]
        )
        self.assertEqual(code, 1)
        self.assertIn("does-not-exist.json", err)

    def test_cost_line_names_a_model_the_price_list_does_not_cover(self):
        prices = json.loads(FIXTURE_PRICES.read_text(encoding="utf-8"))
        del prices["models"]["fixture.model-v2"]
        partial = REPO_ROOT / "tests" / "fixtures" / "tmp-partial-prices.json"
        partial.write_text(json.dumps(prices), encoding="utf-8")
        try:
            code, out, _ = run_cli(
                [
                    "compare",
                    str(SAMPLE_SUITE),
                    str(FIXTURE_RUN),
                    str(FIXTURE_RUN_V2),
                    "--prices",
                    str(partial),
                ]
            )
        finally:
            partial.unlink()
        self.assertEqual(code, 0)
        self.assertIn("fixture.model-v2", out)
        # No dollar figure at all, rather than one side's cost presented as a delta.
        self.assertNotIn("$", out.split("cost ")[-1])

    def test_both_models_priced_prints_both_costs(self):
        code, out, _ = run_cli(
            [
                "compare",
                str(SAMPLE_SUITE),
                str(FIXTURE_RUN),
                str(FIXTURE_RUN_V2),
                "--prices",
                str(FIXTURE_PRICES),
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("$6.027000 → $2.009000", out)


if __name__ == "__main__":
    unittest.main()
