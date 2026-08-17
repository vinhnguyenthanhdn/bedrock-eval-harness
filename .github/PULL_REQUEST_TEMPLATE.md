## What this changes

<!-- One or two sentences. -->

## Files touched, and why each one

<!-- List them. A file in the diff that is not explained here is usually scope creep. -->

## How you verified it

```
<!-- Paste the commands you ran and their output. -->
```

- [ ] `python3 -m unittest discover -s tests -t .` passes
- [ ] `python3 -m beval validate $(find suites -name '*.json' | sort)` passes
- [ ] Checked on a clean clone if this touches fixtures, packaging or `.gitignore`
- [ ] `CHANGELOG.md` has a line under `## [Unreleased]` when the change is user-visible

## If this fixes a defect

- [ ] A test covering it **fails** on `main` — output pasted above
- [ ] No number was added to the docs that a committed run does not produce
