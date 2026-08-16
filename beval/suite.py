"""Load and validate a case suite.

Loading never raises on a bad file: it returns the problems it found, all of them, so a
contributor fixing a suite sees the whole list in one pass instead of one error per run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checks import validate_check

FORMAT_VERSION = 1
ID_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")

SUITE_FIELDS_REQUIRED = ("format_version", "suite_id", "description", "cases")
SUITE_FIELDS_OPTIONAL = ("defaults",)
CASE_FIELDS_REQUIRED = ("id", "input", "checks")
CASE_FIELDS_OPTIONAL = ("weight", "tags", "max_output_tokens", "temperature")
INPUT_FIELDS_REQUIRED = ("user",)
INPUT_FIELDS_OPTIONAL = ("system",)
REQUEST_SETTINGS = ("max_output_tokens", "temperature", "system")


@dataclass(frozen=True)
class Case:
    id: str
    user: str
    checks: tuple[dict[str, Any], ...]
    system: str | None = None
    weight: float = 1.0
    tags: tuple[str, ...] = ()
    max_output_tokens: int | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class Suite:
    suite_id: str
    description: str
    cases: tuple[Case, ...]
    path: Path | None = None
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def total_weight(self) -> float:
        return sum(case.weight for case in self.cases)


class SuiteError(Exception):
    """Raised by load_suite_or_raise when a suite cannot be used."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def load_suite(path: str | Path) -> tuple[Suite | None, list[str]]:
    """Read a suite file. Returns (suite, problems); suite is None when problems exist."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"{path}: cannot read file: {exc}"]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"{path}: not valid JSON: line {exc.lineno} column {exc.colno}: {exc.msg}"]

    return parse_suite(data, source=str(path))


def load_suite_or_raise(path: str | Path) -> Suite:
    suite, problems = load_suite(path)
    if suite is None:
        raise SuiteError(problems)
    return suite


def parse_suite(data: Any, source: str = "<suite>") -> tuple[Suite | None, list[str]]:
    problems: list[str] = []

    if not isinstance(data, dict):
        return None, [f"{source}: top level must be an object, got {type(data).__name__}"]

    version = data.get("format_version")
    if version != FORMAT_VERSION:
        # Refuse rather than guess: a reader that does not know the version cannot know
        # which fields changed meaning.
        return None, [
            f"{source}: format_version must be {FORMAT_VERSION}, got {version!r}"
        ]

    problems += _unknown_keys(data, SUITE_FIELDS_REQUIRED + SUITE_FIELDS_OPTIONAL, source)
    for name in SUITE_FIELDS_REQUIRED:
        if name not in data:
            problems.append(f"{source}: missing required field {name!r}")

    suite_id = data.get("suite_id")
    if "suite_id" in data and not _is_id(suite_id):
        problems.append(
            f"{source}: 'suite_id' must match [a-z0-9-]{{1,64}}, got {suite_id!r}"
        )

    description = data.get("description")
    if "description" in data and not _is_nonempty_str(description):
        problems.append(f"{source}: 'description' must be a non-empty string")

    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        problems.append(f"{source}: 'defaults' must be an object")
        defaults = {}
    else:
        problems += _unknown_keys(defaults, REQUEST_SETTINGS, f"{source}: defaults")
        problems += _request_settings(defaults, f"{source}: defaults")

    raw_cases = data.get("cases")
    cases: list[Case] = []
    if "cases" in data:
        if not isinstance(raw_cases, list) or not raw_cases:
            problems.append(f"{source}: 'cases' must be a non-empty list")
        else:
            seen: set[str] = set()
            for index, raw_case in enumerate(raw_cases):
                case, case_problems = _parse_case(raw_case, source, index)
                problems += case_problems
                if case is None:
                    continue
                if case.id in seen:
                    problems.append(f"{source}: duplicate case id {case.id!r}")
                seen.add(case.id)
                cases.append(case)

    if problems:
        return None, problems

    return (
        Suite(
            suite_id=str(suite_id),
            description=str(description),
            cases=tuple(cases),
            path=Path(source) if source != "<suite>" else None,
            defaults=defaults,
        ),
        [],
    )


def _parse_case(raw: Any, source: str, index: int) -> tuple[Case | None, list[str]]:
    where = f"{source}: cases[{index}]"
    if not isinstance(raw, dict):
        return None, [f"{where}: case must be an object, got {type(raw).__name__}"]

    problems = _unknown_keys(raw, CASE_FIELDS_REQUIRED + CASE_FIELDS_OPTIONAL, where)
    for name in CASE_FIELDS_REQUIRED:
        if name not in raw:
            problems.append(f"{where}: missing required field {name!r}")

    case_id = raw.get("id")
    if "id" in raw and not _is_id(case_id):
        problems.append(f"{where}: 'id' must match [a-z0-9-]{{1,64}}, got {case_id!r}")
    else:
        where = f"{source}: case {case_id!r}" if _is_id(case_id) else where

    user = system = None
    raw_input = raw.get("input")
    if "input" in raw:
        if not isinstance(raw_input, dict):
            problems.append(f"{where}: 'input' must be an object")
        else:
            problems += _unknown_keys(
                raw_input, INPUT_FIELDS_REQUIRED + INPUT_FIELDS_OPTIONAL, f"{where}: input"
            )
            user = raw_input.get("user")
            if not _is_nonempty_str(user):
                problems.append(f"{where}: 'input.user' must be a non-empty string")
            if "system" in raw_input:
                system = raw_input["system"]
                if not _is_nonempty_str(system):
                    problems.append(f"{where}: 'input.system' must be a non-empty string")

    raw_checks = raw.get("checks")
    checks: list[dict[str, Any]] = []
    if "checks" in raw:
        if not isinstance(raw_checks, list) or not raw_checks:
            # A case with no check cannot fail, so it would add weight to the score
            # while measuring nothing.
            problems.append(f"{where}: 'checks' must be a non-empty list")
        else:
            for check_index, raw_check in enumerate(raw_checks):
                check_problems = validate_check(raw_check, f"{where}: checks[{check_index}]")
                problems += check_problems
                if not check_problems:
                    checks.append(raw_check)

    weight = raw.get("weight", 1)
    if "weight" in raw and not _is_positive_number(weight):
        problems.append(f"{where}: 'weight' must be a positive number")
        weight = 1

    tags = raw.get("tags", [])
    if "tags" in raw:
        if not isinstance(tags, list) or not all(_is_nonempty_str(t) for t in tags):
            problems.append(f"{where}: 'tags' must be a list of non-empty strings")
            tags = []

    problems += _request_settings(raw, where)

    if problems:
        return None, problems

    return (
        Case(
            id=str(case_id),
            user=str(user),
            checks=tuple(checks),
            system=system,
            weight=float(weight),
            tags=tuple(tags),
            max_output_tokens=raw.get("max_output_tokens"),
            temperature=raw.get("temperature"),
        ),
        [],
    )


def _request_settings(holder: dict[str, Any], where: str) -> list[str]:
    problems: list[str] = []
    if "max_output_tokens" in holder and not _is_positive_int(holder["max_output_tokens"]):
        problems.append(f"{where}: 'max_output_tokens' must be a positive integer")
    if "temperature" in holder:
        temperature = holder["temperature"]
        if not _is_number(temperature) or not 0 <= temperature <= 1:
            problems.append(f"{where}: 'temperature' must be a number between 0 and 1")
    if "system" in holder and where.endswith("defaults") and not _is_nonempty_str(holder["system"]):
        problems.append(f"{where}: 'system' must be a non-empty string")
    return problems


def _unknown_keys(holder: dict[str, Any], allowed: tuple[str, ...], where: str) -> list[str]:
    return [
        f"{where}: unknown field {key!r}; allowed fields are {', '.join(sorted(allowed))}"
        for key in holder
        if key not in allowed
    ]


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and bool(ID_PATTERN.match(value))


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive_number(value: Any) -> bool:
    return _is_number(value) and value > 0


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
