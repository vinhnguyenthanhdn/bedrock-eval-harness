"""Command line entry point.

Two commands today, both offline:

    beval validate <suite.json> [...]   check a suite against the format
    beval show <suite.json>            print what the suite measures
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

from .suite import Suite, load_suite

PROGRAM = "beval"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Evaluation harness for Bedrock models. Offline commands only so far.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="check one or more suite files")
    p_validate.add_argument("paths", nargs="+", type=Path)

    p_show = sub.add_parser("show", help="print what a suite measures")
    p_show.add_argument("path", type=Path)

    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args.paths)
    if args.command == "show":
        return _cmd_show(args.path)
    return 2


def _cmd_validate(paths: Sequence[Path]) -> int:
    failed = 0
    for path in paths:
        suite, problems = load_suite(path)
        if suite is None:
            failed += 1
            print(f"FAIL {path}  ({len(problems)} problem(s))")
            for problem in problems:
                print(f"  - {problem}")
            continue
        checks = sum(len(case.checks) for case in suite.cases)
        print(
            f"OK   {path}  suite={suite.suite_id} "
            f"cases={len(suite.cases)} checks={checks} weight={_num(suite.total_weight)}"
        )
    if failed:
        print(f"\n{failed} of {len(paths)} suite(s) invalid", file=sys.stderr)
        return 1
    return 0


def _cmd_show(path: Path) -> int:
    suite, problems = load_suite(path)
    if suite is None:
        print(f"FAIL {path}  ({len(problems)} problem(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"suite    {suite.suite_id}")
    print(f"about    {suite.description}")
    print(f"cases    {len(suite.cases)}  (total weight {_num(suite.total_weight)})")
    if suite.defaults:
        for key in sorted(suite.defaults):
            print(f"default  {key} = {_short(suite.defaults[key])}")
    else:
        print("default  (none)")
    print()

    width = max([len("case")] + [len(case.id) for case in suite.cases])
    rule = "-" * (width + 40)
    print(f"{'case':<{width}} {'weight':>6}  {'checks':>6}  types")
    print(rule)
    for case in suite.cases:
        types = ", ".join(sorted({str(check['type']) for check in case.checks}))
        print(f"{case.id:<{width}} {_num(case.weight):>6}  {len(case.checks):>6}  {types}")
    print(rule)
    _print_tally("check types", _tally_checks(suite))
    _print_tally("tags", Counter(tag for case in suite.cases for tag in case.tags))
    return 0


def _tally_checks(suite: Suite) -> Counter:
    return Counter(str(check["type"]) for case in suite.cases for check in case.checks)


def _print_tally(title: str, tally: Counter) -> None:
    if not tally:
        return
    body = "  ".join(f"{name}={count}" for name, count in sorted(tally.items()))
    print(f"{title:<12} {body}")


def _short(value: object, limit: int = 60) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
