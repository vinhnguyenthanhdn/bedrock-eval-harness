"""Tests for the Converse request body.

Every assertion here names a field path from the API reference in
`beval.request.CONTRACT_SOURCE`. That is the whole point of the module: nothing else in
the harness can tell you that `system` is a list rather than a string, and a mistake in
that shape does not surface until a real call — on someone else's credentials.
"""

import copy
import json
import unittest
from pathlib import Path

from beval.request import (
    BODY_KEYS,
    INFERENCE_CONFIG_KEYS,
    build_converse_body,
    converse_kwargs,
    resolve_setting,
)
from beval.suite import load_suite, parse_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SUITE = REPO_ROOT / "suites" / "support-triage" / "suite.json"

MINIMAL_CHECK = [{"type": "contains_any", "values": ["billing"]}]


def make_suite(defaults=None, case_extra=None, case_input=None):
    """A one-case suite built through the real parser, so tests never bypass validation."""
    data = {
        "format_version": 1,
        "suite_id": "unit",
        "description": "One case, built for a request-shape test.",
        "cases": [
            {
                "id": "only-case",
                "input": case_input or {"user": "route this ticket"},
                "checks": MINIMAL_CHECK,
                **(case_extra or {}),
            }
        ],
    }
    if defaults is not None:
        data["defaults"] = defaults
    suite, problems = parse_suite(data)
    assert suite is not None, problems
    return suite, suite.cases[0]


class MessagesTest(unittest.TestCase):
    def test_user_turn_sits_at_messages_content_text(self):
        suite, case = make_suite(case_input={"user": "the ticket text"})
        body = build_converse_body(suite, case)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][0]["content"][0]["text"], "the ticket text")

    def test_exactly_one_message_per_case(self):
        # The harness is single-turn by design: a case is one question asked once. A
        # second message here would mean a case is carrying conversation state that the
        # suite format cannot record, so a rerun would not be the same request.
        suite, case = make_suite()
        body = build_converse_body(suite, case)
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(len(body["messages"][0]["content"]), 1)


class SystemPromptTest(unittest.TestCase):
    def test_system_is_a_list_of_objects_not_a_string(self):
        suite, case = make_suite(defaults={"system": "you are a router"})
        body = build_converse_body(suite, case)
        self.assertEqual(body["system"], [{"text": "you are a router"}])

    def test_case_system_beats_suite_default(self):
        suite, case = make_suite(
            defaults={"system": "from defaults"},
            case_input={"user": "u", "system": "from the case"},
        )
        self.assertEqual(build_converse_body(suite, case)["system"], [{"text": "from the case"}])

    def test_no_system_anywhere_leaves_the_key_out(self):
        # Not `"system": []` and not `"system": None`: an empty list is a system prompt of
        # nothing, which is a different request from one that never had the field.
        suite, case = make_suite()
        self.assertNotIn("system", build_converse_body(suite, case))


class InferenceConfigTest(unittest.TestCase):
    def test_max_output_tokens_maps_to_max_tokens(self):
        suite, case = make_suite(defaults={"max_output_tokens": 200})
        self.assertEqual(build_converse_body(suite, case)["inferenceConfig"]["maxTokens"], 200)

    def test_case_settings_beat_suite_defaults(self):
        suite, case = make_suite(
            defaults={"max_output_tokens": 200, "temperature": 1},
            case_extra={"max_output_tokens": 50, "temperature": 0.2},
        )
        config = build_converse_body(suite, case)["inferenceConfig"]
        self.assertEqual(config, {"maxTokens": 50, "temperature": 0.2})

    def test_temperature_zero_is_sent(self):
        # The value most suites want is exactly the one a truthiness test drops. A missing
        # temperature means the model's default sampling, so losing this silently makes a
        # deterministic suite non-deterministic without changing any file.
        suite, case = make_suite(defaults={"temperature": 0})
        self.assertEqual(build_converse_body(suite, case)["inferenceConfig"], {"temperature": 0})

    def test_no_settings_leaves_inference_config_out(self):
        suite, case = make_suite()
        self.assertNotIn("inferenceConfig", build_converse_body(suite, case))

    def test_only_settings_the_suite_can_record_are_sent(self):
        suite, case = make_suite(defaults={"max_output_tokens": 10, "temperature": 0.5})
        config = build_converse_body(suite, case)["inferenceConfig"]
        self.assertEqual(tuple(config), INFERENCE_CONFIG_KEYS)


class BodyShapeTest(unittest.TestCase):
    def test_model_id_is_not_in_the_body(self):
        # `modelId` is a path parameter on POST /model/{modelId}/converse. Putting it in
        # the body is accepted by nothing and rejected with a message about the body.
        suite, case = make_suite(defaults={"system": "s", "max_output_tokens": 1})
        self.assertNotIn("modelId", build_converse_body(suite, case))

    def test_converse_kwargs_carries_the_model_id_beside_the_body(self):
        suite, case = make_suite()
        kwargs = converse_kwargs("anthropic.claude-3-haiku-20240307-v1:0", suite, case)
        self.assertEqual(kwargs["modelId"], "anthropic.claude-3-haiku-20240307-v1:0")
        self.assertEqual({k: v for k, v in kwargs.items() if k != "modelId"},
                         build_converse_body(suite, case))

    def test_body_has_no_keys_beyond_the_contract(self):
        suite, case = make_suite(defaults={"system": "s", "max_output_tokens": 1, "temperature": 0})
        self.assertEqual(tuple(build_converse_body(suite, case)), BODY_KEYS)

    def test_body_is_json_serialisable(self):
        # A recorded run stores the request that produced it, so anything unserialisable
        # here becomes a run that cannot be written after the model has already been paid.
        suite, case = make_suite(defaults={"system": "s", "max_output_tokens": 1, "temperature": 0})
        json.dumps(build_converse_body(suite, case))

    def test_body_does_not_alias_suite_state(self):
        suite, case = make_suite(defaults={"system": "s", "max_output_tokens": 1})
        before = copy.deepcopy(suite.defaults)
        body = build_converse_body(suite, case)
        body["system"][0]["text"] = "mutated"
        body["inferenceConfig"]["maxTokens"] = 999
        self.assertEqual(suite.defaults, before)


class SampleSuiteTest(unittest.TestCase):
    """The suite shipped in the repo, so the test moves when the sample moves."""

    def test_every_sample_case_builds_a_body(self):
        suite, problems = load_suite(SAMPLE_SUITE)
        self.assertIsNotNone(suite, problems)
        for case in suite.cases:
            with self.subTest(case=case.id):
                body = build_converse_body(suite, case)
                self.assertEqual(body["messages"][0]["content"][0]["text"], case.user)
                self.assertEqual(body["system"], [{"text": suite.defaults["system"]}])
                self.assertEqual(
                    body["inferenceConfig"], {"maxTokens": 200, "temperature": 0}
                )


class ResolveSettingTest(unittest.TestCase):
    def test_missing_everywhere_is_none(self):
        suite, case = make_suite()
        self.assertIsNone(resolve_setting(suite, case, "temperature"))

    def test_zero_from_defaults_is_not_treated_as_missing(self):
        suite, case = make_suite(defaults={"temperature": 0})
        self.assertEqual(resolve_setting(suite, case, "temperature"), 0)


if __name__ == "__main__":
    unittest.main()
