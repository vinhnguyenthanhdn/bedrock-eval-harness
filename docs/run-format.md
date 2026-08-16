# Run file format

A run file is what a runner produced for one suite against one model. The scorer reads
nothing else, which is what makes a score recomputable months later without calling
anything.

Today these files are written by hand as fixtures. The Bedrock runner will write the same
shape, and the `source` field is what tells the two apart.

## File shape

```json
{
  "format_version": 1,
  "suite_id": "support-triage",
  "run_id": "fixture-hand-written",
  "model_id": "fixture.model-v1",
  "source": "fixture",
  "recorded_at": "2026-08-16T11:20:00Z",
  "region": "us-east-1",
  "note": "Free text. Say here where the numbers came from.",
  "responses": [
    {
      "case_id": "refund-past-window",
      "output_text": "{\"queue\": \"billing\", \"reason\": \"...\"}",
      "input_tokens": 210,
      "output_tokens": 28,
      "latency_ms": 640,
      "stop_reason": "end_turn"
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `format_version` | yes | Integer, `1`. Unknown values are refused rather than guessed. |
| `suite_id` | yes | Must equal the `suite_id` of the suite you score against; `beval score` refuses a mismatch. |
| `run_id` | yes | Your name for this run. Appears in the report. |
| `model_id` | yes | The model the responses came from. Also the key used to look up prices. |
| `source` | yes | `bedrock` for output that came back from a real call, `fixture` for output written by a human. Reports label a fixture run as not a measurement. |
| `recorded_at`, `region`, `note` | no | Provenance. `note` is the place to say where invented numbers came from. |
| `responses` | yes | One entry per case that was answered. |

Response fields: `case_id`, `output_text`, `input_tokens` and `output_tokens` are required;
`latency_ms` and `stop_reason` are optional.

## Rules the loader enforces

1. Token counts are non-negative integers and must be present. A missing count would become
   a cost of zero, which is worse than an error.
2. `latency_ms` may be absent, and absent is not zero — percentiles are computed over the
   responses that carried a latency, and the report says how many that was.
3. One response per case; duplicates are refused.
4. A case in the suite with no response is reported as `MISS` and counts as a failure, so a
   run that died halfway cannot look like a good run on a smaller sample.
5. A response for a case the suite does not have is reported as a warning, not silently
   dropped.

## Prices

Cost is only printed when you pass `--prices`. The price list carries the page it came from
and the date it was read:

```json
{
  "format_version": 1,
  "source_url": "https://aws.amazon.com/bedrock/pricing/",
  "read_on": "2026-08-16",
  "models": {
    "your.model-id": { "input_per_1k_usd": 0.0, "output_per_1k_usd": 0.0 }
  }
}
```

No price is built into this repository: prices change, and a stale constant becomes a wrong
cost report nobody notices. `pricing.example.json` ships with `REPLACE_ME` in both
provenance fields and is **refused** until you replace them, so placeholder rates cannot
produce a cost report by accident.

## CI Gating with `--min-score`

To use `beval score` as a gate in CI/CD pipelines, supply `--min-score <percent>` (e.g. `--min-score 85`).
If the final score falls below the required percentage, `beval score` will print a failure message and exit with code `1`.
When `--min-score` is not passed, `beval score` exits `0` as long as all cases are answered.

