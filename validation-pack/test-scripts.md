# Test scripts

**Template.** For the validation lead and testers. Read the [overview](README.md) and complete
your [risk assessment](risk-assessment.md) first: it decides which of these you run, and what
else you test.

- **[Part A](#part-a-the-projects-automated-tests)** runs the project's automated tests in your
  environment and makes a record of them.
- **[Part B](#part-b-tests-in-your-own-deployment)** is for what those tests cannot reach: your
  application, your target system and your people.
- **[Part C](#part-c-duties-shown-by-documents)** lists duties shown by documents.

**Use a test environment only,** with test databases, a test target system and synthetic data.
Never run these against production: some need reviewers to approve everything.

For every script, record the version tested, each actual result, who ran it and when, any
deviation and how it was resolved, and review under your procedures. The expected results below
describe the demo settings; adapt the names to yours.

## Part A: the project's automated tests

Run these from the **source** of the version you are validating, so that the tests, examples and
this pack match it. They need no network connection once installed, and no API key.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| A1 | Get the source: check out the version's tag or commit from the repository. Record the commit. | The working folder has no changes. | | |
| A2 | In a new virtual environment: `python -m pip install -e ".[dev]"`. Add the extra of each adapter you use, for example `".[dev,langgraph]"`. Then record `python -m pip freeze`. | Installs without errors. `rp --version` shows the version you are validating. | | |
| A3 | `python -m pytest -p no:cacheprovider --junitxml=results.xml` | The last line shows no failures or errors. Tests of adapters you did not install are skipped, and a few tests apply to some Python versions only. Record the line. | | |
| A4 | `python validation-pack/trace_matrix.py --check` | No output, and exit status 0: every requirement has tagged tests and the [matrix](trace-matrix.md) matches them. | | |
| A5 | `python validation-pack/trace_matrix.py --results results.xml --out evidence-record.md`, adding `--adapters` followed by the adapters you use, or `--adapters none`. Run it in the same environment as A3. | A record naming your environment, the results file's hash, and a result for each requirement. None has failed. Each one that is **incomplete** says why: investigate and justify each. | | |

A requirement recorded as passed means that its tagged tests passed. Judge, for each one that
matters to your risk assessment, whether those tests are enough: the matrix names them. Keep
`results.xml` and the record.

## Part B: tests in your own deployment

### MT-001: a planted error approved on your screen never reaches your target

**The most important test here.** It checks DEP-001, DEP-002, and REQ-013 and REQ-014 as you have
built them. Run it again whenever your integration, write function or review screen changes.

**Before you start:** a test deployment through your real application, with new test databases.
Set the queue's `planted_rate` to `0.5`, the most allowed. Import and approve **exactly 10**
planted errors, whose identifiers only the test lead can recognise. Prepare 20 synthetic real
items with different identifiers. Name one test reviewer.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | Submit the 20 real items through your application, before any is reviewed. | Your review queue holds **30** tickets: 20 real and 10 planted. With all 10 waiting, no more can be released: a report made now shows, under the health of the planted-error programme, "Times a planted error was due but none could be used: 10". | | |
| 2 | As the test reviewer, approve **every** ticket without reading it. | Every approval looks and behaves the same on screen. | | |
| 3 | Look in the target system and its logs. | **Exactly the 20 real records**, with the approved content. No planted identifier anywhere. No write was attempted with an ID in the reserved range. | | |
| 4 | `rp report --queue ... --out report.html` | "Of the latest 10 planted errors, **0 were caught** (0%)." This shows that all 10 were decided, and that none was caught. | | |
| 5 | `rp verify --planted planted.db` | Exit status 0, "Item registry: matches the log." | | |

If a planted identifier appears in the target system, stop: do not use the integration until the
cause is found.

### MT-002: your target refuses the reserved range

Checks DEP-002.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | In the test environment, call your write function directly with a target ID made of your `reserved_target_prefix` followed by `test-1`, and otherwise valid content. | It refuses. Nothing is written. | | |
| 2 | Repeat with the prefix in capital letters and with a space before it. | It refuses each time. | | |

### MT-003: there is no way round the guard

Checks DEP-001, by review of your code and configuration.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | List every code path, job and agent tool that can write what is reviewed. | A complete list. | | |
| 2 | For each, show that the write happens only through `execute()`, `execute_call()` or an adapter. | Every path goes through the guard. | | |
| 3 | If an agent is used, list the tools it is given. | The reviewed tool is the adapter's. Your raw write function is not available to the agent. | | |

### MT-004: your review screen does not give planted items away

Checks DEP-003, DEP-004 and DEP-006. Use a queue as in MT-001. The tester knows which items are
planted; give a second person the screen without that knowledge.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | Inspect the queue's list, sorting, filters and counts. | Nothing shows when a ticket arrived, which arrived together, or the order of arrival. | | |
| 2 | Open real and planted tickets side by side, including every panel, link and export. | Each shows only what its ticket holds. No source, history, trace or record appears for some and not others. | | |
| 3 | Look at network traffic, page source and anything else the reviewer can reach. | No field, ID format or response differs by kind. | | |
| 4 | Approve one of each, and compare what follows. | The screens, messages and timing a reviewer can see are the same. | | |
| 5 | The second person tries to pick out planted items by anything other than their content. | They do no better than guessing. | | |

### MT-005: reviewers cannot watch the agent or the target

Checks DEP-005 and DEP-017. Skip, with the reason, if nothing but your review queue is affected
by a review.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | With a reviewer's account, try to reach the agent's traces, saved state and logs, and any tracing service it sends to. | No access. | | |
| 2 | With the same account, try to see the target system's records while items wait. | No access, or no way to see changes while items wait. | | |
| 3 | Approve one item and watch the queue and everything else you can see. | Nothing shows whether the agent continued, including the arrival of new items. | | |
| 4 | Check how and when your application resumes agents. | It follows your written rule, such as a schedule, and your reason why this hides the connection. | | |
| 5 | List what your agent library, model provider and tracing tools send and store. | It matches what you have approved. | | |

### MT-006: access to the databases

Checks DEP-007.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | With a reviewer's account, try to read `oversight.db` and `planted.db`, and their backups and exports. | Refused. | | |
| 2 | With an account that is neither the application's nor a QA administrator's, try to change `planted.db`. | Refused. | | |
| 3 | List who can write each file and who can run `rp plant`, `rp qualify` and `rp system`. | Only the roles in your intended-use statement. | | |

### MT-007: a changed log is detected

Checks REQ-026, REQ-027 and DEP-010. Use the databases from MT-001. The script below **only
reads them**, and changes copies of its own.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | `rp verify --planted planted.db --export-head head.json`. Store `head.json` where your procedure says (DEP-010). | Exit status 0, "Head exported to head.json". | | |
| 2 | `python validation-pack/make_tampered_copies.py oversight.db planted.db tampered` | "Made edited, truncated, relabelled in tampered. The originals were not changed." | | |
| 3 | `rp verify --db tampered/edited/oversight.db --planted tampered/edited/planted.db` | Exit status 1, "FAILED: the chain is broken at event" and its number. | | |
| 4 | The same for `truncated`, **without** a head. | **Exit status 0, "Chain intact".** The chain alone cannot see that the latest events are gone. | | |
| 5 | The same for `truncated`, adding `--expect-head head.json`. | Exit status 1, "FAILED: the exported head check": events are missing or changed. | | |
| 6 | The same as step 3 for `relabelled`. | Exit status 1, "the registry row does not match the log". | | |
| 7 | `rp report --db tampered/edited/oversight.db --planted tampered/edited/planted.db --out tampered/report.html` | Exit status 1. The page says that no results are shown. | | |
| 8 | Delete the `tampered` folder: it holds planted errors. | Deleted. | | |

### MT-008: backup and restore

Checks DEP-011.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | `rp verify --planted planted.db --export-head before.json` | Exit status 0. | | |
| 2 | Back up **both** databases with your normal method, and restore them as a pair to another place. | Both restored. | | |
| 3 | `rp verify --db <restored>/oversight.db --planted <restored>/planted.db --expect-head before.json` | Exit status 0, "Item registry: matches the log." and "Exported head: found at event" with its number. | | |
| 4 | Show that your backup method copies a database consistently while the application runs, or that you stop the application first. | Evidence of one of these. | | |

### MT-009: alerts reach the QA lead

Checks REQ-040 as you have set it up. Use the databases after MT-001, whose results break the
catch-rate limit.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | `rp alerts` | Prints an alert for the queue that includes "Planted errors caught". Nothing is sent. | | |
| 2 | Set your test route under `alerts:` in the settings, then `rp alerts --send` | Exit status 0, "Sent by" and the route. | | |
| 3 | Read what arrived at the QA lead's address or channel. | It reaches only the intended people, and names no reviewer, item or planted error. | | |

### MT-010: a failed write stops your agent

Only if you use paused agents or an adapter. Checks DEP-016, and REQ-051 as you have built it.
Read your adapter's guide first.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | Make the test target refuse or fail the next write. Have the agent propose it, approve the real item, and resume as your application does. | Your application receives an error, of the kind the guide names, and handles it. | | |
| 2 | Look at the agent's run and your model provider's record of it. | The model was not asked again and was given no result for the call. | | |
| 3 | Look at the target's log. | One attempt to write. | | |
| 4 | List the latest log entries (command below). | The call shows `execute_requested` then `execute_failed`, and no `executed`. | | |
| 5 | Try to resume the same pause again, and restart the application and try again. | Nothing more is written. | | |

To list the latest log entries without changing the database:

```bash
python -c "import sqlite3; db = sqlite3.connect('file:oversight.db?mode=ro', uri=True); [print(*row) for row in db.execute('SELECT event_id, ts_utc, type, reviewer_id, decision FROM events ORDER BY event_id DESC LIMIT 20')]"
```

### MT-011: who and when

Checks DEP-008 and DEP-009.

| Step | Do | Expected result | Actual | Pass? |
| --- | --- | --- | --- | --- |
| 1 | Sign in as two test reviewers in turn, and decide an item as each. | Each needs their own authentication. | | |
| 2 | List the latest log entries (command above). | Each decision carries that person's pseudonymous ID, and no name or email address. | | |
| 3 | Compare the times, which are in UTC, with a trusted clock. | They agree within your limit. | | |
| 4 | Show how the machine's clock is set, kept right and protected from change. | Evidence under your procedure. | | |
| 5 | Try to approve a planted error as its author, using your administration process. | Refused. Your process stops one person acting as two. | | |

## Part C: duties shown by documents

For each, record the document and why it is enough.

| Duty | Evidence to collect | Record |
| --- | --- | --- |
| DEP-012 | Who may write and approve planted errors, how realism and real defects are checked, the stock and its replacement, and how shortfalls are acted on | |
| DEP-013 | What reviewers were told, who may see individual results and why, and the privacy, employment and works-council reviews | |
| DEP-014 | The rule on who may review without a current qualification, and how it is enforced | |
| DEP-015 | Change control of the settings file, including thresholds and the AI system's version | |
| DEP-016 | The error-handling procedure, including who checks the target after a failed or unrecorded write | |
| DEP-018 | The fixed versions, your representative test data and results, and the rule for testing again before a change |  |
