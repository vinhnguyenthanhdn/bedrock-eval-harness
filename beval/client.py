"""The boundary between the harness and the Bedrock SDK.

One narrow surface — `invoke(model_id, body) -> response` — with the raw Converse response
on both sides of it. Everything above the boundary is pure and tested offline; everything
below it is `boto3`, and lands with the runner.

Splitting it here is what replaces a real call in CI. A fake client that returns the exact
response shape the service returns catches the two mistakes a real call would otherwise
catch first, at someone's expense: sending the wrong request, and reading the response out
of the wrong field.

Reading is strict for the same reason the run-file loader is strict. A token count that
quietly defaults to zero becomes a cost report of zero, which is worse than a crash: it
looks like an answer.
"""

from __future__ import annotations

from typing import Any, Protocol

from .runfile import Response

# The service reports these; the run file stores the snake_case names. The rename is the
# single most likely place to lose a number silently, so it happens in exactly one place.
USAGE_INPUT = "inputTokens"
USAGE_OUTPUT = "outputTokens"
METRICS_LATENCY = "latencyMs"

# stopReason when the answer was cut off by maxTokens. A case that failed because it was
# truncated failed for a different reason than a case that answered wrongly, and the run
# file keeps the distinction so a report can tell them apart.
STOP_REASON_TRUNCATED = "max_tokens"


class ResponseShapeError(Exception):
    """The response did not have the shape the Converse API documents."""


class ConverseClient(Protocol):
    """Anything that can turn a request body into a Converse response.

    `body` is what `beval.request.build_converse_body` produced. The return value is the
    response as the service sends it — camelCase and all — because the whole value of the
    fake client is that it lies about nothing except where the answer came from.
    """

    def invoke(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]: ...


class ScriptedClient:
    """A fake client that answers from a script and remembers what it was asked.

    Not a mock of the harness's own types: it takes and returns the same shapes the real
    SDK does, so a test that passes here is a test about the Converse contract rather than
    about the fake. `calls` is what lets a test assert the request that went out.
    """

    def __init__(self, responses: dict[str, dict[str, Any]] | list[dict[str, Any]]) -> None:
        self._responses = responses
        self._index = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def invoke(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((model_id, body))
        if isinstance(self._responses, dict):
            key = self._request_key(body)
            if key not in self._responses:
                raise KeyError(f"no scripted response for user turn {key!r}")
            return self._responses[key]
        if self._index >= len(self._responses):
            raise IndexError("scripted client ran out of responses")
        response = self._responses[self._index]
        self._index += 1
        return response

    @staticmethod
    def _request_key(body: dict[str, Any]) -> str:
        return body["messages"][0]["content"][0]["text"]


def make_converse_response(
    text: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float | None = None,
    stop_reason: str = "end_turn",
) -> dict[str, Any]:
    """Build a response in the shape the service returns, for scripting a fake client.

    Kept beside the reader rather than in the tests so both sides of the contract move
    together: change the field names here and `read_response` stops working, loudly.
    """
    response: dict[str, Any] = {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "usage": {
            USAGE_INPUT: input_tokens,
            USAGE_OUTPUT: output_tokens,
            "totalTokens": input_tokens + output_tokens,
        },
        "stopReason": stop_reason,
    }
    if latency_ms is not None:
        response["metrics"] = {METRICS_LATENCY: latency_ms}
    return response


def read_response(case_id: str, raw: Any) -> Response:
    """Turn a Converse response into the run-file record for one case.

    Raises `ResponseShapeError` rather than filling in a default: every field read here is
    either a number that ends up in a cost report or the text a score is computed from.
    """
    if not isinstance(raw, dict):
        raise ResponseShapeError(f"{case_id}: response must be an object, got {type(raw).__name__}")

    return Response(
        case_id=case_id,
        output_text=_read_output_text(case_id, raw),
        input_tokens=_read_count(case_id, raw, USAGE_INPUT),
        output_tokens=_read_count(case_id, raw, USAGE_OUTPUT),
        latency_ms=_read_latency(case_id, raw),
        stop_reason=_read_stop_reason(case_id, raw),
    )


def _read_output_text(case_id: str, raw: dict[str, Any]) -> str:
    content = _dig(case_id, raw, "output", "message", "content")
    if not isinstance(content, list) or not content:
        raise ResponseShapeError(f"{case_id}: 'output.message.content' must be a non-empty list")
    # Content can hold blocks that are not text at all (tool use, images). Concatenating
    # only the text blocks is the honest read: a response that carried no text becomes an
    # empty output, which scores as a failure rather than as a missing case.
    parts = [block["text"] for block in content if isinstance(block, dict) and "text" in block]
    if not all(isinstance(part, str) for part in parts):
        raise ResponseShapeError(f"{case_id}: 'output.message.content[].text' must be strings")
    return "".join(parts)


def _read_count(case_id: str, raw: dict[str, Any], field: str) -> int:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        raise ResponseShapeError(f"{case_id}: 'usage' is missing; token counts cannot default to 0")
    if field not in usage:
        raise ResponseShapeError(f"{case_id}: 'usage.{field}' is missing")
    value = usage[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ResponseShapeError(f"{case_id}: 'usage.{field}' must be a non-negative integer")
    return value


def _read_latency(case_id: str, raw: dict[str, Any]) -> float | None:
    metrics = raw.get("metrics")
    if metrics is None:
        # Absent latency is allowed and is not zero: the ledger reports percentiles over
        # the responses that carried one, and says how many that was.
        return None
    if not isinstance(metrics, dict):
        raise ResponseShapeError(f"{case_id}: 'metrics' must be an object")
    if METRICS_LATENCY not in metrics:
        return None
    value = metrics[METRICS_LATENCY]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ResponseShapeError(f"{case_id}: 'metrics.{METRICS_LATENCY}' must be a non-negative number")
    return value


def _read_stop_reason(case_id: str, raw: dict[str, Any]) -> str | None:
    value = raw.get("stopReason")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ResponseShapeError(f"{case_id}: 'stopReason' must be a non-empty string")
    return value


def _dig(case_id: str, raw: dict[str, Any], *path: str) -> Any:
    node: Any = raw
    walked: list[str] = []
    for key in path:
        walked.append(key)
        if not isinstance(node, dict) or key not in node:
            raise ResponseShapeError(f"{case_id}: '{'.'.join(walked)}' is missing")
        node = node[key]
    return node
