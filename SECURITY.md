# Security Policy

## Supported versions

This project is pre-1.0. Only the `main` branch is supported; fixes land there.

## What counts as a vulnerability here

The harness has no network code yet and no server. The realistic risks are about
**credentials and about data leaving the machine in a file you did not expect**:

- A path where credentials, an AWS session token, or an `Authorization` header could end up
  written into a run file, a report, or a log.
- A path where `beval` reads or writes outside the files it was given.
- A crafted suite, run file or price list that causes code execution rather than a
  validation error. Every input is JSON parsed with the standard library and no input is
  evaluated, so this would be a real bug.

Not vulnerabilities: a model answering badly, a suite that scores low, or a cost estimate
that disagrees with your bill because the price list you supplied was out of date.

## When the Bedrock runner lands

Two rules the runner is designed around, worth knowing before it exists:

- Credentials come from the standard AWS credential chain. The harness will never read a
  key out of a suite, a run file or a command-line flag.
- A recorded run stores the response text and token counts. It does **not** store request
  headers or credentials, and `runs/` is gitignored so a real run is not committed by
  accident. Read a run file before publishing it: whatever your prompts contain is in there.

## Reporting

Use GitHub's private vulnerability reporting on this repository (Security → Report a
vulnerability). If that is unavailable, open an issue that describes the shape of the
problem without the exploit details, and say a private channel is needed.

Please include what you ran, what happened, and what you expected. A reproduction that
runs offline is the fastest path to a fix.
