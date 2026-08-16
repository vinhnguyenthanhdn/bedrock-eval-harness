# From a case to a Converse request

`beval.request` turns one case plus the suite's `defaults` into the body of a Bedrock
[Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
call. It is a pure function: no client, no credentials, no `boto3` import. That split is
deliberate — deciding *what to send* is the part that can be wrong in a way tests catch,
and it should not need an AWS account to be covered.

The field paths below were read from the API reference on **2026-08-16**. AWS can change
them, so read the page again before editing this module rather than trusting this table.

## The mapping

| Suite | Converse | Note |
|---|---|---|
| — | `modelId` | **Path parameter**, not a body field. `converse_kwargs()` attaches it beside the body. |
| `input.user` | `messages[0].content[0].text`, with `role: "user"` | One message per case: a case is one question asked once. |
| `input.system`, else `defaults.system` | `system[0].text` | `system` is a **list of objects**, not a string. |
| `max_output_tokens` | `inferenceConfig.maxTokens` | Case value wins over `defaults`. |
| `temperature` | `inferenceConfig.temperature` | Case value wins over `defaults`. |

## Rules that are easy to get wrong

- **`temperature: 0` is a value, not a missing field.** It is the setting most suites
  actually want, and it is exactly the one a truthiness test drops. Losing it silently
  turns a deterministic suite into a sampled one without any file changing.
- **A key that has no value is left out entirely.** No system prompt anywhere means no
  `system` key — not `[]`, not `null`. No request settings means no `inferenceConfig`.
  An empty object here would be recorded in the run file as a setting the suite never
  asked for.
- **Nothing outside the suite format is sent.** Converse also accepts `topP`,
  `stopSequences` and `additionalModelRequestFields`; none of them appear here, because a
  setting the suite cannot record is a setting a rerun next month cannot reproduce. When
  one of them is needed, it goes into the suite format first.

## Reading the response

Not implemented yet — it lands with the runner. The paths, from the same page and the
same date, so the runner is not written from memory either:

| Converse response | Run file field |
|---|---|
| `output.message.content[].text` | `output_text` |
| `usage.inputTokens` | `input_tokens` |
| `usage.outputTokens` | `output_tokens` |
| `metrics.latencyMs` | `latency_ms` |
| `stopReason` | `stop_reason` |

Two traps worth naming before the code exists: the response uses **camelCase**
(`inputTokens`), while the run file uses snake_case; and `metrics.latencyMs` is the number
the service reports. Timing the call on the client instead would fold network time into
every latency comparison between two runs.
