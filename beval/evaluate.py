"""Decide whether one model output satisfies the checks of one case.

Everything here is pure: text in, verdict out. That is what makes a score reproducible
months after the call that produced the text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .suite import Case


@dataclass(frozen=True)
class CheckResult:
    type: str
    passed: bool
    detail: str
    label: str | None = None

    @property
    def name(self) -> str:
        return self.label or self.type


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    weight: float
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        # A case passes only when every check passes; partial credit inside a case
        # would need per-check weights the format deliberately does not have.
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.passed)


def evaluate_case(case: Case, output_text: str) -> CaseResult:
    results = tuple(evaluate_check(check, output_text) for check in case.checks)
    return CaseResult(case_id=case.id, weight=case.weight, checks=results)


def evaluate_check(check: dict[str, Any], output_text: str) -> CheckResult:
    ctype = str(check["type"])
    label = check.get("label")
    ignore_case = bool(check.get("ignore_case", False))
    haystack = output_text.lower() if ignore_case else output_text

    def prepare(values):
        return [v.lower() if ignore_case else v for v in values]

    if ctype == "contains_all":
        missing = [v for v in prepare(check["values"]) if v not in haystack]
        return CheckResult(
            ctype,
            not missing,
            "all present" if not missing else f"missing {_join(missing)}",
            label,
        )

    if ctype == "contains_any":
        wanted = prepare(check["values"])
        found = [v for v in wanted if v in haystack]
        return CheckResult(
            ctype,
            bool(found),
            f"found {_join(found)}" if found else f"none of {_join(wanted)} present",
            label,
        )

    if ctype == "not_contains":
        present = [v for v in prepare(check["values"]) if v in haystack]
        return CheckResult(
            ctype,
            not present,
            "none present" if not present else f"found forbidden {_join(present)}",
            label,
        )

    if ctype == "regex":
        flags = re.IGNORECASE if ignore_case else 0
        match = re.search(str(check["pattern"]), output_text, flags)
        return CheckResult(
            ctype,
            match is not None,
            f"matched {_clip(match.group(0))}" if match else "no match",
            label,
        )

    if ctype == "max_words":
        limit = int(check["value"])
        count = len(output_text.split())
        return CheckResult(ctype, count <= limit, f"{count} words, limit {limit}", label)

    if ctype == "json_field_equals":
        return _json_field_equals(check, output_text, label)

    # Unreachable through a validated suite; kept so a new check type cannot be scored
    # as a silent pass while its evaluation is still unwritten.
    raise ValueError(f"no evaluator for check type {ctype!r}")


def _json_field_equals(check: dict[str, Any], output_text: str, label) -> CheckResult:
    ctype = "json_field_equals"
    path = str(check["path"])
    expected = check["value"]
    try:
        data = json.loads(output_text)
    except json.JSONDecodeError as exc:
        return CheckResult(ctype, False, f"output is not JSON: {exc.msg}", label)

    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return CheckResult(ctype, False, f"no field {path!r} in output", label)
        node = node[part]

    ok = node == expected
    return CheckResult(
        ctype, ok, f"{path}={node!r}" + ("" if ok else f", expected {expected!r}"), label
    )


def _join(values) -> str:
    return ", ".join(repr(v) for v in values)


def _clip(text: str, limit: int = 40) -> str:
    text = text.replace("\n", " ")
    return repr(text if len(text) <= limit else text[: limit - 1] + "…")
