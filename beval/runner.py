"""Ask every case in a suite, and keep what came back.

Two files come out of one run, and the split is the point:

- the **run file** (`beval.runfile`) — what the scorer reads: output text and token counts.
- the **record** — the raw request and the raw response for every case, exactly as they
  crossed the SDK boundary.

The record is what makes a run repeatable without an account. Replaying it re-runs the
whole path — building the request, reading the response, scoring — against answers that
were really returned once. And replay refuses to serve an answer when the request no
longer matches the one that produced it: a recording is only evidence while the question
is still the same question.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import ConverseClient, read_response
from .request import build_converse_body
from .runfile import FORMAT_VERSION as RUN_FORMAT_VERSION
from .runfile import SOURCES, Response, Run
from .suite import Suite

RECORD_FORMAT_VERSION = 1


class RunAborted(Exception):
    """No case produced a usable response, so there is nothing worth writing."""


class RecordMismatch(Exception):
    """A replayed request is not the request that produced the recorded answer."""


@dataclass(frozen=True)
class Exchange:
    case_id: str
    request: dict[str, Any]
    response: dict[str, Any]


@dataclass(frozen=True)
class Record:
    suite_id: str
    model_id: str
    exchanges: tuple[Exchange, ...]
    # What produced these answers: `bedrock` if a model really returned them, `fixture` if
    # a person wrote them. A replay inherits it, because replaying a real call does not
    # make the numbers less real, and replaying hand-written answers does not make them a
    # measurement. A record from before this field existed reads as `fixture`: it cannot
    # prove it was a measurement, and the safe default is the one that claims less.
    source: str = "fixture"
    region: str | None = None
    recorded_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "format_version": RECORD_FORMAT_VERSION,
            "suite_id": self.suite_id,
            "model_id": self.model_id,
            "source": self.source,
            "exchanges": [
                {"case_id": e.case_id, "request": e.request, "response": e.response}
                for e in self.exchanges
            ],
        }
        if self.region is not None:
            data["region"] = self.region
        if self.recorded_at is not None:
            data["recorded_at"] = self.recorded_at
        return data


@dataclass(frozen=True)
class RunOutcome:
    run: Run
    record: Record
    failures: tuple[tuple[str, str], ...] = ()  # (case_id, why it produced nothing)


def run_suite(
    suite: Suite,
    model_id: str,
    client: ConverseClient,
    run_id: str,
    source: str = "bedrock",
    region: str | None = None,
    recorded_at: str | None = None,
) -> RunOutcome:
    """Ask every case once and collect the answers.

    A case whose call or response fails is left out of the run file rather than filled in.
    The scorer already treats a case with no response as a failure, so an incomplete run
    cannot pass as a good run on a smaller sample — but only if nothing invents a blank
    answer for it here.
    """
    responses: list[Response] = []
    exchanges: list[Exchange] = []
    failures: list[tuple[str, str]] = []

    for case in suite.cases:
        body = build_converse_body(suite, case)
        try:
            raw = client.invoke(model_id, body)
        except Exception as exc:  # noqa: BLE001 - the SDK raises its own hierarchy
            failures.append((case.id, f"{type(exc).__name__}: {exc}"))
            continue
        try:
            responses.append(read_response(case.id, raw))
        except Exception as exc:  # noqa: BLE001
            failures.append((case.id, f"{type(exc).__name__}: {exc}"))
            continue
        exchanges.append(Exchange(case_id=case.id, request=body, response=raw))

    if not responses:
        raise RunAborted(
            f"every case failed ({len(failures)} of {len(suite.cases)}); no run file written"
        )

    run = Run(
        suite_id=suite.suite_id,
        run_id=run_id,
        model_id=model_id,
        source=source,
        responses=tuple(responses),
        recorded_at=recorded_at,
        region=region,
    )
    record = Record(
        suite_id=suite.suite_id,
        model_id=model_id,
        exchanges=tuple(exchanges),
        source=source,
        region=region,
        recorded_at=recorded_at,
    )
    return RunOutcome(run=run, record=record, failures=tuple(failures))


def run_to_json(run: Run) -> dict[str, Any]:
    """The run file, in the shape `beval.runfile` reads back."""
    data: dict[str, Any] = {
        "format_version": RUN_FORMAT_VERSION,
        "suite_id": run.suite_id,
        "run_id": run.run_id,
        "model_id": run.model_id,
        "source": run.source,
        "responses": [],
    }
    for name in ("recorded_at", "region", "note"):
        value = getattr(run, name)
        if value is not None:
            data[name] = value
    for response in run.responses:
        entry: dict[str, Any] = {
            "case_id": response.case_id,
            "output_text": response.output_text,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        if response.latency_ms is not None:
            entry["latency_ms"] = response.latency_ms
        if response.stop_reason is not None:
            entry["stop_reason"] = response.stop_reason
        data["responses"].append(entry)
    return data


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_record(path: str | Path) -> tuple[Record | None, list[str]]:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"{path}: cannot read file: {exc}"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"{path}: not valid JSON: line {exc.lineno} column {exc.colno}: {exc.msg}"]
    return parse_record(data, source=str(path))


def parse_record(data: Any, source: str = "<record>") -> tuple[Record | None, list[str]]:
    if not isinstance(data, dict):
        return None, [f"{source}: top level must be an object, got {type(data).__name__}"]
    if data.get("format_version") != RECORD_FORMAT_VERSION:
        return None, [
            f"{source}: format_version must be {RECORD_FORMAT_VERSION},"
            f" got {data.get('format_version')!r}"
        ]

    problems: list[str] = []
    for name in ("suite_id", "model_id", "exchanges"):
        if name not in data:
            problems.append(f"{source}: missing required field {name!r}")
    if "source" in data and data["source"] not in SOURCES:
        problems.append(f"{source}: 'source' must be one of {', '.join(SOURCES)}")

    exchanges: list[Exchange] = []
    raw_exchanges = data.get("exchanges")
    if "exchanges" in data:
        if not isinstance(raw_exchanges, list) or not raw_exchanges:
            problems.append(f"{source}: 'exchanges' must be a non-empty list")
        else:
            for index, raw in enumerate(raw_exchanges):
                where = f"{source}: exchanges[{index}]"
                if not isinstance(raw, dict):
                    problems.append(f"{where}: must be an object")
                    continue
                missing = [k for k in ("case_id", "request", "response") if k not in raw]
                if missing:
                    problems.append(f"{where}: missing {', '.join(missing)}")
                    continue
                exchanges.append(
                    Exchange(
                        case_id=str(raw["case_id"]),
                        request=raw["request"],
                        response=raw["response"],
                    )
                )

    if problems:
        return None, problems

    return (
        Record(
            suite_id=str(data["suite_id"]),
            model_id=str(data["model_id"]),
            exchanges=tuple(exchanges),
            source=str(data.get("source", "fixture")),
            region=data.get("region"),
            recorded_at=data.get("recorded_at"),
        ),
        [],
    )


class RecordedClient:
    """Replays a record, and only for the request that produced it.

    Matching on the request rather than on the case id is the whole guarantee. Change a
    system prompt or a temperature and the replay stops, instead of quietly reporting the
    old answer as the answer to a new question.
    """

    def __init__(self, record: Record) -> None:
        self._by_case = {exchange.case_id: exchange for exchange in record.exchanges}
        self._record = record
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def invoke(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((model_id, body))
        exchange = self._find(body)
        if exchange.request != body:
            # No case id in the message: every caller that catches this already has the
            # case in hand and prefixes it, and printing it twice reads like a bug.
            raise RecordMismatch(
                "the recorded request differs from the one being replayed;"
                " re-record the run or restore the suite settings it was recorded with"
            )
        return exchange.response

    def _find(self, body: dict[str, Any]) -> Exchange:
        user_turn = _user_turn(body)
        for exchange in self._record.exchanges:
            if _user_turn(exchange.request) == user_turn:
                return exchange
        raise RecordMismatch(f"no recorded exchange for user turn {user_turn!r}")


def _user_turn(body: dict[str, Any]) -> str | None:
    try:
        return body["messages"][0]["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None
