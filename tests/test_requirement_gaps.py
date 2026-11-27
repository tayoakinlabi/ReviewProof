"""Tests for the parts of requirements that no earlier test covered (step 3.4).

Each looks at a whole requirement clause, often across a whole workflow, where earlier tests
looked at one part of it.
"""

import ast
import json
import re
import socket
import sqlite3
from contextlib import closing
from dataclasses import fields
from datetime import datetime
from pathlib import Path

import pytest
from deploy import EXAMPLES, ITEMS, QUEUE, item, submit_until_pair
from qual_support import load_pool, plan, sit

from reviewproof import Item, Ticket, UnknownTokenError, initialise
from reviewproof.cli.main import main
from reviewproof.store import StoreError, open_oversight_db, open_planted_db
from reviewproof.store.eventlog import EventLog

SOURCE = Path(__file__).parents[1] / "src" / "reviewproof"
PLANTED_ROWS = "SELECT * FROM planted_errors ORDER BY planted_error_id"
# Listed here, not taken from the code, so that a column the code stops protecting is noticed.
FIXED = [
    "planted_error_id",
    "queue_id",
    "defect_class",
    "defect_field",
    "expected_decision",
    "payload",
    "correct_value",
    "notes",
    "reserved_target_id",
    "author",
    "imported_at",
    "purpose",
]
SET_ONCE = [
    "approved_by",
    "approved_at",
    "second_approved_by",
    "second_approved_at",
    "retired_at",
    "retired_by",
    "retire_reason",
]


def dump(path):
    """Everything a database holds, as text. Blobs appear as hexadecimal."""
    with closing(sqlite3.connect(path)) as db:
        return "\n".join(db.iterdump())


def decide(ov, ticket, decision="approve", final=None, reviewer="r-1"):
    ov.shown(ticket.token, reviewer)
    ov.decided(ticket.token, reviewer, decision, final_payload=final)


# ----------------------------------------------------------------- the planted-error library


@pytest.mark.req("REQ-004")
@pytest.mark.parametrize("column", FIXED)
def test_no_part_of_a_planted_errors_content_can_be_changed(ov, deployment, column):
    before = deployment.query(PLANTED_ROWS, planted=True)
    with (
        closing(open_planted_db(deployment.planted_db)) as db,
        pytest.raises(sqlite3.IntegrityError, match="content cannot be changed"),
    ):
        db.execute(f"UPDATE planted_errors SET {column} = {column}")
    assert deployment.query(PLANTED_ROWS, planted=True) == before


@pytest.mark.req("REQ-005")
def test_reviewing_submitted_items_never_creates_a_planted_error(make):
    """A heavy use of everything that touches a submitted item, then the library compared."""
    deployment = make(rate=0.5)
    ov = deployment.open()
    deployment.load_planted(ov)
    before = deployment.query(PLANTED_ROWS, planted=True)
    for number in range(60):
        base = item(number % len(ITEMS))
        payload = {**base.payload, "description": f"SUBMITTED-MARKER-{number}"}
        key = f"run/{number}"
        for ticket in ov.submit(Item(payload=payload, target_id=f"QMS-{number}"), key=key):
            real = ticket.payload == payload
            if real and number % 3 == 0:
                edited = {**payload, "root_cause": f"SUBMITTED-MARKER-edit-{number}"}
                decide(ov, ticket, "edit", edited)
                ov.settle(ticket.token, edited)
            else:
                decide(ov, ticket, "reject" if number % 3 == 1 else "approve")
                ov.settle(ticket.token, ticket.payload)
    ov.feedback("r-1", batch=True)
    assert deployment.query(PLANTED_ROWS, planted=True) == before
    library = deployment.query(PLANTED_ROWS, planted=True)
    assert "SUBMITTED-MARKER" not in json.dumps(library)


@pytest.mark.req("REQ-005")
def test_only_the_import_of_a_users_file_adds_to_the_library():
    """The one statement that adds a planted error, and what reaches it."""
    found = []
    for path in SOURCE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        found += [path.name] * len(re.findall(r"INTO planted_errors\b", text))
    assert found == ["planted.py"]
    callers = {
        path.name
        for path in SOURCE.rglob("*.py")
        if re.search(r"\.import_(data|file)\(", path.read_text(encoding="utf-8"))
    }
    # The command that imports files, and the library's own file reader.
    assert callers <= {"plant.py", "planted.py", "simulator.py"}
    for name in ("api.py", "gate.py", "guard.py", "keyed.py", "feedback.py", "qualification.py"):
        assert name not in callers


@pytest.mark.req("REQ-004")
def test_every_column_of_a_planted_error_is_accounted_for(deployment):
    columns = deployment.query("PRAGMA table_info(planted_errors)", planted=True)
    assert sorted(c["name"] for c in columns) == sorted([*FIXED, *SET_ONCE])


# ----------------------------------------------------------------- what the host is given


@pytest.mark.req("REQ-010")
def test_a_ticket_holds_a_token_and_the_content_and_nothing_else(ov):
    assert [field.name for field in fields(Ticket)] == ["token", "payload"]
    for ticket in submit_until_pair(ov):
        assert set(vars(ticket)) == {"token", "payload"}
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket.token)


# ----------------------------------------------------------------- what is stored


@pytest.mark.req("REQ-023")
def test_no_database_holds_real_content_or_a_token(ov, deployment):
    """Both databases, after everything that touches a real item: trace, screen, edit."""
    base = item(0)
    payload = {
        **base.payload,
        "description": "REAL-SECRET-description",
        "root_cause": "REAL-SECRET-cause",
    }
    trace = [
        {
            "step_id": "s1",
            "step_type": "model",
            "inputs": "REAL-SECRET-trace-input",
            "outputs": "REAL-SECRET-trace-output",
        }
    ]
    tickets = ov.submit(Item(payload=payload, target_id="QMS-SECRET-TEST", trace=trace), key="k")
    real = next(t for t in tickets if t.payload == payload)
    ov.shown(real.token, "r-1", {"sources": ["source-name"], "steps": ["s1"], "fields": []})
    edited = {**payload, "immediate_actions": ["REAL-SECRET-edit"]}
    ov.decided(real.token, "r-1", "edit", final_payload=edited)
    ov.settle(real.token, edited)
    ov.execute_call("k", edited, lambda target, content: "REAL-SECRET-result")
    for path in (deployment.db, deployment.planted_db):
        held = dump(path)
        assert "REAL-SECRET" not in held, path.name
        assert real.token not in held, path.name
    assert "source-name" in dump(deployment.planted_db)  # names are kept, content is not


# ----------------------------------------------------------------- what every event holds


@pytest.mark.req("REQ-025")
def test_every_event_of_a_whole_workflow_says_when_what_and_who(make):
    deployment = make(rate=0.5)
    ov = deployment.open()
    deployment.load_planted(ov)
    for number in range(6):
        for position, ticket in enumerate(ov.submit(item(number))):
            if (number + position) % 2:
                edited = {**ticket.payload, "root_cause": "changed"}
                decide(ov, ticket, "edit", edited)
                ov.execute(ticket.token, edited, lambda *call: None)
            else:
                decide(ov, ticket)
                ov.execute(ticket.token, ticket.payload, lambda *call: None)
    ov.feedback("r-1", batch=True)
    ov.register_system(prompt_version="2")
    events = deployment.query("SELECT * FROM events ORDER BY event_id")
    systems = {r["system_id"] for r in deployment.query("SELECT system_id FROM systems")}
    seen = {event["type"] for event in events}
    assert seen >= {
        "context_registered",
        "submitted",
        "shown",
        "decided",
        "execute_requested",
        "executed",
        "blocked",
        "withdrawn",
        "feedback_sent",
    }
    for event in events:
        assert event["type"] and event["system_id"] in systems
        stamp = datetime.strptime(event["ts_utc"], "%Y-%m-%dT%H:%M:%S.%fZ")
        assert stamp.year >= 2026
        assert event["queue_id"] == QUEUE
        if event["type"] != "context_registered":
            assert event["item_id"], event["type"]
        if event["type"] in ("shown", "decided", "executed", "feedback_sent"):
            assert event["reviewer_id"] == "r-1", event["type"]
        assert (event["decision"] is not None) == (event["type"] == "decided"), event["type"]


@pytest.mark.req("REQ-029")
def test_a_refused_write_adds_nothing_to_the_log(event_log, oversight_path):
    with closing(open_oversight_db(oversight_path)) as other:
        other.execute("PRAGMA busy_timeout = 10")
        second = EventLog(other)
        with event_log.transaction(), pytest.raises(StoreError, match="cannot lock"):
            second.append("shown", system_id="sys-1")
        assert second.head().event_count == 0


# ----------------------------------------------------------------- qualification


@pytest.mark.req("REQ-032")
def test_a_session_item_cannot_be_reviewed_or_executed_as_live_work(make):
    deployment = make(rate=0.5)
    ov = deployment.open()
    session = ov.qualification().start("r-1", plan(load_pool(ov, range(12))))
    token = session.items[0].token
    written = []
    for attempt in (
        lambda: ov.shown(token, "r-1"),
        lambda: ov.decided(token, "r-1", "approve"),
        lambda: ov.execute(token, session.items[0].payload, lambda *c: written.append(c)),
    ):
        with pytest.raises(UnknownTokenError):
            attempt()
    sit(ov.qualification(), session)
    with pytest.raises(UnknownTokenError):
        ov.execute(token, session.items[0].payload, lambda *c: written.append(c))
    assert written == []


# ----------------------------------------------------------------- the network


@pytest.fixture
def no_network(monkeypatch):
    """Refuse every connection and name lookup, local ones included, and note each attempt."""
    attempts = []

    def refuse(*arguments, **keywords):
        attempts.append(arguments[1:] or arguments)
        raise OSError("this test allows no network use")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, refuse)
    for name in ("create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, name, refuse)
    return attempts


@pytest.mark.req("REQ-043")
def test_the_network_block_would_see_a_connection(no_network):
    with pytest.raises(OSError, match="no network"):
        socket.create_connection(("example.invalid", 80))
    assert no_network


@pytest.mark.req("REQ-043")
def test_a_whole_workflow_uses_no_network(tmp_path, make, no_network, capsys):
    deployment = make(rate=0.5)
    settings = deployment.folder / "reviewproof.yaml"
    # A route is set up and a limit is broken, so that only the missing --send holds it back.
    text = settings.read_text(encoding="utf-8").split("\nalerts:")[0].split("\nreporting:")[0]
    settings.write_text(
        text + "\nreporting:\n  fast_approval_share_max: 0.0\n  min_sample: 1\n"
        "alerts:\n  qa_lead:\n    webhook: https://alerts.example.invalid/hook\n",
        encoding="utf-8",
    )
    initialise(deployment.db, deployment.planted_db)
    ov = deployment.open()
    for path in EXAMPLES:
        planted = ov.planted.import_file(path)
        ov.planted.approve(planted.planted_error_id, "qa-1")
        ov.planted.approve(planted.planted_error_id, "qa-2")
    for number in range(8):
        for ticket in ov.submit(item(number), key=f"run/{number}"):
            decide(ov, ticket)
            ov.settle(ticket.token, ticket.payload)
            if ticket.payload == ITEMS[number]:
                ov.execute_call(f"run/{number}", ticket.payload, lambda *call: None)
    ov.feedback("r-1", batch=True)
    sit(ov.qualification(), ov.qualification().start("r-2", plan(load_pool(ov, range(12)))))
    files = ["--db", str(deployment.db), "--planted", str(deployment.planted_db)]
    with_settings = ["--config", str(settings), *files]
    assert main(["verify", *files]) == 0
    for form, name in (("html", "report.html"), ("csv", "report.csv")):
        arguments = ["report", *with_settings, "--format", form, "--out", str(tmp_path / name)]
        assert main(arguments) == 0
    capsys.readouterr()
    assert main(["alerts", *with_settings]) == 0
    assert "needs attention" in capsys.readouterr().out
    assert no_network == []
    # Asked to send, the same command does try, so only the missing --send was holding it back.
    assert main(["alerts", *with_settings, "--send"]) == 3
    assert no_network


@pytest.mark.req("REQ-043")
def test_only_the_alerts_module_can_use_the_network():
    """The adapters use their agent libraries, which are the host's choice."""
    networking = {
        "socket",
        "ssl",
        "smtplib",
        "ftplib",
        "imaplib",
        "poplib",
        "http",
        "urllib.request",
        "urllib.error",
        "xmlrpc",
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "websockets",
    }
    users = {}
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            else:
                continue
            for name in names:
                if name in networking or name.split(".")[0] in networking:
                    users.setdefault(path.name, set()).add(name)
    assert set(users) == {"alerts.py"}, users
