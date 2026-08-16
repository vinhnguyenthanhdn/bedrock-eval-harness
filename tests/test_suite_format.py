"""Tests for the suite format.

Each invalid-suite test asserts on the *reason* reported, not just on the fact that
something failed — a validator that rejects everything for the wrong reason passes a
test that only counts errors.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from beval.checks import CHECK_TYPES, validate_check
from beval.suite import load_suite, parse_suite

REPO_ROOT = Path(__file__).resolve().parents[1]

MINIMAL = {
    "format_version": 1,
    "suite_id": "demo",
    "description": "A suite used by the tests.",
    "cases": [
        {
            "id": "first",
            "input": {"user": "hello"},
            "checks": [{"type": "contains_any", "values": ["hi"]}],
        }
    ],
}


def mutate(**changes):
    """A copy of MINIMAL with top-level fields replaced or removed (value None)."""
    data = copy.deepcopy(MINIMAL)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def problems_for(data):
    suite, problems = parse_suite(data)
    return suite, problems


def joined(problems):
    return " | ".join(problems)


class ValidSuite(unittest.TestCase):
    def test_minimal_suite_parses(self):
        suite, problems = problems_for(MINIMAL)
        self.assertEqual(problems, [])
        self.assertIsNotNone(suite)
        self.assertEqual(suite.suite_id, "demo")
        self.assertEqual(len(suite.cases), 1)

    def test_defaults_are_kept(self):
        suite, problems = problems_for(
            mutate(defaults={"system": "be brief", "max_output_tokens": 50, "temperature": 0})
        )
        self.assertEqual(problems, [])
        self.assertEqual(suite.defaults["max_output_tokens"], 50)

    def test_weight_defaults_to_one_and_totals_up(self):
        data = mutate()
        data["cases"].append(
            {
                "id": "second",
                "input": {"user": "hello again"},
                "checks": [{"type": "max_words", "value": 10}],
                "weight": 2.5,
            }
        )
        suite, problems = problems_for(data)
        self.assertEqual(problems, [])
        self.assertEqual(suite.cases[0].weight, 1.0)
        self.assertEqual(suite.total_weight, 3.5)

    def test_case_keeps_its_own_request_settings(self):
        data = mutate()
        data["cases"][0]["temperature"] = 1
        data["cases"][0]["max_output_tokens"] = 32
        suite, problems = problems_for(data)
        self.assertEqual(problems, [])
        self.assertEqual(suite.cases[0].temperature, 1)
        self.assertEqual(suite.cases[0].max_output_tokens, 32)


class RejectedSuite(unittest.TestCase):
    def assert_rejected(self, data, expected_fragment):
        suite, problems = problems_for(data)
        self.assertIsNone(suite, "suite should not load")
        self.assertTrue(
            any(expected_fragment in problem for problem in problems),
            f"expected a problem mentioning {expected_fragment!r}, got: {joined(problems)}",
        )

    def test_unknown_format_version_is_refused(self):
        self.assert_rejected(mutate(format_version=2), "format_version must be 1")

    def test_missing_format_version_is_refused(self):
        self.assert_rejected(mutate(format_version=None), "format_version must be 1")

    def test_suite_id_must_be_slug(self):
        self.assert_rejected(mutate(suite_id="Support Triage"), "'suite_id' must match")

    def test_unknown_top_level_field(self):
        self.assert_rejected(mutate(model="claude"), "unknown field 'model'")

    def test_empty_case_list(self):
        self.assert_rejected(mutate(cases=[]), "'cases' must be a non-empty list")

    def test_duplicate_case_ids(self):
        data = mutate()
        data["cases"].append(copy.deepcopy(data["cases"][0]))
        self.assert_rejected(data, "duplicate case id 'first'")

    def test_case_without_checks_is_refused(self):
        # A case nobody can fail would add weight to the score while measuring nothing.
        data = mutate()
        data["cases"][0]["checks"] = []
        self.assert_rejected(data, "'checks' must be a non-empty list")

    def test_case_without_user_input(self):
        data = mutate()
        data["cases"][0]["input"] = {"system": "only a system prompt"}
        self.assert_rejected(data, "'input.user' must be a non-empty string")

    def test_blank_user_input(self):
        data = mutate()
        data["cases"][0]["input"]["user"] = "   "
        self.assert_rejected(data, "'input.user' must be a non-empty string")

    def test_zero_weight(self):
        data = mutate()
        data["cases"][0]["weight"] = 0
        self.assert_rejected(data, "'weight' must be a positive number")

    def test_temperature_out_of_range(self):
        data = mutate()
        data["cases"][0]["temperature"] = 1.5
        self.assert_rejected(data, "'temperature' must be a number between 0 and 1")

    def test_boolean_is_not_a_token_count(self):
        data = mutate()
        data["cases"][0]["max_output_tokens"] = True
        self.assert_rejected(data, "'max_output_tokens' must be a positive integer")

    def test_all_problems_are_reported_at_once(self):
        data = mutate(suite_id="Bad Id")
        data["cases"][0]["checks"] = []
        data["cases"][0]["weight"] = -1
        _, problems = problems_for(data)
        self.assertGreaterEqual(len(problems), 3, joined(problems))

    def test_top_level_must_be_an_object(self):
        self.assert_rejected([MINIMAL], "top level must be an object")


class Checks(unittest.TestCase):
    def test_every_documented_type_is_accepted(self):
        samples = {
            "contains_all": {"values": ["a"]},
            "contains_any": {"values": ["a"]},
            "not_contains": {"values": ["a"]},
            "regex": {"pattern": "a+"},
            "max_words": {"value": 5},
            "json_field_equals": {"path": "result.queue", "value": "billing"},
        }
        self.assertEqual(sorted(samples), sorted(CHECK_TYPES))
        for ctype, fields in samples.items():
            with self.subTest(ctype=ctype):
                self.assertEqual(validate_check({"type": ctype, **fields}, "x"), [])

    def test_unknown_check_type(self):
        problems = validate_check({"type": "vibes"}, "x")
        self.assertTrue(any("unknown check type" in p for p in problems), problems)

    def test_missing_required_field(self):
        problems = validate_check({"type": "max_words"}, "x")
        self.assertTrue(any("missing required field 'value'" in p for p in problems), problems)

    def test_unknown_field_on_a_check(self):
        # Typos are the failure mode this guards: a misspelled field would otherwise be
        # dropped and the check would silently stop testing what it claims to test.
        problems = validate_check({"type": "max_words", "value": 5, "valeu": 3}, "x")
        self.assertTrue(any("unknown field 'valeu'" in p for p in problems), problems)

    def test_broken_regex_is_reported_not_raised(self):
        problems = validate_check({"type": "regex", "pattern": "("}, "x")
        self.assertTrue(any("does not compile" in p for p in problems), problems)

    def test_empty_values_list(self):
        problems = validate_check({"type": "contains_all", "values": []}, "x")
        self.assertTrue(any("non-empty list" in p for p in problems), problems)

    def test_ignore_case_must_be_boolean(self):
        problems = validate_check(
            {"type": "contains_any", "values": ["a"], "ignore_case": "yes"}, "x"
        )
        self.assertTrue(any("must be true or false" in p for p in problems), problems)

    def test_label_is_allowed(self):
        self.assertEqual(
            validate_check({"type": "max_words", "value": 5, "label": "short"}, "x"), []
        )


class Files(unittest.TestCase):
    def test_broken_json_reports_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "suite.json"
            path.write_text('{"format_version": 1,}', encoding="utf-8")
            suite, problems = load_suite(path)
            self.assertIsNone(suite)
            self.assertTrue(any("not valid JSON" in p for p in problems), problems)

    def test_missing_file_is_a_problem_not_a_crash(self):
        suite, problems = load_suite(REPO_ROOT / "suites" / "does-not-exist.json")
        self.assertIsNone(suite)
        self.assertTrue(any("cannot read file" in p for p in problems), problems)


class ShippedSuites(unittest.TestCase):
    def test_every_suite_in_the_repo_is_valid(self):
        paths = sorted((REPO_ROOT / "suites").rglob("*.json"))
        self.assertTrue(paths, "no suite files found under suites/")
        for path in paths:
            with self.subTest(path=path.name):
                suite, problems = load_suite(path)
                self.assertIsNotNone(suite, joined(problems))

    def test_sample_suite_has_the_shape_the_readme_claims(self):
        suite, problems = load_suite(REPO_ROOT / "suites" / "support-triage" / "suite.json")
        self.assertEqual(problems, [])
        self.assertEqual(suite.suite_id, "support-triage")
        self.assertEqual(len(suite.cases), 6)
        tags = {tag for case in suite.cases for tag in case.tags}
        self.assertIn("robustness", tags)

    def test_documented_check_types_match_the_code(self):
        doc = (REPO_ROOT / "docs" / "case-format.md").read_text(encoding="utf-8")
        for ctype in CHECK_TYPES:
            with self.subTest(ctype=ctype):
                self.assertIn(f"`{ctype}`", doc)


if __name__ == "__main__":
    unittest.main()
