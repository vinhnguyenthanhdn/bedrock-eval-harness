"""Score a run, and price it if a price list was supplied.

Two rules shape this module:

- **No price is built in.** Prices change, and a stale constant in a repository turns into
  a wrong cost report that nobody notices. The caller passes a price list that carries the
  date and the URL it was read from; without one, the report shows tokens and no money.
- **Latency is reported only for the cases that carried it.** A missing latency is not zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .evaluate import CaseResult, evaluate_case
from .runfile import Run
from .suite import Suite

PRICE_FIELDS_REQUIRED = ("format_version", "source_url", "read_on", "models")
PRICE_MODEL_FIELDS = ("input_per_1k_usd", "output_per_1k_usd")
PLACEHOLDER = "REPLACE_ME"


@dataclass(frozen=True)
class PriceList:
    source_url: str
    read_on: str
    models: dict[str, dict[str, float]]

    def for_model(self, model_id: str) -> dict[str, float] | None:
        return self.models.get(model_id)


@dataclass(frozen=True)
class Scored:
    suite: Suite
    run: Run
    results: tuple[CaseResult, ...]
    missing_case_ids: tuple[str, ...]
    extra_case_ids: tuple[str, ...]

    @property
    def earned_weight(self) -> float:
        return sum(result.weight for result in self.results if result.passed)

    @property
    def possible_weight(self) -> float:
        # Every case in the suite counts, including ones the run has no response for:
        # a case that was never answered is a case that did not pass.
        return self.suite.total_weight

    @property
    def score(self) -> float:
        return 0.0 if not self.possible_weight else self.earned_weight / self.possible_weight

    @property
    def input_tokens(self) -> int:
        return sum(response.input_tokens for response in self.run.responses)

    @property
    def output_tokens(self) -> int:
        return sum(response.output_tokens for response in self.run.responses)

    @property
    def latencies(self) -> tuple[float, ...]:
        return tuple(
            response.latency_ms
            for response in self.run.responses
            if response.latency_ms is not None
        )

    def cost_usd(self, prices: PriceList | None) -> float | None:
        if prices is None:
            return None
        rates = prices.for_model(self.run.model_id)
        if rates is None:
            return None
        return (
            self.input_tokens / 1000 * rates["input_per_1k_usd"]
            + self.output_tokens / 1000 * rates["output_per_1k_usd"]
        )


def score_run(suite: Suite, run: Run) -> Scored:
    responses = run.by_case()
    results = []
    missing = []
    for case in suite.cases:
        response = responses.get(case.id)
        if response is None:
            missing.append(case.id)
            continue
        results.append(evaluate_case(case, response.output_text))
    extra = [case_id for case_id in responses if case_id not in {c.id for c in suite.cases}]
    return Scored(
        suite=suite,
        run=run,
        results=tuple(results),
        missing_case_ids=tuple(missing),
        extra_case_ids=tuple(sorted(extra)),
    )


def percentile(values, fraction: float) -> float | None:
    """Nearest-rank percentile. Returns None for an empty sample rather than 0."""
    ordered = sorted(values)
    if not ordered:
        return None
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    rank = max(1, min(len(ordered), _ceil(fraction * len(ordered))))
    return ordered[rank - 1]


def _ceil(value: float) -> int:
    whole = int(value)
    return whole if value == whole else whole + 1


def load_prices(path: str | Path) -> tuple[PriceList | None, list[str]]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, [f"{path}: cannot read file: {exc}"]
    except json.JSONDecodeError as exc:
        return None, [f"{path}: not valid JSON: line {exc.lineno}: {exc.msg}"]
    return parse_prices(data, source=str(path))


def parse_prices(data: Any, source: str = "<prices>") -> tuple[PriceList | None, list[str]]:
    if not isinstance(data, dict):
        return None, [f"{source}: top level must be an object"]
    if data.get("format_version") != 1:
        return None, [f"{source}: format_version must be 1, got {data.get('format_version')!r}"]

    problems = [
        f"{source}: missing required field {name!r}"
        for name in PRICE_FIELDS_REQUIRED
        if name not in data
    ]

    # The shipped example carries this marker. Refusing it means nobody can produce a cost
    # report from placeholder rates by forgetting to edit one line.
    for name in ("source_url", "read_on"):
        if PLACEHOLDER in str(data.get(name, "")):
            problems.append(
                f"{source}: {name!r} still holds the {PLACEHOLDER} placeholder — "
                "put the page you read the prices from and the date you read it"
            )

    models: dict[str, dict[str, float]] = {}
    raw_models = data.get("models")
    if "models" in data:
        if not isinstance(raw_models, dict) or not raw_models:
            problems.append(f"{source}: 'models' must be a non-empty object")
        else:
            for model_id, rates in raw_models.items():
                where = f"{source}: models[{model_id!r}]"
                if not isinstance(rates, dict):
                    problems.append(f"{where}: must be an object")
                    continue
                clean: dict[str, float] = {}
                for field in PRICE_MODEL_FIELDS:
                    value = rates.get(field)
                    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                        problems.append(f"{where}: {field!r} must be a non-negative number")
                    else:
                        clean[field] = float(value)
                if len(clean) == len(PRICE_MODEL_FIELDS):
                    models[model_id] = clean

    if problems:
        return None, problems

    return (
        PriceList(
            source_url=str(data["source_url"]),
            read_on=str(data["read_on"]),
            models=models,
        ),
        [],
    )
