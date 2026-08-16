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

from .ledger import load_prices, percentile, score_run
from .runfile import load_run
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

    p_score = sub.add_parser("score", help="score a run file against a suite")
    p_score.add_argument("suite", type=Path)
    p_score.add_argument("run", type=Path)
    p_score.add_argument(
        "--prices",
        type=Path,
        help="price list to turn tokens into dollars; without it the report shows tokens only",
    )

    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args.paths)
    if args.command == "show":
        return _cmd_show(args.path)
    if args.command == "score":
        return _cmd_score(args.suite, args.run, args.prices)
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


def _cmd_score(suite_path: Path, run_path: Path, prices_path: Path | None) -> int:
    suite, problems = load_suite(suite_path)
    if suite is None:
        _print_problems(suite_path, problems)
        return 1

    run, problems = load_run(run_path)
    if run is None:
        _print_problems(run_path, problems)
        return 1

    prices = None
    if prices_path is not None:
        prices, problems = load_prices(prices_path)
        if prices is None:
            _print_problems(prices_path, problems)
            return 1

    if run.suite_id != suite.suite_id:
        print(
            f"run was recorded against suite {run.suite_id!r}, not {suite.suite_id!r}",
            file=sys.stderr,
        )
        return 1

    scored = score_run(suite, run)

    print(f"suite    {suite.suite_id}")
    print(f"run      {run.run_id}  model={run.model_id}")
    if not run.is_measurement:
        # Say it in the report, not only in the file: a fixture score is not a measurement.
        print(f"source   {run.source} — hand-written output, not a call to a model")
    elif run.recorded_at:
        print(f"source   {run.source}, recorded {run.recorded_at}")
    print()

    width = max([len("case")] + [len(result.case_id) for result in scored.results] or [4])
    for result in scored.results:
        mark = "pass" if result.passed else "FAIL"
        print(f"{mark}  {result.case_id:<{width}}  weight {_num(result.weight)}")
        for check in result.failures:
            print(f"        └ {check.name}: {check.detail}")

    for case_id in scored.missing_case_ids:
        print(f"MISS  {case_id:<{width}}  no response in the run file")
    for case_id in scored.extra_case_ids:
        print(f"warn  {case_id:<{width}}  response for a case the suite does not have")

    print()
    passed = sum(1 for result in scored.results if result.passed)
    print(
        f"score    {scored.score * 100:.1f}%  "
        f"({_num(scored.earned_weight)}/{_num(scored.possible_weight)} weight, "
        f"{passed}/{len(suite.cases)} cases)"
    )
    print(f"tokens   in {scored.input_tokens}  out {scored.output_tokens}")

    latencies = scored.latencies
    if latencies:
        p50 = percentile(latencies, 0.5)
        p95 = percentile(latencies, 0.95)
        print(
            f"latency  p50 {p50:.0f} ms  p95 {p95:.0f} ms  "
            f"(from {len(latencies)}/{len(run.responses)} responses)"
        )
    else:
        print("latency  not recorded")

    cost = scored.cost_usd(prices)
    if cost is None and prices is None:
        print("cost     no price list given (--prices)")
    elif cost is None:
        print(f"cost     no price for model {run.model_id!r} in the price list")
    else:
        print(f"cost     ${cost:.6f}  (prices read {prices.read_on} from {prices.source_url})")

    return 0 if not scored.missing_case_ids else 1


def _print_problems(path: Path, problems: list[str]) -> None:
    print(f"FAIL {path}  ({len(problems)} problem(s))", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)


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
