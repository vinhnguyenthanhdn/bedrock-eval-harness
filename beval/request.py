"""Turn one case into the body of a Bedrock Converse request.

Pure on purpose: no client, no credentials, no boto3 import. The runner is the part that
needs AWS; deciding *what to send* is not, and keeping it separate means the shape of the
request is covered by tests that run on a clean checkout.

The field paths below come from the Converse API reference read on 2026-08-16, not from
memory. Two of them are easy to get wrong in a way nothing catches until a real call:

- `system` is a **list of objects** (`[{"text": ...}]`), not a string.
- `modelId` is a path parameter, so it does **not** belong in the body. `converse_kwargs`
  is where it gets attached, next to the body rather than inside it.

Everything the suite format can express maps onto `inferenceConfig`; anything AWS accepts
that the format does not have (`topP`, `stopSequences`, `additionalModelRequestFields`)
is deliberately absent rather than passed through, because a setting the suite cannot
record is a setting a rerun a month later cannot reproduce.
"""

from __future__ import annotations

from typing import Any

from .suite import Case, Suite

CONTRACT_SOURCE = (
    "https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html"
    " (read 2026-08-16)"
)

# Every key this module is allowed to put in a body. Kept as data so a test can assert
# the body never grows a field silently.
BODY_KEYS = ("messages", "system", "inferenceConfig")
INFERENCE_CONFIG_KEYS = ("maxTokens", "temperature")


def resolve_setting(suite: Suite, case: Case, name: str) -> Any:
    """Value of one request setting for this case: the case wins, defaults fill in.

    `None` means neither level set it, which is different from a level setting it to
    zero — `temperature: 0` is the value most suites actually want.
    """
    case_value = getattr(case, name, None)
    if case_value is not None:
        return case_value
    return suite.defaults.get(name)


def build_converse_body(suite: Suite, case: Case) -> dict[str, Any]:
    """The Converse request body for one case. No `modelId` — that is a path parameter."""
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": [{"text": case.user}]}],
    }

    system = resolve_setting(suite, case, "system")
    if system is not None:
        body["system"] = [{"text": system}]

    inference_config: dict[str, Any] = {}
    max_output_tokens = resolve_setting(suite, case, "max_output_tokens")
    if max_output_tokens is not None:
        inference_config["maxTokens"] = max_output_tokens
    temperature = resolve_setting(suite, case, "temperature")
    if temperature is not None:
        inference_config["temperature"] = temperature
    # An empty `inferenceConfig` is not the same as no `inferenceConfig`: send the key
    # empty and the request is still valid, but the run file then records settings the
    # suite never asked for. Leave it out.
    if inference_config:
        body["inferenceConfig"] = inference_config

    return body


def converse_kwargs(model_id: str, suite: Suite, case: Case) -> dict[str, Any]:
    """What a Converse call takes: the body plus the model id that rides beside it."""
    return {"modelId": model_id, **build_converse_body(suite, case)}
