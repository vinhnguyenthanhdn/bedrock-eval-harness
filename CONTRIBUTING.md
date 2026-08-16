# Contributing

Issues and pull requests are welcome, including on the formats themselves while they are
still at version 1.

## Run what CI runs

No dependencies, nothing to install:

```bash
python3 -m unittest discover -s tests -t . -v
python3 -m beval validate $(find suites -name '*.json' | sort)
python3 -m beval score suites/support-triage/suite.json \
                      tests/fixtures/runs/support-triage-fixture.json \
                      --prices tests/fixtures/prices-fixture.json
```

CI runs exactly these on Python 3.10 and 3.12, plus one step that asserts
`pricing.example.json` is still **refused**. If your change makes that guard pass, CI goes
red on purpose.

Test with a clean checkout when you touch anything to do with fixtures or `.gitignore`:

```bash
git clone . /tmp/beval-check && cd /tmp/beval-check && python3 -m unittest discover -s tests -t .
```

A file that exists in your working tree but was never committed makes tests pass locally
and fail for everyone else. That has already happened once here.

## Scope of a pull request

A PR that changes one behaviour touches the lines that behaviour lives on. Do not rewrite a
module to fix a function inside it: a large diff is expensive to review and hides the change
that mattered.

If you think a rewrite is the right call, open an issue first and say why.

## The rules the code is built around

These are not style preferences; a PR that breaks one will be asked to change:

1. **No invented numbers.** No price, latency or token count is hard-coded anywhere. Prices
   come from a price list the user supplies, carrying the page and the date it was read
   from. If you add a number to the README, say which committed run produced it.
2. **Fixtures are labelled.** Anything written by a human carries `"source": "fixture"` and
   a `note` saying so, and reports print that label. A fixture score must never be quotable
   as a measurement.
3. **A check that cannot fail is not a check.** Adding a check type means adding both its
   schema entry and its evaluator, plus a test that shows it going red — the evaluator
   raises for an unknown type rather than defaulting to pass, so a half-added type breaks
   loudly.
4. **Missing data is not zero.** A missing latency is excluded from percentiles and the
   count is printed; a case with no response counts as a failure.
5. **Validators report every problem at once**, with the field name and the case id.

## Adding a check type

1. Add it to `CHECK_SCHEMA` in `beval/checks.py` with its required and optional fields.
2. Add a branch to `evaluate_check` in `beval/evaluate.py`.
3. Document it in the table in `docs/case-format.md` — a test asserts every known type
   appears there.
4. Add tests for a passing and a failing input.

## Commit messages

Imperative subject line, and a body explaining *why* when the decision is not obvious.
