# ReviewProof

A rubber-stamp detector for AI oversight: an open-source Python SDK for testing whether human
reviewers of AI output actually catch errors.

**Status: pre-release. Not for production use, and not pre-validated.** No outside quality or
privacy specialist has reviewed it, and the method has not been tried on a real review queue.

Version 0.2.0 has the safety core, metrics and reports, reviewer qualification, voiding of
faulty planted errors, drafting of planted errors from output you export, and adapters for five
agent libraries.

ReviewProof puts *planted errors* (items with one known defect, written and approved by your QA
team) into a real approval queue. It blocks them from being executed through its guard, and
records every review step in a tamper-evident log. It then reports how many were caught.

- It is not an approval engine and has no review UI.
- It cannot stop an application that bypasses `execute()` and writes to its target directly.
- In this version, everything a reviewer sees about an item must come from the item itself,
  because a planted error has no agent trace or retrieved sources behind it.
- A [validation pack](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/validation-pack/README.md) to adapt is in
  draft. No outside quality specialist has reviewed it.
- It sends no telemetry.

## Quickstart: run the demo

You need Python 3.10 or later and Git. All the demo's data is invented, and its two reviewers
are simulated.

```bash
git clone https://github.com/tayoakinlabi/ReviewProof.git
cd ReviewProof
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
python examples/demo.py demo-output
```

Then open `demo-output/report.html`. The demo gives two simulated reviewers a week of work,
verifies the log, and writes the report a QA lead would read. The report should flag the
rubber-stamping reviewer and not the careful one. The folder must not already hold a demo.

The simulated reviewers behave as they were programmed to. The demo shows that the software
works, **not** that the method does: that needs real reviewers on a real queue.

## Install

```bash
pip install reviewproof==0.2.0
```

Python 3.10 or later. The one runtime dependency is PyYAML. Each agent library adapter is an
optional extra, such as `pip install "reviewproof[langgraph]==0.2.0"`; its guide says what it
needs.

**Upgrading from 0.1.0?** Read the
[change impact](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/CHANGELOG.md#change-impact) first.
Back up both databases, then run `rp init`. Version 0.1.0 cannot open them afterwards.

## Guides

- [Integrating ReviewProof](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/integration.md): read its safety rules first
- [Writing planted errors](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/writing-planted-errors.md)
- [Qualifying reviewers](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/qualification.md)
- [The `rp` command, report settings and how figures are defined](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/cli.md)
- [LangGraph](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/langgraph.md)
- [OpenAI Agents SDK](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/openai-agents.md)
- [Pydantic AI](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/pydantic-ai.md)
- [Strands Agents](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/strands.md)
- [Microsoft Agent Framework](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/agent-framework.md)
- [A complete example](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/examples/deviation_triage/integrate.py)
- [Design records](https://github.com/tayoakinlabi/ReviewProof/tree/v0.2.0/docs/adr)
- [Changes](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/CHANGELOG.md)

All example data is synthetic.

## License

Apache 2.0. See [LICENSE](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/LICENSE).
