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

`beval.client.read_response` turns a Converse response into the run-file record for one
case. The paths come from the same page and the same date:

| Converse response | Run file field |
|---|---|
| `output.message.content[].text` | `output_text` |
| `usage.inputTokens` | `input_tokens` |
| `usage.outputTokens` | `output_tokens` |
| `metrics.latencyMs` | `latency_ms` |
| `stopReason` | `stop_reason` |

Two traps, both of which now fail a test rather than a bill: the response uses
**camelCase** (`inputTokens`) while the run file uses snake_case, and `metrics.latencyMs`
is the number the service reports. Timing the call on the client instead would fold
network time into every latency comparison between two runs.

Reading is strict. A missing `usage` raises instead of defaulting to zero tokens, because
a cost report of zero looks like an answer. A missing `metrics` is allowed and stays
missing: absent latency is not zero latency, and the ledger reports percentiles over the
responses that carried one.

## The client boundary

`beval.client.ConverseClient` is one method — `invoke(model_id, body) -> response` — with
the raw Converse shapes on both sides. Below it is `boto3`, which lands with the runner;
above it everything is pure and runs in CI without credentials.

`beval.bedrock.BedrockConverseClient` is the real implementation, and the only place that
imports `boto3`. The import is lazy and `boto3` is not a dependency of this project:
nothing else here needs it, and CI must run without it. Credentials come from the standard
AWS credential chain — the harness never reads a key from a suite, a run file or a flag.

`ScriptedClient` is the fake used in tests. It answers from a script and records what it
was asked, so a test can assert the request that went out and the reading of the answer
that came back. It is not a mock of the harness's own types: it takes and returns exactly
what the SDK does, which is what makes a green test say something about the Converse
contract rather than about the fake. `make_converse_response()` builds the responses it
serves, and lives next to the reader so that renaming a field breaks both sides at once.
