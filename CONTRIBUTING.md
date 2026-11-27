# Contributing to ReviewProof

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
```

CI runs the same three checks.

## Rules that matter here

- **No real planted errors.** Only synthetic examples under `examples/`. A public library would
  teach reviewers what planted errors look like.
- **No real data.** No clinical, patient, company or employer data in code, tests, issues or
  examples.
- **No telemetry.** The tool never makes a network call that the user has not configured.
- **Minimal dependencies.** A new runtime dependency needs an issue and a reason first.
- **Changes to the guard or event log** need tests, including the failure case.
- Design decisions are recorded in [`docs/adr/`](docs/adr/).

## Developer Certificate of Origin

Every commit must be signed off, which certifies that you agree to the
[Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote the change,
or otherwise have the right to submit it under this project's license.

```bash
git commit -s -m "Describe the change"
```

This adds a `Signed-off-by: Your Name <you@example.com>` line. CI rejects pull requests with
unsigned commits.

## Requirements and tests

A test written for a requirement in the [validation pack](validation-pack/requirements.md) is
tagged with it: `@pytest.mark.req("REQ-001")`. Tag a test only if it checks that requirement.
After changing tags or requirements, run `python validation-pack/trace_matrix.py` and commit
the matrix. A change to what the software must do changes the requirement too.
