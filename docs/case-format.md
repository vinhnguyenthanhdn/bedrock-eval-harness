# Case suite format

A suite is one JSON file. It holds the cases you want to keep asking a model, and the
criteria each answer must satisfy. It is the only input the harness needs — everything
else (runs, scores, cost ledgers) is derived from it and can be regenerated.

The format is versioned because saved runs point back at the suite that produced them.
A run recorded against `format_version: 1` stays readable after the format grows.

## File shape

```json
{
  "format_version": 1,
  "suite_id": "support-triage",
  "description": "Route an inbound support message to one queue and justify it.",
  "defaults": { "max_output_tokens": 300, "temperature": 0 },
  "cases": [
    {
      "id": "refund-past-window",
      "input": {
        "system": "You are a support triage assistant.",
        "user": "I bought the annual plan 8 months ago and want my money back."
      },
      "checks": [
        { "type": "contains_any", "values": ["billing"], "ignore_case": true },
        { "type": "not_contains", "values": ["I cannot help"], "ignore_case": true },
        { "type": "max_words", "value": 120 }
      ],
      "weight": 2,
      "tags": ["billing", "policy"]
    }
  ]
}
```

## Fields

### Suite level

| Field | Required | Meaning |
|---|---|---|
| `format_version` | yes | Integer. Only `1` exists today. A reader that does not know the value must refuse the file rather than guess. |
| `suite_id` | yes | `[a-z0-9-]{1,64}`. Used in run directory names, so it has to survive a filesystem. |
| `description` | yes | One sentence. Shows up in run reports next to the scores. |
| `defaults` | no | Request settings applied to every case unless the case overrides them. See below. |
| `cases` | yes | At least one case. |

### Case level

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | `[a-z0-9-]{1,64}`, unique inside the suite. Scores are keyed by it, so renaming an id breaks comparison with older runs — add a new case instead. |
| `input.user` | yes | The user turn. Non-empty string. |
| `input.system` | no | System prompt for this case. Falls back to `defaults.system`. |
| `checks` | yes | At least one check. A case nobody can fail is not a case — it inflates the score without measuring anything. |
| `weight` | no | Positive number, default `1`. A case worth twice as much gets `2`. |
| `tags` | no | Free-form labels, used to slice a report. |
| `max_output_tokens`, `temperature` | no | Per-case override of `defaults`. |

### Request settings

`max_output_tokens` (positive integer) and `temperature` (`0` to `1`) may appear in
`defaults`, in a case, or in both; the case wins. They are stored in the suite rather
than passed on the command line so that a rerun a month later sends the same request.

## Checks

Every check is machine-decidable — no model grades another model here. That is a
deliberate limit: it keeps a score reproducible offline, and it excludes qualities that
need a judge. See `Limitations` in the README.

| `type` | Fields | Passes when |
|---|---|---|
| `contains_all` | `values` (non-empty list), `ignore_case` | Every string appears in the output |
| `contains_any` | `values`, `ignore_case` | At least one string appears |
| `not_contains` | `values`, `ignore_case` | None of the strings appear |
| `regex` | `pattern`, `ignore_case` | `re.search` finds a match |
| `max_words` | `value` (positive int) | Output has at most that many whitespace-separated words |
| `json_field_equals` | `path` (dot path), `value` | Output parses as JSON and the field equals `value` |

`ignore_case` defaults to `false`.

A check may carry an optional `label` used in reports. Anything else is rejected:
unknown keys are almost always typos, and a silently ignored typo in a check is a check
that never fails.

## Validation rules

`beval validate` refuses a suite that breaks any of these, and prints every problem it
found rather than stopping at the first:

1. `format_version` is exactly `1`.
2. `suite_id` and every case `id` match `[a-z0-9-]{1,64}`.
3. Case ids are unique.
4. Every case has at least one check, and every check has a known `type` and valid fields.
5. No unknown keys anywhere — at suite, case, or check level.
6. `weight > 0`, `max_output_tokens > 0`, `0 <= temperature <= 1`.
7. `regex` patterns compile.

## What the format deliberately does not have

- **No expected answer string.** Full-text equality against a generative model measures
  luck. Criteria describe what must be true of the answer.
- **No model or region field.** The same suite is meant to be run against several models;
  binding it to one would make comparison a manual copy job.
- **No scoring weights per check.** A case is the unit that passes or fails. Per-check
  weighting inside a case buys precision the harness cannot justify yet.
