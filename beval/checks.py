"""Check definitions.

A check is the machine-decidable part of a case: given the model output as text, it
either passes or fails, with no model in the loop. This module owns the schema of every
check type. Evaluation lives elsewhere so that a suite can be validated without running
anything.
"""

from __future__ import annotations

import re
from typing import Any

# type -> (required fields, optional fields)
CHECK_SCHEMA: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "contains_all": (("values",), ("ignore_case",)),
    "contains_any": (("values",), ("ignore_case",)),
    "not_contains": (("values",), ("ignore_case",)),
    "regex": (("pattern",), ("ignore_case",)),
    "max_words": (("value",), ()),
    "json_field_equals": (("path", "value"), ()),
}

# Accepted on every check regardless of type.
COMMON_FIELDS = ("type", "label")

CHECK_TYPES = tuple(sorted(CHECK_SCHEMA))


def validate_check(check: Any, where: str) -> list[str]:
    """Return a list of problems with one check. Empty list means valid."""
    if not isinstance(check, dict):
        return [f"{where}: check must be an object, got {_kind(check)}"]

    ctype = check.get("type")
    if not isinstance(ctype, str) or ctype not in CHECK_SCHEMA:
        return [
            f"{where}: unknown check type {ctype!r}; "
            f"known types are {', '.join(CHECK_TYPES)}"
        ]

    required, optional = CHECK_SCHEMA[ctype]
    allowed = set(required) | set(optional) | set(COMMON_FIELDS)
    problems: list[str] = []

    for field in required:
        if field not in check:
            problems.append(f"{where}: check {ctype!r} is missing required field {field!r}")

    for key in check:
        if key not in allowed:
            problems.append(
                f"{where}: check {ctype!r} has unknown field {key!r}; "
                f"allowed fields are {', '.join(sorted(allowed))}"
            )

    if "label" in check and not _is_nonempty_str(check["label"]):
        problems.append(f"{where}: 'label' must be a non-empty string")

    if "ignore_case" in check and not isinstance(check["ignore_case"], bool):
        problems.append(f"{where}: 'ignore_case' must be true or false")

    if "values" in check:
        values = check["values"]
        if not isinstance(values, list) or not values:
            problems.append(f"{where}: 'values' must be a non-empty list of strings")
        elif not all(_is_nonempty_str(v) for v in values):
            problems.append(f"{where}: every entry of 'values' must be a non-empty string")

    if ctype == "regex" and "pattern" in check:
        pattern = check["pattern"]
        if not _is_nonempty_str(pattern):
            problems.append(f"{where}: 'pattern' must be a non-empty string")
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                problems.append(f"{where}: 'pattern' does not compile: {exc}")

    if ctype == "max_words" and "value" in check:
        value = check["value"]
        if not _is_positive_int(value):
            problems.append(f"{where}: 'value' must be a positive integer")

    if ctype == "json_field_equals" and "path" in check:
        path = check["path"]
        if not _is_nonempty_str(path) or not all(part for part in path.split(".")):
            problems.append(
                f"{where}: 'path' must be a dot path such as 'queue' or 'result.queue'"
            )

    return problems


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_positive_int(value: Any) -> bool:
    # bool is a subclass of int; True would otherwise sail through as 1.
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _kind(value: Any) -> str:
    return type(value).__name__
