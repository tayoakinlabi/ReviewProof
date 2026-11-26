# ReviewProof

A rubber-stamp detector for AI oversight: an open-source Python SDK for testing whether human
reviewers of AI output actually catch errors.

**Status: 0.1.0, pre-release. Not for production use, and not pre-validated.** The safety core is
built: submitting and reviewing items, blocking planted errors from execution, and a
tamper-evident log. **Metrics and reports are not built yet.**

ReviewProof puts *planted errors* (items with one known defect, written and approved by your QA
team) into a real approval queue. It blocks them from being executed through its guard, and
records every review step in a tamper-evident log. Reporting how many were caught is next.

- It is not an approval engine and has no review UI.
- It cannot stop an application that bypasses `execute()` and writes to its target directly.
- In this version, everything a reviewer sees about an item must come from the item itself,
  because a planted error has no agent trace or retrieved sources behind it.
- A validation pack to adapt is planned.
- It sends no telemetry.

## Install

```bash
pip install reviewproof==0.1.0
```

Python 3.10 or later. The one runtime dependency is PyYAML.

## Guides

- [Integrating ReviewProof](https://github.com/tayoakinlabi/ReviewProof/blob/v0.1.0/docs/guides/integration.md):
  read its safety rules first
- [Writing planted errors](https://github.com/tayoakinlabi/ReviewProof/blob/v0.1.0/docs/guides/writing-planted-errors.md)
- [A complete example](https://github.com/tayoakinlabi/ReviewProof/blob/v0.1.0/examples/deviation_triage/integrate.py)
- [Design records](https://github.com/tayoakinlabi/ReviewProof/tree/v0.1.0/docs/adr)
- [Changes](https://github.com/tayoakinlabi/ReviewProof/blob/v0.1.0/CHANGELOG.md)

All example data is synthetic.

## License

Apache 2.0. See [LICENSE](https://github.com/tayoakinlabi/ReviewProof/blob/v0.1.0/LICENSE).
