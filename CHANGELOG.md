# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the case format is at `format_version: 1` and this project is below `1.0.0`, the
format itself may still change. Any change to it will appear here with the migration.

## [Unreleased]

### Added

- A `scope-guard` CI job (`scripts/scope_guard.py`): a pull request that removes a public
  definition from `beval/` fails unless the description names that definition. `beval` is
  imported as a library as well as run as a CLI, so a removal breaks code outside this
  repository. `tests/test_public_surface.py` pins the same surface on every push, which is
  the half that also runs on a direct push to `main`.
- A `Where this sits next to the alternatives` section in the README, comparing this harness
  with Amazon Bedrock's own evaluation jobs and with promptfoo, and naming the three choices
  that are deliberate rather than missing. The README described what the harness does
  without saying who should use something else instead.

### Fixed

- `beval.__version__` reports the released version. It had stayed at `0.0.1` through both
  releases because nothing read it, and `tests/test_version.py` now ties it to the newest
  release heading in this file so it cannot drift again.

## [0.2.0] - 2026-08-17

The runner, and still no model call from this project. Every number in the repository comes
from a committed fixture or a committed record, and the report says so wherever it appears.
Calling a model is the user's own run, on the user's own account.

### Added

- `beval.request` — builds the body of a Bedrock Converse call from one case and the
  suite's `defaults`, as a pure function with no client and no credentials. The field
  mapping is documented in [`docs/converse-request.md`](docs/converse-request.md) and read
  from the API reference on 2026-08-16, not from memory.
- `beval run` — asks every case in a suite, writes a run file, and optionally a **record**
  holding the raw request and response for every case. `--replay <record>` answers from a
  record instead of calling a model, so a run made once on an account stays reproducible
  on a machine with none. A replay refuses to answer a case whose request has changed
  since it was recorded, which is what keeps a record evidence rather than a cache; CI
  asserts that refusal on a deliberately edited record. Format in
  [`docs/record-format.md`](docs/record-format.md).
- A committed record, `tests/fixtures/records/support-triage-record.json`, carrying the
  same hand-written answers as the fixture run. A test replays it and asserts the two
  still agree, so the runner is held to a run file written before it existed.
- `beval.bedrock` — the only module that imports `boto3`, lazily. `boto3` is still not a
  dependency of this project, and CI runs the whole runner path without it.
- `beval.client` — the boundary to the SDK, `invoke(model_id, body) -> response`, plus the
  reader that turns a Converse response into a run-file record and a scripted fake client
  that returns the shapes the service returns. Reading is strict: a missing `usage` raises
  instead of defaulting to zero tokens, and a missing `metrics` stays missing rather than
  becoming a latency of zero.

## [0.1.0] - 2026-08-16

Everything that does not need an AWS account. No model has been called at any point: every
number produced by this release comes from a committed fixture, and the report labels it as
one.

### Added

- Case suite format, documented in `docs/case-format.md`, with six machine-decidable check
  types: `contains_all`, `contains_any`, `not_contains`, `regex`, `max_words`, and
  `json_field_equals`.
- `beval validate` — reports every problem in a suite at once, with case id and field name,
  instead of stopping at the first. A case with no checks is refused: it would add weight to
  the score while measuring nothing.
- `beval show` — prints what a suite measures, by case, check type and tag.
- `beval score` — scores a run file against a suite, with a token, latency and cost ledger.
  `--min-score` gates the exit code on the score, so the command can be used in CI.
- `beval compare` — diffs two runs of the same suite case by case, leading with the cases
  that changed verdict rather than the totals. `--fail-on-regression` exits `1` when a case
  passed in the baseline and fails in the candidate, whatever the score did.
- Run file format, documented in `docs/run-format.md`. A case with no response counts as
  failed, so a run that crashed halfway cannot look like a good run on a smaller sample.
- Price lists supplied by the caller, carrying the page and the date they were read from.
  No rate is built into the code, and `pricing.example.json` is refused until both
  provenance fields are filled in.
- Fixture labelling in the report itself: a run whose source is not `bedrock` is named as
  hand-written output wherever its numbers appear, including in a comparison against a
  recorded run.

### Not in this release

- The Bedrock runner. Recording real responses is the next piece, and the only one that
  needs credentials.

[Unreleased]: https://github.com/vinhnguyenthanhdn/bedrock-eval-harness/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/vinhnguyenthanhdn/bedrock-eval-harness/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vinhnguyenthanhdn/bedrock-eval-harness/releases/tag/v0.1.0
