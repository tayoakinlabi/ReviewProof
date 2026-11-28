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

## Architecture

ReviewProof is a library that runs inside your application. It sits around the approval step you
already have, and keeps its records in two SQLite files.

```text
  YOUR APPLICATION                   REVIEWPROOF                      ITS TWO FILES

  AI agent proposes an item
         |                      +---------------------+          +----------------------+
         |  submit(item)        | GATE                |          | planted.db           |
         +--------------------> | sometimes adds a    | <------> | RESTRICTED           |
         |                      | planted error       |          |                      |
         | <------------------- +---------------------+          | planted errors       |
         |  1 or 2 tickets                                       | which items are real |
         v                                                       | and which planted    |
  Your review queue             +---------------------+          +----------------------+
  and screen (people)           | RECORDER            |
         |  shown(), decided()  | one decision for    |          +----------------------+
         +--------------------> | each item           |          | oversight.db         |
         |                      +---------------------+ -------> |                      |
         |                                                       | hash-chained log of  |
         |  execute(token,      +---------------------+          | every step           |
         |    payload, writer)  | GUARD               | <------> |                      |
         +--------------------> | checks, then acts   |          | no content of real   |
                                +---------------------+          | items, only hashes   |
                                           |                     +----------------------+
                                           |  only if every check passes
                                           v
  Your writer function  <------------------+
  writes to your target system
```

The guard also reads `planted.db`, and each part writes its steps to the log.

The other parts run away from the review path. All but `rp craft` work on the same two files:

```text
  PREPARE  (QA and an engineer, where reviewers cannot see)

    files you write -----------------------> rp plant import, approve --> planted.db
    items you export --> rp craft --> a file ---^
                         (opens neither database)

  LEARN FROM IT  (the QA lead)

    oversight.db --+                                       +--> rp report (HTML, CSV)
                   +--> verify --> metrics --> thresholds -+
    planted.db ----+                                       +--> rp alerts (email, webhook)

  CHECK AND CORRECT

    rp verify        check the log for changes; limits are under "Choices that shape it"
    rp qualify       test one reviewer, away from the live queue
    rp plant void    two people remove a faulty planted error's results; reports say so
```

### The life of an item

1. **Submit.** Your code passes the agent's proposal and the ID of the record it would write.
   With each real item, the gate releases an approved planted error with probability
   p / (1 − p), so that planted items are about a share p of all items. Each draw is
   independent, so there is no pattern to learn.
2. **Queue.** You get one ticket, or two in random order. A ticket holds a token and the item's
   content, nothing that says whether it is planted. Queue them all.
3. **Review.** Your screen shows each item. Your code records `shown()` and then one
   `decided()`: approve, edit or reject.
4. **Execute.** Call `execute()` for every approved or edited ticket. The guard calls your
   writer function only if all its checks pass:
   - the item has an approve or edit decision, and no earlier request to execute;
   - the log entries it relies on are intact;
   - the item is registered as real, and that record still matches the one made at submission;
   - the content is what was approved;
   - the target ID is not one reserved for planted errors, checked separately from the
     real-or-planted record.

   **Any failure, expected or not, means that nothing runs.** An approved planted item is not
   written.
5. **Score and report.** A reviewer catches a planted error by rejecting it, or by editing the
   field that holds the defect. Reports are calculated only from evidence that verifies.

## Using it from Python

```python
from reviewproof import Item, Oversight, initialise
from reviewproof.config import load_config

config = load_config("reviewproof.yaml")  # your AI systems and queues
initialise("oversight.db", "planted.db")  # once, at setup

with Oversight(config, "deviation-triage", "oversight.db", "planted.db") as oversight:
    item = Item(payload=proposal, target_id="QMS-0001")
    for ticket in oversight.submit(item, reviewer="r-104"):  # one or two tickets
        oversight.shown(ticket.token, "r-104")
        decision = ask_reviewer(ticket.payload)  # your screen: "approve" or "reject"
        oversight.decided(ticket.token, "r-104", decision)
        if decision == "approve":
            oversight.execute(ticket.token, ticket.payload, write_to_target)
```

`proposal` is a dictionary in your queue's item format, and `ask_reviewer` and
`write_to_target(target_id, payload)` are yours. For simplicity this leaves out edits, and asks
straight away. A real queue holds tickets until someone reviews them, and must not show which
arrived together. **Read the
[integration guide](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/integration.md)
before using it:** planted errors stay hidden only if your application keeps its rules.

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

## The `rp` command

| Command | What it does |
| --- | --- |
| `rp init` | Create the two databases, or upgrade ones from an earlier release |
| `rp system register` | Record a new version of your AI system |
| `rp plant` | Draft, import, approve, list, retire and void planted errors |
| `rp craft` | Draft planted errors from items you export. **It cannot make an item anonymous** |
| `rp qualify` | Run seeded-defect sessions to qualify a reviewer |
| `rp report`, `rp alerts` | Report catch rates and other figures, and send threshold alerts |
| `rp verify` | Check the log, and export its latest hash to keep elsewhere |

See the [command reference](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/cli.md).

## Inside the code

The package is in
[`src/reviewproof`](https://github.com/tayoakinlabi/ReviewProof/tree/v0.2.0/src/reviewproof).

| Part | Modules | Responsible for |
| --- | --- | --- |
| Host API | `api`, `models`, `errors`, `records`, `config` | `Oversight`, the items and tickets, and the settings file |
| Gate | `gate` | When a planted error is released, and which one |
| Guard | `guard` | The last check before anything is acted on |
| Planted errors | `planted`, `craft`, `mutators`, `voids` | The library and its approvals, drafting, and voiding |
| Review outcomes | `feedback`, `qualification` | Scoring a decision, telling the reviewer, and qualification sessions |
| Evidence | `store/`, `canonical`, `capture` | Database schemas and upgrades, the hash chain, the commitments between the two files, and verification |
| Formats | `itemschema`, `strictload` | A small subset of JSON Schema, and loading that refuses repeated keys |
| Results | `metrics/`, `report/`, `alerts` | Figures with 95% intervals, thresholds, HTML and CSV reports |
| Agent libraries | `adapters/`, `keyed` | Plain functions, five libraries, and calls that pause and resume |
| Tools | `cli/`, `simulator` | The `rp` command, and the demo's simulated reviewers |

### Choices that shape it

- **Two files, two levels of access.** `oversight.db` holds the log. `planted.db` holds what
  would give planted items away, so reviewers must have no access to it.
- **The log shows tampering, within a limit.** Each entry holds the hash of the one before, and
  salted hashes tie the log to records in `planted.db`. **Someone who can rewrite the files
  can rebuild the whole chain,** so export the latest hash at intervals to storage they cannot
  change, and verify against it.
- **The guard does not trust one answer.** Besides the real-or-planted record, it separately
  refuses target IDs reserved for planted errors. Make your target system refuse them too.
- **Upgrades are explicit.** Opening an older database changes nothing until you run `rp init`.
- **Few moving parts.** One runtime dependency, no server, and no AI model. The only things it
  sends over a network are the alerts you set up.

### Agent libraries

| Library | Guide |
| --- | --- |
| LangGraph | [Guide](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/langgraph.md) |
| OpenAI Agents SDK | [Guide](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/openai-agents.md) |
| Pydantic AI (Python 3.11 or later) | [Guide](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/pydantic-ai.md) |
| Strands Agents | [Guide](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/strands.md) |
| Microsoft Agent Framework (Python 3.11 or later) | [Guide](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/agent-framework.md) |

**Each library has conditions for safe use,** and some send trace data elsewhere by default.
Read its guide first.

## Tests and development

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest
```

Adapter tests are skipped unless the library is installed, for example with
`python -m pip install -e ".[dev,langgraph]"`.

- On each push, GitHub Actions runs the tests on Linux and Windows with Python 3.10 to 3.14,
  requires at least 90% coverage of the files listed as safety-critical, and tests each adapter
  against its oldest supported and newest library release.
- [Tests for the first gate](https://github.com/tayoakinlabi/ReviewProof/tree/v0.2.0/tests/gate1)
  check that a planted item is never executed.
- Tests tagged with a requirement ID produce the
  [traceability matrix](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/validation-pack/trace-matrix.md).
  A tag shows which tests were written for a requirement, not that it is met in your use.

Contributions need a sign-off: see
[CONTRIBUTING](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/CONTRIBUTING.md). Report vulnerabilities privately, as the
[security policy](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/SECURITY.md) describes.

## Guides

- [Integrating ReviewProof](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/integration.md): read its safety rules first
- [Writing planted errors](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/writing-planted-errors.md)
- [Qualifying reviewers](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/guides/qualification.md)
- [The `rp` command, report settings and how figures are defined](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/docs/cli.md)
- [A complete example](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/examples/deviation_triage/integrate.py)
- [Design records](https://github.com/tayoakinlabi/ReviewProof/tree/v0.2.0/docs/adr): why it is built this way
- [Changes](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/CHANGELOG.md)

All example data is synthetic.

## License

Apache 2.0. See [LICENSE](https://github.com/tayoakinlabi/ReviewProof/blob/v0.2.0/LICENSE).
