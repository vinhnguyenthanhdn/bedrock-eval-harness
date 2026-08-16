# bedrock-eval-harness

Keep a fixed set of cases, run them against Amazon Bedrock models, and answer the two
questions you get after changing a model or a prompt: **did it get better, and what does
it now cost?**

[![CI](https://github.com/vinhnguyenthanhdn/bedrock-eval-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/vinhnguyenthanhdn/bedrock-eval-harness/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Most teams answer both questions from memory, because the previous run was a terminal
scrollback that is gone. This harness makes a run a file: same cases, same request
settings, scores and cost side by side with the run before it.

## Status

Early. Everything that does not need an AWS account is in place: the case suite format,
its validator, and the scorer with its cost and latency ledger, exercised against
committed fixtures.

| Piece | State |
|---|---|
| Case suite format (input + pass criteria) | **done** — [`docs/case-format.md`](docs/case-format.md) |
| Suite validator and CLI (`validate`, `show`) | **done** |
| Scorer, and the cost/latency ledger, on run files | **done** — scored against committed fixtures |
| Bedrock runner and response recording | next, needs an account with Bedrock enabled |
| Comparing two runs | after that |

There are no benchmark numbers in this README because no model has been called yet.
When there are, they will come from a recorded run committed to the repo, with the date
and the price list used.

## Features

- **One file describes a suite** — cases, pass criteria, and the request settings used
  to produce them, so a rerun next month sends the same request.
- **Criteria are machine-decidable.** No model grades another model, so a score is
  reproducible offline and a red result points at a specific check.
- **The validator says everything that is wrong at once**, with the case id and field
  name, instead of one error per run.
- **A case with no check is rejected**, because it would add weight to the score while
  measuring nothing.
- **A case with no response counts as failed**, so a run that crashed halfway cannot look
  like a good run on a smaller sample.
- **No prices are built in.** You pass a price list carrying the page it came from and the
  date you read it; the shipped example is refused until you fill both in.
- **Fixture runs are labelled as fixtures** in the report, so a hand-written output can
  never be quoted as a measurement.
- **Unknown fields are rejected.** A misspelled key in a check is a check that silently
  stops testing what it claims to test.

## Architecture

```
suites/<id>/suite.json ──▶ load + validate ──▶ Suite (cases, checks, request settings)
                                                  │
                          ┌───────────────────────┴────────────────────┐
                          ▼                                            ▼
                   runner (Bedrock)                              replay (fixtures)
                          │                                            │
                          └──────────────▶ responses ◀─────────────────┘
                                              │
                                              ▼
                              scorer + cost/latency ledger ──▶ run record
                                                                   │
                                                     compare(run A, run B)
```

The suite is the only hand-written input. Everything to its right is derived and can be
regenerated, which is what makes two runs comparable: they were produced from the same
contract, not from two terminal sessions.

The right-hand half of that diagram is not implemented yet — see **Status** above.

## Quick Start

No dependencies beyond the Python standard library, and nothing to install.

```bash
git clone https://github.com/vinhnguyenthanhdn/bedrock-eval-harness.git
cd bedrock-eval-harness

python3 -m beval validate suites/support-triage/suite.json
python3 -m beval show     suites/support-triage/suite.json
```

`show` prints what the suite measures:

```
suite    support-triage
about    Route an inbound support message to exactly one queue and return it as JSON.
cases    6  (total weight 9)
default  max_output_tokens = 200
default  system = You are a support triage assistant. Reply with JSON only, i…
default  temperature = 0

case                          weight  checks  types
---------------------------------------------------------------------
refund-past-window                 2       2  json_field_equals, max_words
password-reset-loop                1       2  json_field_equals, not_contains
seat-count-upgrade                 1       2  json_field_equals, regex
sso-domain-transfer                1       2  contains_any, json_field_equals
ambiguous-charge-after-cancel      2       2  json_field_equals, max_words
prompt-injection-in-ticket         2       2  json_field_equals, not_contains
---------------------------------------------------------------------
check types  contains_any=1  json_field_equals=6  max_words=2  not_contains=2  regex=1
tags         account=1  ambiguous=1  billing=3  policy=1  robustness=1  sales=1  sso=1  technical=1
```

Score a run against the suite. You can pass `--prices` for cost calculation and `--min-score <percent>` to gate CI by exiting non-zero when the score falls below a threshold:

```bash
python3 -m beval score suites/support-triage/suite.json \
                      tests/fixtures/runs/support-triage-fixture.json \
                      --prices tests/fixtures/prices-fixture.json \
                      --min-score 80
```

```
suite    support-triage
run      fixture-hand-written  model=fixture.model-v1
source   fixture — hand-written output, not a call to a model

pass  refund-past-window             weight 2
pass  password-reset-loop            weight 1
pass  seat-count-upgrade             weight 1
FAIL  sso-domain-transfer            weight 1
        └ json_field_equals: queue='technical', expected 'account'
pass  ambiguous-charge-after-cancel  weight 2
FAIL  prompt-injection-in-ticket     weight 2
        └ json_field_equals: output is not JSON: Expecting value

score    66.7%  (6/9 weight, 4/6 cases)
tokens   in 1314  out 139
latency  p50 700 ms  p95 1180 ms  (from 6/6 responses)
cost     $6.027000  (prices read 2026-08-16 from https://example.invalid/not-a-real-price-list)
```

Those token counts, latencies and rates are invented for the arithmetic — see the `note`
field in both fixture files. Nothing in this repository has been measured yet.

Run the tests the same way CI does:

```bash
python3 -m unittest discover -s tests -t . -v
```

## Usage

Write your own suite next to the sample one:

```json
{
  "format_version": 1,
  "suite_id": "my-suite",
  "description": "One sentence about what these cases are for.",
  "defaults": { "max_output_tokens": 300, "temperature": 0 },
  "cases": [
    {
      "id": "first-case",
      "input": { "user": "The message the model has to handle." },
      "checks": [
        { "type": "json_field_equals", "path": "queue", "value": "billing" },
        { "type": "max_words", "value": 60 }
      ],
      "weight": 2,
      "tags": ["billing"]
    }
  ]
}
```

Then `python3 -m beval validate path/to/suite.json`; it exits non-zero if the suite is
unusable, so it drops straight into CI. Every field and every check type is documented
in [`docs/case-format.md`](docs/case-format.md), and the run file the scorer reads is
documented in [`docs/run-format.md`](docs/run-format.md).

## Limitations

- **No model has been called yet.** The runner and the comparison step are not written, and
  every number in this repository comes from a committed fixture, not from a measurement.
- **Only machine-decidable checks.** Tone, helpfulness and factual accuracy against an
  open-ended answer are out of reach here by design. If a quality needs a judge model to
  score it, this harness will not score it.
- **It does not rank models in general.** A score only means something on the suite that
  produced it. Two suites are not comparable to each other.
- **It does not generate cases.** The suite is your input; a harness that invents its own
  cases measures the generator, not the model.
- **Not an agent framework and not a convenience wrapper** over the Bedrock SDK.

## Contributing

Issues and PRs are welcome, including on the format itself while it is still at version 1.
If a check type you need is missing, open an issue with the output you need to decide on
and the rule that decides it — the check list is deliberately short and grows from real
cases rather than from guesses about what might be useful.

## License

MIT — see [LICENSE](LICENSE).
