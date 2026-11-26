# ReviewProof

A rubber-stamp detector for AI oversight: an open-source Python SDK that tests whether human
reviewers of AI output actually catch errors.

**Status: pre-release, not for production use.** The safety core is built: submitting and
reviewing items, blocking planted errors from execution, and the tamper-evident log. Metrics
and reports are not built yet, and nothing here is pre-validated. The package on PyPI
(0.0.0) is a placeholder that does not contain this code.

ReviewProof puts *planted errors* (items with one known defect, written and approved by your QA
team) into a real approval queue. It blocks them from ever being executed, records every review
step in a tamper-evident log, and reports how many were caught.

- It is not an approval engine and has no review UI.
- It is not pre-validated. A validation pack to adapt is planned.
- It sends no telemetry.

## Guides

- [Integrating ReviewProof](docs/guides/integration.md): read its safety rules first
- [Writing planted errors](docs/guides/writing-planted-errors.md)
- [A complete example](examples/deviation_triage/integrate.py)
- [Design records](docs/adr/)

## License

Apache 2.0. See [LICENSE](https://github.com/tayoakinlabi/ReviewProof/blob/main/LICENSE).
