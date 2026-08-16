"""Command line entry point.

    beval validate <suite.json> [...]           check a suite against the format
    beval show <suite.json>                     print what the suite measures
    beval score <suite.json> <run.json>         score one run, with tokens and cost
    beval compare <suite.json> <a.json> <b.json>  show which cases changed verdict
    beval run <suite.json> --model <id>         ask a model, write a run file

Every command except `run` is offline. `run` is offline too when given `--replay`: it
re-asks the recorded questions and reads the recorded answers, which is how a run made on
someone's account stays reproducible on a machine that has none.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

from .bedrock import BedrockConverseClient, MissingSDK
from .compare import compare_scored
from .ledger import load_prices, percentile, score_run
from .runfile import load_run
from .runner import (
    RecordedClient,
    RecordMismatch,
    RunAborted,
    load_record,
    run_suite,
    run_to_json,
    write_json,
)
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
    p_score.add_argument(
        "--min-score",
        type=float,
        help="minimum score percentage (0..100) required to exit 0",
    )

    p_compare = sub.add_parser(
        "compare",
        help="show which cases changed verdict between two runs of the same suite",
    )
    p_compare.add_argument("suite", type=Path)
    p_compare.add_argument("baseline", type=Path, help="the run you are comparing against")
    p_compare.add_argument("candidate", type=Path, help="the newer run")
    p_compare.add_argument(
        "--prices",
        type=Path,
        help="price list to turn tokens into dollars; without it the report shows tokens only",
    )
    p_compare.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit 1 when any case passed in the baseline and fails in the candidate",
    )

    p_run = sub.add_parser(
        "run",
        help="ask every case in a suite and write a run file",
    )
    p_run.add_argument("suite", type=Path)
    p_run.add_argument(
        "--model",
        help="model id to call, e.g. anthropic.claude-3-haiku-20240307-v1:0."
        " Required unless --replay carries one",
    )
    p_run.add_argument("--region", help="AWS region; defaults to the SDK's own resolution")
    p_run.add_argument("--run-id", help="name for this run; defaults to the model and the record")
    p_run.add_argument("--out", type=Path, help="where to write the run file; default stdout")
    p_run.add_argument(
        "--record",
        type=Path,
        help="also write the raw requests and responses here, so the run can be replayed",
    )
    p_run.add_argument(
        "--replay",
        type=Path,
        help="answer from a record instead of calling the model; needs no credentials",
    )

    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args.paths)
    if args.command == "show":
        return _cmd_show(args.path)
    if args.command == "score":
        return _cmd_score(args.suite, args.run, args.prices, args.min_score)
    if args.command == "compare":
        return _cmd_compare(
            args.suite,
            args.baseline,
            args.candidate,
            args.prices,
            args.fail_on_regression,
        )
    if args.command == "run":
        return _cmd_run(args)
    return 2


def _cmd_run(args) -> int:
    suite, problems = load_suite(args.suite)
    if suite is None:
        _print_problems(args.suite, problems)
        return 1

    record = None
    if args.replay is not None:
        record, problems = load_record(args.replay)
        if record is None:
            _print_problems(args.replay, problems)
            return 1
        if record.suite_id != suite.suite_id:
            print(
                f"record was made against suite {record.suite_id!r}, not {suite.suite_id!r}",
                file=sys.stderr,
            )
            return 1

    model_id = args.model or (record.model_id if record else None)
    if not model_id:
        print("error: --model is required unless --replay carries one", file=sys.stderr)
        return 2
    if record is not None and args.model and args.model != record.model_id:
        # Serving one model's answers under another model's name would put a wrong model
        # id in the run file, and the model id is what the cost report prices.
        print(
            f"error: record holds answers from {record.model_id!r}, not {args.model!r}",
            file=sys.stderr,
        )
        return 2

    if record is not None:
        client = RecordedClient(record)
        source = record.source
    else:
        client = BedrockConverseClient(region=args.region)
        source = "bedrock"

    run_id = args.run_id or (f"replay-{record.model_id}" if record else f"run-{model_id}")
    try:
        outcome = run_suite(
            suite,
            model_id,
            client,
            run_id=run_id,
            source=source,
            region=args.region or (record.region if record else None),
            recorded_at=record.recorded_at if record else None,
        )
    except (MissingSDK, RunAborted, RecordMismatch) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for case_id, why in outcome.failures:
        # Loud, and on stderr: these cases have no response, so the score treats them as
        # failures. A silent skip would look like a lower score for the wrong reason.
        print(f"warning: {case_id}: {why}", file=sys.stderr)

    payload = run_to_json(outcome.run)
    if args.out is not None:
        write_json(args.out, payload)
        print(f"run file  {args.out}  ({len(outcome.run.responses)} response(s))")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.record is not None:
        if record is not None:
            print("note: --record ignored during a replay", file=sys.stderr)
        else:
            write_json(args.record, outcome.record.to_json())
            print(f"record    {args.record}  ({len(outcome.record.exchanges)} exchange(s))")

    return 1 if outcome.failures else 0


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


def _cmd_score(
    suite_path: Path,
    run_path: Path,
    prices_path: Path | None,
    min_score: float | None = None,
) -> int:
    if min_score is not None and not (0.0 <= min_score <= 100.0):
        print(
            f"error: --min-score must be between 0 and 100, got {min_score}",
            file=sys.stderr,
        )
        return 2

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

    if min_score is not None:
        actual_percent = scored.score * 100
        if actual_percent < min_score:
            print(
                f"score {actual_percent:.1f}% is below the required minimum of {min_score:.1f}%",
                file=sys.stderr,
            )
            return 1

    return 0 if not scored.missing_case_ids else 1


def _cmd_compare(
    suite_path: Path,
    baseline_path: Path,
    candidate_path: Path,
    prices_path: Path | None,
    fail_on_regression: bool,
) -> int:
    suite, problems = load_suite(suite_path)
    if suite is None:
        _print_problems(suite_path, problems)
        return 1

    runs = []
    for path in (baseline_path, candidate_path):
        run, problems = load_run(path)
        if run is None:
            _print_problems(path, problems)
            return 1
        if run.suite_id != suite.suite_id:
            print(
                f"{path}: recorded against suite {run.suite_id!r}, not {suite.suite_id!r}",
                file=sys.stderr,
            )
            return 1
        runs.append(run)
    baseline_run, candidate_run = runs

    prices = None
    if prices_path is not None:
        prices, problems = load_prices(prices_path)
        if prices is None:
            _print_problems(prices_path, problems)
            return 1

    diff = compare_scored(score_run(suite, baseline_run), score_run(suite, candidate_run))

    print(f"suite     {suite.suite_id}")
    print(f"baseline  {baseline_run.run_id}  model={baseline_run.model_id}  ({baseline_run.source})")
    print(f"candidate {candidate_run.run_id}  model={candidate_run.model_id}  ({candidate_run.source})")
    if diff.compares_fixture_with_measurement:
        # Loud, because the numbers underneath look exactly like a model comparison.
        print()
        print(
            "warning   one side is hand-written output and the other is a recorded call; "
            "this diff shows the difference between a fixture and a measurement, not "
            "between two models"
        )
    print()

    ids = (
        diff.regressed
        + diff.fixed
        + diff.scored_only_in_baseline
        + diff.scored_only_in_candidate
        + diff.missing_from_both
    )
    width = max([len("case")] + [len(case_id) for case_id in ids] or [4])

    candidate_failures = {result.case_id: result for result in diff.candidate.results}
    baseline_failures = {result.case_id: result for result in diff.baseline.results}

    for case_id in diff.regressed:
        print(f"REGRESSED  {case_id:<{width}}  passed before, fails now")
        for check in candidate_failures[case_id].failures:
            print(f"        └ {check.name}: {check.detail}")
    for case_id in diff.fixed:
        print(f"fixed      {case_id:<{width}}  failed before, passes now")
        for check in baseline_failures[case_id].failures:
            print(f"        └ was: {check.name}: {check.detail}")
    for case_id in diff.scored_only_in_baseline:
        print(f"dropped    {case_id:<{width}}  no response in the candidate run — not compared")
    for case_id in diff.scored_only_in_candidate:
        print(f"added      {case_id:<{width}}  no response in the baseline run — not compared")
    for case_id in diff.missing_from_both:
        print(f"MISS       {case_id:<{width}}  neither run answered it")

    if not diff.regressed and not diff.fixed:
        print(f"no case changed verdict  ({diff.comparable_case_count} compared)")

    print()
    print(
        f"unchanged {len(diff.still_passing)} passing, {len(diff.still_failing)} failing "
        f"of {diff.comparable_case_count} comparable"
    )

    before, after = diff.baseline.score * 100, diff.candidate.score * 100
    print(f"score     {before:.1f}% → {after:.1f}%  ({diff.score_delta * 100:+.1f} points)")
    print(
        f"tokens    in {diff.baseline.input_tokens} → {diff.candidate.input_tokens}  "
        f"out {diff.baseline.output_tokens} → {diff.candidate.output_tokens}"
    )

    base_p50 = percentile(diff.baseline.latencies, 0.5)
    cand_p50 = percentile(diff.candidate.latencies, 0.5)
    if base_p50 is None or cand_p50 is None:
        print("latency   not recorded on both sides")
    else:
        print(f"latency   p50 {base_p50:.0f} ms → {cand_p50:.0f} ms")

    base_cost = diff.baseline.cost_usd(prices)
    cand_cost = diff.candidate.cost_usd(prices)
    if prices is None:
        print("cost      no price list given (--prices)")
    elif base_cost is None or cand_cost is None:
        unpriced = [
            run.model_id
            for run, cost in ((baseline_run, base_cost), (candidate_run, cand_cost))
            if cost is None
        ]
        # Naming them matters: comparing a priced model against an unpriced one and
        # printing one number would read as a cost delta.
        print(f"cost      no price for {', '.join(repr(m) for m in unpriced)} in the price list")
    else:
        print(
            f"cost      ${base_cost:.6f} → ${cand_cost:.6f}  "
            f"(prices read {prices.read_on} from {prices.source_url})"
        )

    if fail_on_regression and diff.has_regression:
        print(
            f"{len(diff.regressed)} case(s) passed in {baseline_run.run_id} and fail in "
            f"{candidate_run.run_id}: {', '.join(diff.regressed)}",
            file=sys.stderr,
        )
        return 1

    return 0


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
