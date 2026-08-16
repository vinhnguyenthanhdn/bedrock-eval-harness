"""Diff two scored runs of the same suite.

A total score answers "did it get better", which is the question that hides the answer
you need. Two runs can land on the same percentage with a different set of cases passing,
and a run that gains three cases while quietly losing the prompt-injection case reads as an
improvement. So the unit here is the **case**, and the score delta is printed last.

Everything in this module is pure: two `Scored` objects in, a verdict out.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ledger import Scored


@dataclass(frozen=True)
class Comparison:
    baseline: Scored
    candidate: Scored

    regressed: tuple[str, ...]
    """Cases that passed in the baseline and fail in the candidate."""

    fixed: tuple[str, ...]
    """Cases that failed in the baseline and pass in the candidate."""

    still_passing: tuple[str, ...]
    still_failing: tuple[str, ...]

    scored_only_in_baseline: tuple[str, ...]
    """Cases the candidate has no response for, so no verdict can be compared."""

    scored_only_in_candidate: tuple[str, ...]
    missing_from_both: tuple[str, ...]

    @property
    def score_delta(self) -> float:
        """Candidate score minus baseline score, as a fraction."""
        return self.candidate.score - self.baseline.score

    @property
    def has_regression(self) -> bool:
        return bool(self.regressed)

    @property
    def comparable_case_count(self) -> int:
        return (
            len(self.regressed)
            + len(self.fixed)
            + len(self.still_passing)
            + len(self.still_failing)
        )

    @property
    def compares_fixture_with_measurement(self) -> bool:
        """One side is hand-written and the other is a real call.

        Worth saying out loud: that comparison measures the person who wrote the fixture,
        not the two models.
        """
        return self.baseline.run.is_measurement != self.candidate.run.is_measurement


def compare_scored(baseline: Scored, candidate: Scored) -> Comparison:
    """Compare two runs that have already been scored against the same suite.

    Case order follows the suite, not either run file, so the report reads the same way
    every time regardless of the order responses happened to be written in.
    """
    base_verdicts = {result.case_id: result.passed for result in baseline.results}
    cand_verdicts = {result.case_id: result.passed for result in candidate.results}

    regressed: list[str] = []
    fixed: list[str] = []
    still_passing: list[str] = []
    still_failing: list[str] = []
    only_baseline: list[str] = []
    only_candidate: list[str] = []
    neither: list[str] = []

    for case in baseline.suite.cases:
        in_base = case.id in base_verdicts
        in_cand = case.id in cand_verdicts

        if in_base and in_cand:
            before, after = base_verdicts[case.id], cand_verdicts[case.id]
            if before and not after:
                regressed.append(case.id)
            elif not before and after:
                fixed.append(case.id)
            elif before:
                still_passing.append(case.id)
            else:
                still_failing.append(case.id)
        elif in_base:
            only_baseline.append(case.id)
        elif in_cand:
            only_candidate.append(case.id)
        else:
            neither.append(case.id)

    return Comparison(
        baseline=baseline,
        candidate=candidate,
        regressed=tuple(regressed),
        fixed=tuple(fixed),
        still_passing=tuple(still_passing),
        still_failing=tuple(still_failing),
        scored_only_in_baseline=tuple(only_baseline),
        scored_only_in_candidate=tuple(only_candidate),
        missing_from_both=tuple(neither),
    )
