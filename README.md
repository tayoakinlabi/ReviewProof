# ReviewProof

A rubber-stamp detector for AI oversight: an open-source Python SDK that tests whether human
reviewers of AI output actually catch errors.

**Status: pre-release, not usable yet.** The package name is a placeholder while the core is built.

ReviewProof puts *planted errors* (items with one known defect, written and approved by your QA
team) into a real approval queue. It blocks them from ever being executed, records every review
step in a tamper-evident log, and reports how many were caught.

- It is not an approval engine and has no review UI.
- It is not pre-validated. A validation pack to adapt is planned.
- It sends no telemetry.

## Guides

- [Writing planted errors](docs/guides/writing-planted-errors.md)

## License

Apache 2.0. See [LICENSE](https://github.com/tayoakinlabi/ReviewProof/blob/main/LICENSE).
