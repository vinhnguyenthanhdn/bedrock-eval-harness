"""Tests for check evaluation, scoring, and the cost/latency ledger."""

import unittest
from pathlib import Path

from beval.evaluate import evaluate_check
from beval.ledger import load_prices, parse_prices, percentile, score_run
from beval.runfile import load_run, parse_run
from beval.suite import load_suite, parse_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_RUN = REPO_ROOT / "tests" / "fixtures" / "runs" / "support-triage-fixture.json"
FIXTURE_PRICES = REPO_ROOT / "tests" / "fixtures" / "prices-fixture.json"
SAMPLE_SUITE = REPO_ROOT / "suites" / "support-triage" / "suite.json"


def check(ctype, **fields):
    return {"type": ctype, **fields}


class EvaluateChecks(unittest.TestCase):
    def test_contains_all(self):
        self.assertTrue(evaluate_check(check("contains_all", values=["a", "b"]), "a b c").passed)
        result = evaluate_check(check("contains_all", values=["a", "z"]), "a b c")
        self.assertFalse(result.passed)
        self.assertIn("'z'", result.detail)

    def test_contains_any(self):
        self.assertTrue(evaluate_check(check("contains_any", values=["z", "b"]), "a b").passed)
        self.assertFalse(evaluate_check(check("contains_any", values=["z"]), "a b").passed)

    def test_ignore_case_applies_to_both_sides(self):
        self.assertFalse(evaluate_check(check("contains_any", values=["ABC"]), "abc").passed)
        self.assertTrue(
            evaluate_check(check("contains_any", values=["ABC"], ignore_case=True), "abc").passed
        )

    def test_not_contains(self):
        self.assertTrue(evaluate_check(check("not_contains", values=["x"]), "a b").passed)
        result = evaluate_check(check("not_contains", values=["b"]), "a b")
        self.assertFalse(result.passed)
        self.assertIn("forbidden", result.detail)

    def test_regex(self):
        self.assertTrue(evaluate_check(check("regex", pattern=r"\d{3}"), "id 123").passed)
        self.assertFalse(evaluate_check(check("regex", pattern=r"\d{4}"), "id 123").passed)

    def test_max_words_counts_whitespace_separated_words(self):
        self.assertTrue(evaluate_check(check("max_words", value=3), "one two three").passed)
        result = evaluate_check(check("max_words", value=2), "one two three")
        self.assertFalse(result.passed)
        self.assertIn("3 words", result.detail)

    def test_json_field_equals_walks_a_dot_path(self):
        text = '{"result": {"queue": "billing"}}'
        self.assertTrue(
            evaluate_check(check("json_field_equals", path="result.queue", value="billing"), text).passed
        )
        self.assertFalse(
            evaluate_check(check("json_field_equals", path="result.queue", value="sales"), text).passed
        )

    def test_json_field_equals_on_non_json_output(self):
        result = evaluate_check(check("json_field_equals", path="queue", value="billing"), "OK")
        self.assertFalse(result.passed)
        self.assertIn("not JSON", result.detail)

    def test_json_field_equals_on_missing_field(self):
        result = evaluate_check(
            check("json_field_equals", path="queue", value="billing"), '{"other": 1}'
        )
        self.assertFalse(result.passed)
        self.assertIn("no field", result.detail)

    def test_an_unscored_check_type_raises_instead_of_passing(self):
        # A new check type added to the schema but not to the evaluator must not be
        # silently treated as a pass.
        with self.assertRaises(ValueError):
            evaluate_check({"type": "not_implemented_yet"}, "anything")


class Scoring(unittest.TestCase):
    def load(self):
        suite, problems = load_suite(SAMPLE_SUITE)
        self.assertEqual(problems, [])
        run, problems = load_run(FIXTURE_RUN)
        self.assertEqual(problems, [])
        return suite, run

    def test_fixture_run_scores_as_expected(self):
        suite, run = self.load()
        scored = score_run(suite, run)
        failed = {result.case_id for result in scored.results if not result.passed}
        self.assertEqual(failed, {"sso-domain-transfer", "prompt-injection-in-ticket"})
        self.assertEqual(scored.earned_weight, 6)
        self.assertEqual(scored.possible_weight, 9)
        self.assertAlmostEqual(scored.score, 6 / 9)

    def test_a_case_without_a_response_counts_against_the_score(self):
        suite, run = self.load()
        trimmed = run.__class__(
            suite_id=run.suite_id,
            run_id=run.run_id,
            model_id=run.model_id,
            source=run.source,
            responses=run.responses[:-1],
        )
        scored = score_run(suite, trimmed)
        self.assertEqual(scored.missing_case_ids, ("prompt-injection-in-ticket",))
        # The missing case was worth 2 and was failing anyway, so the ceiling stays 9.
        self.assertEqual(scored.possible_weight, 9)

    def test_a_response_for_an_unknown_case_is_reported(self):
        suite, run = self.load()
        extra = run.responses[0].__class__(
            case_id="not-in-the-suite", output_text="{}", input_tokens=1, output_tokens=1
        )
        widened = run.__class__(
            suite_id=run.suite_id,
            run_id=run.run_id,
            model_id=run.model_id,
            source=run.source,
            responses=run.responses + (extra,),
        )
        self.assertEqual(score_run(suite, widened).extra_case_ids, ("not-in-the-suite",))

    def test_token_totals(self):
        suite, run = self.load()
        scored = score_run(suite, run)
        self.assertEqual(scored.input_tokens, 1314)
        self.assertEqual(scored.output_tokens, 139)

    def test_cost_uses_the_supplied_price_list_only(self):
        suite, run = self.load()
        scored = score_run(suite, run)
        self.assertIsNone(scored.cost_usd(None))
        prices, problems = load_prices(FIXTURE_PRICES)
        self.assertEqual(problems, [])
        expected = 1314 / 1000 * 3.0 + 139 / 1000 * 15.0
        self.assertAlmostEqual(scored.cost_usd(prices), expected)

    def test_no_cost_when_the_model_is_absent_from_the_price_list(self):
        suite, run = self.load()
        other = run.__class__(
            suite_id=run.suite_id,
            run_id=run.run_id,
            model_id="some.other-model",
            source=run.source,
            responses=run.responses,
        )
        prices, _ = load_prices(FIXTURE_PRICES)
        self.assertIsNone(score_run(suite, other).cost_usd(prices))


class Percentiles(unittest.TestCase):
    def test_nearest_rank(self):
        values = [10, 20, 30, 40]
        self.assertEqual(percentile(values, 0.5), 20)
        self.assertEqual(percentile(values, 0.95), 40)
        self.assertEqual(percentile(values, 1), 40)

    def test_empty_sample_is_none_not_zero(self):
        self.assertIsNone(percentile([], 0.5))

    def test_single_value(self):
        self.assertEqual(percentile([7], 0.95), 7)


class RunFile(unittest.TestCase):
    minimal = {
        "format_version": 1,
        "suite_id": "demo",
        "run_id": "r1",
        "model_id": "m1",
        "source": "fixture",
        "responses": [
            {"case_id": "a", "output_text": "x", "input_tokens": 1, "output_tokens": 2}
        ],
    }

    def test_minimal_run_parses(self):
        run, problems = parse_run(dict(self.minimal))
        self.assertEqual(problems, [])
        self.assertEqual(run.responses[0].latency_ms, None)

    def test_missing_token_count_is_refused(self):
        data = {**self.minimal, "responses": [{"case_id": "a", "output_text": "x", "input_tokens": 1}]}
        run, problems = parse_run(data)
        self.assertIsNone(run)
        self.assertTrue(any("output_tokens" in p for p in problems), problems)

    def test_unknown_source_is_refused(self):
        run, problems = parse_run({**self.minimal, "source": "guessed"})
        self.assertIsNone(run)
        self.assertTrue(any("'source' must be one of" in p for p in problems), problems)

    def test_duplicate_case_response(self):
        data = {**self.minimal, "responses": self.minimal["responses"] * 2}
        run, problems = parse_run(data)
        self.assertIsNone(run)
        self.assertTrue(any("duplicate response" in p for p in problems), problems)

    def test_fixture_is_marked_as_not_a_measurement(self):
        run, problems = load_run(FIXTURE_RUN)
        self.assertEqual(problems, [])
        self.assertFalse(run.is_measurement)


class Prices(unittest.TestCase):
    def test_the_shipped_example_is_refused_until_it_is_filled_in(self):
        prices, problems = load_prices(REPO_ROOT / "pricing.example.json")
        self.assertIsNone(prices, "placeholder prices must never produce a cost report")
        self.assertTrue(any("placeholder" in p for p in problems), problems)

    def test_negative_rate_is_refused(self):
        prices, problems = parse_prices(
            {
                "format_version": 1,
                "source_url": "https://example.invalid",
                "read_on": "2026-08-16",
                "models": {"m": {"input_per_1k_usd": -1, "output_per_1k_usd": 1}},
            }
        )
        self.assertIsNone(prices)
        self.assertTrue(any("non-negative" in p for p in problems), problems)

    def test_price_list_carries_its_provenance(self):
        prices, problems = load_prices(FIXTURE_PRICES)
        self.assertEqual(problems, [])
        self.assertTrue(prices.source_url)
        self.assertTrue(prices.read_on)


class SuiteAndRunAgree(unittest.TestCase):
    def test_fixture_covers_every_case_of_the_sample_suite(self):
        suite, _ = load_suite(SAMPLE_SUITE)
        run, _ = load_run(FIXTURE_RUN)
        self.assertEqual({c.id for c in suite.cases}, {r.case_id for r in run.responses})

    def test_parse_suite_and_parse_run_reject_the_wrong_file(self):
        # Handing a run file to the suite parser must fail loudly, not half-load.
        import json

        data = json.loads(FIXTURE_RUN.read_text(encoding="utf-8"))
        suite, problems = parse_suite(data)
        self.assertIsNone(suite)
        self.assertTrue(problems)


class CliScoreExitCodes(unittest.TestCase):
    def test_score_without_min_score_exits_zero_even_with_failing_cases(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from beval.cli import main

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["score", str(SAMPLE_SUITE), str(FIXTURE_RUN)])
        self.assertEqual(code, 0)

    def test_score_with_satisfied_min_score_exits_zero(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from beval.cli import main

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            # Fixture score is 66.7%, min-score 50% is satisfied
            code = main(["score", str(SAMPLE_SUITE), str(FIXTURE_RUN), "--min-score", "50"])
        self.assertEqual(code, 0)

    def test_score_with_unsatisfied_min_score_exits_one(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from beval.cli import main

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            # Fixture score is 66.7%, min-score 80% fails
            code = main(["score", str(SAMPLE_SUITE), str(FIXTURE_RUN), "--min-score", "80"])
        self.assertEqual(code, 1)
        self.assertIn("below the required minimum", err.getvalue())

    def test_score_with_invalid_min_score_exits_two(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from beval.cli import main

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            # Out of bounds (>100 or <0) returns exit code 2
            code1 = main(["score", str(SAMPLE_SUITE), str(FIXTURE_RUN), "--min-score", "150"])
            code2 = main(["score", str(SAMPLE_SUITE), str(FIXTURE_RUN), "--min-score", "-5"])
        self.assertEqual(code1, 2)
        self.assertEqual(code2, 2)
        self.assertIn("must be between 0 and 100", err.getvalue())


if __name__ == "__main__":
    unittest.main()

