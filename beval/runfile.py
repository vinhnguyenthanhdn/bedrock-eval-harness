"""The run file: what a runner produced for one suite against one model.

Today these files are written by hand as fixtures; the Bedrock runner will write the same
shape. Scoring reads nothing else, which is why a score can be recomputed offline.

The loader is as strict as the suite loader, for the same reason: a token count that is
silently missing turns into a cost of zero, and a cost of zero is worse than an error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1

RUN_FIELDS_REQUIRED = ("format_version", "suite_id", "run_id", "model_id", "source", "responses")
RUN_FIELDS_OPTIONAL = ("recorded_at", "region", "note")
RESPONSE_FIELDS_REQUIRED = ("case_id", "output_text", "input_tokens", "output_tokens")
RESPONSE_FIELDS_OPTIONAL = ("latency_ms", "stop_reason")

# `fixture` output was written by a human to exercise the scorer; `bedrock` output came
# back from a real call. Reports print the difference so nobody quotes a fixture as a
# measurement.
SOURCES = ("fixture", "bedrock")


@dataclass(frozen=True)
class Response:
    case_id: str
    output_text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float | None = None
    stop_reason: str | None = None


@dataclass(frozen=True)
class Run:
    suite_id: str
    run_id: str
    model_id: str
    source: str
    responses: tuple[Response, ...]
    recorded_at: str | None = None
    region: str | None = None
    note: str | None = None

    @property
    def is_measurement(self) -> bool:
        return self.source == "bedrock"

    def by_case(self) -> dict[str, Response]:
        return {response.case_id: response for response in self.responses}


def load_run(path: str | Path) -> tuple[Run | None, list[str]]:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"{path}: cannot read file: {exc}"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"{path}: not valid JSON: line {exc.lineno} column {exc.colno}: {exc.msg}"]
    return parse_run(data, source=str(path))


def parse_run(data: Any, source: str = "<run>") -> tuple[Run | None, list[str]]:
    if not isinstance(data, dict):
        return None, [f"{source}: top level must be an object, got {type(data).__name__}"]

    if data.get("format_version") != FORMAT_VERSION:
        return None, [
            f"{source}: format_version must be {FORMAT_VERSION}, got {data.get('format_version')!r}"
        ]

    problems: list[str] = []
    for key in data:
        if key not in RUN_FIELDS_REQUIRED + RUN_FIELDS_OPTIONAL:
            problems.append(f"{source}: unknown field {key!r}")
    for name in RUN_FIELDS_REQUIRED:
        if name not in data:
            problems.append(f"{source}: missing required field {name!r}")

    run_source = data.get("source")
    if "source" in data and run_source not in SOURCES:
        problems.append(f"{source}: 'source' must be one of {', '.join(SOURCES)}")

    responses: list[Response] = []
    raw_responses = data.get("responses")
    if "responses" in data:
        if not isinstance(raw_responses, list) or not raw_responses:
            problems.append(f"{source}: 'responses' must be a non-empty list")
        else:
            seen: set[str] = set()
            for index, raw in enumerate(raw_responses):
                response, response_problems = _parse_response(raw, source, index)
                problems += response_problems
                if response is None:
                    continue
                if response.case_id in seen:
                    problems.append(f"{source}: duplicate response for case {response.case_id!r}")
                seen.add(response.case_id)
                responses.append(response)

    if problems:
        return None, problems

    return (
        Run(
            suite_id=str(data["suite_id"]),
            run_id=str(data["run_id"]),
            model_id=str(data["model_id"]),
            source=str(run_source),
            responses=tuple(responses),
            recorded_at=data.get("recorded_at"),
            region=data.get("region"),
            note=data.get("note"),
        ),
        [],
    )


def _parse_response(raw: Any, source: str, index: int) -> tuple[Response | None, list[str]]:
    where = f"{source}: responses[{index}]"
    if not isinstance(raw, dict):
        return None, [f"{where}: must be an object, got {type(raw).__name__}"]

    problems = [
        f"{where}: unknown field {key!r}"
        for key in raw
        if key not in RESPONSE_FIELDS_REQUIRED + RESPONSE_FIELDS_OPTIONAL
    ]
    for name in RESPONSE_FIELDS_REQUIRED:
        if name not in raw:
            problems.append(f"{where}: missing required field {name!r}")

    if "case_id" in raw and not (isinstance(raw["case_id"], str) and raw["case_id"].strip()):
        problems.append(f"{where}: 'case_id' must be a non-empty string")
    if "output_text" in raw and not isinstance(raw["output_text"], str):
        problems.append(f"{where}: 'output_text' must be a string")
    for name in ("input_tokens", "output_tokens"):
        if name in raw and not _is_count(raw[name]):
            problems.append(f"{where}: '{name}' must be a non-negative integer")
    if "latency_ms" in raw:
        value = raw["latency_ms"]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            problems.append(f"{where}: 'latency_ms' must be a non-negative number")

    if problems:
        return None, problems

    return (
        Response(
            case_id=raw["case_id"],
            output_text=raw["output_text"],
            input_tokens=raw["input_tokens"],
            output_tokens=raw["output_tokens"],
            latency_ms=raw.get("latency_ms"),
            stop_reason=raw.get("stop_reason"),
        ),
        [],
    )


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
