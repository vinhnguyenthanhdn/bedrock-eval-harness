# The record: replaying a run without an account

`beval run` writes two files. The **run file** is what the scorer reads — output text and
token counts. The **record** is the raw request and the raw response for every case,
exactly as they crossed the SDK boundary.

The record exists so that a run made once, on someone's account, stays reproducible on a
machine that has none:

```bash
# once, with credentials
beval run suites/support-triage/suite.json \
  --model anthropic.claude-3-haiku-20240307-v1:0 \
  --out runs/haiku.json --record runs/haiku-record.json

# afterwards, anywhere, forever
beval run suites/support-triage/suite.json --replay runs/haiku-record.json --out haiku.json
```

A replay re-runs the whole path — building the request, reading the response, writing the
run file — against answers that were really returned. It is not a cached score: everything
between the suite and the run file is executed again, so a change in any of it shows up.

## Replay refuses when the question changed

The one rule that makes a record evidence rather than a cache: an exchange only answers
the request that produced it. Edit the system prompt, change `temperature`, reword a case,
and the replay stops for that case instead of reporting the old answer as the answer to a
new question. The case then has no response, and the scorer already counts that as a
failure rather than dropping it from the denominator.

CI asserts the refusal, not only the success — a record file with one request edited must
make `beval run --replay` exit non-zero.

## File shape

```json
{
  "format_version": 1,
  "suite_id": "support-triage",
  "model_id": "fixture.model-v1",
  "source": "fixture",
  "region": "us-east-1",
  "recorded_at": "2026-08-16T11:20:00Z",
  "exchanges": [
    {
      "case_id": "refund-past-window",
      "request": { "messages": [], "system": [], "inferenceConfig": {} },
      "response": { "output": {}, "usage": {}, "metrics": {}, "stopReason": "end_turn" }
    }
  ]
}
```

`request` and `response` are stored verbatim in the shapes the Converse API uses; see
[`converse-request.md`](converse-request.md) for the field paths.

`source` says what produced the answers — `bedrock` if a model really returned them,
`fixture` if a person wrote them. A replay inherits it: replaying a real call does not make
the numbers less real, and replaying hand-written answers does not turn them into a
measurement. A record with no `source` reads as `fixture`, because it cannot prove
otherwise and the safe default is the one that claims less.

## What a record does not hold

- **No credentials and no request headers.** Only the body that was sent and the response
  that came back.
- **Nothing under `runs/`** is committed — that path is gitignored and CI fails if anything
  appears there. Whatever your prompts contain is in a record, so read one before
  publishing it.

The record shipped at `tests/fixtures/records/support-triage-record.json` is hand-written
output, labelled `fixture`. It carries the same answers as
`tests/fixtures/runs/support-triage-fixture.json`, and a test replays it and asserts the
two still agree — which is what keeps the runner honest about a run file written long
before the runner existed.
