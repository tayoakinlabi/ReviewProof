"""Gate 1: a planted error approved in a review screen never reaches the executor, including
when the registry is down. Nothing in Phase 2 starts until this file passes.

The fake review screen rubber-stamps everything. The fake target system records what reaches it,
and also rejects reserved target IDs, as a real one is told to. The tests assert that it never
even has to.
"""

import json
import sqlite3
import subprocess
import sys
import threading
from collections import Counter
from contextlib import closing

import pytest
from deploy import ITEMS, QUEUE, item, submit_until_pair
from helpers import triggers_removed

from reviewproof import (
    GuardError,
    Oversight,
    TransitionError,
    UnrecordedExecutionError,
)
from reviewproof.store import open_oversight_db, open_planted_db
from reviewproof.store.eventlog import compute_hash


class TargetSystem:
    """Stands in for the quality system that approved items are written to."""

    def __init__(self):
        self.written = []
        self.rejected_reserved = []

    def __call__(self, target_id, payload):
        if target_id.startswith("rp-reserved-"):
            self.rejected_reserved.append(target_id)
            raise PermissionError("reserved range")
        self.written.append((target_id, payload))
        return f"written:{target_id}"


def planted_ids(deployment):
    rows = deployment.query("SELECT payload, reserved_target_id FROM planted_errors", planted=True)
    return (
        {json.loads(r["payload"])["deviation_id"] for r in rows},
        {r["reserved_target_id"] for r in rows},
    )


def assert_nothing_planted_reached(target, deployment):
    deviation_ids, reserved = planted_ids(deployment)
    assert not target.rejected_reserved, "the guard let a reserved ID through to the host"
    assert not {payload["deviation_id"] for _, payload in target.written} & deviation_ids
    assert not {target_id for target_id, _ in target.written} & reserved


def approve(ov, ticket, reviewer="r-1"):
    ov.shown(ticket.token, reviewer)
    ov.decided(ticket.token, reviewer, "approve")


def approved_pair(ov):
    """A real and a planted item, both approved by a rubber-stamping reviewer."""
    real, planted = submit_until_pair(ov)
    approve(ov, real)
    approve(ov, planted)
    return real, planted


@pytest.fixture
def target():
    return TargetSystem()


# ----------------------------------------------------------------- normal running


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_rubber_stamping_reviewer_cannot_get_a_planted_error_executed(make, target, seed):
    deployment = make(rate=0.3)
    ov = deployment.open(seed=seed)
    deployment.load_planted(ov)
    expected, blocked = {}, 0
    for number in range(150):
        real = item(number % 20)
        payload = {**real.payload, "deviation_id": f"DEV-2025-{number:04d}"}
        real = type(real)(payload=payload, target_id=f"QMS-{number}")
        for ticket in ov.submit(real):
            approve(ov, ticket)
            result = ov.execute(ticket.token, ticket.payload, target)
            if ticket.payload == payload:
                expected[f"QMS-{number}"] = payload
                assert result.executed and result.value == f"written:QMS-{number}"
            else:
                blocked += 1
                assert not result.executed and result.value is None

    assert blocked > 20
    assert dict(target.written) == expected
    assert len(target.written) == 150  # every real item once, nothing else
    assert_nothing_planted_reached(target, deployment)
    events = Counter(r["type"] for r in deployment.query("SELECT type FROM events"))
    assert events["blocked"] == events["withdrawn"] == blocked
    assert events["executed"] == 150
    assert events["execute_requested"] == 150 + blocked
    assert deployment.verify().ok


def test_a_planted_error_edited_and_approved_is_not_executed(ov, deployment, target):
    _, planted = submit_until_pair(ov)
    final = {**planted.payload, "description": "Edited by the reviewer."}
    ov.shown(planted.token, "r-1")
    ov.decided(planted.token, "r-1", "edit", final_payload=final)
    assert not ov.execute(planted.token, final, target).executed
    assert target.written == []


def test_a_rejected_item_of_either_kind_is_not_executed(ov, target):
    for ticket in submit_until_pair(ov):
        ov.shown(ticket.token, "r-1")
        ov.decided(ticket.token, "r-1", "reject")
        with pytest.raises(TransitionError, match="rejected"):
            ov.execute(ticket.token, ticket.payload, target)
    assert target.written == []


def test_an_undecided_item_of_either_kind_is_not_executed(ov, target):
    for ticket in submit_until_pair(ov):
        ov.shown(ticket.token, "r-1")
        with pytest.raises(TransitionError, match="no decision"):
            ov.execute(ticket.token, ticket.payload, target)
    assert target.written == []


def test_nothing_runs_twice(ov, target):
    for ticket in approved_pair(ov):
        ov.execute(ticket.token, ticket.payload, target)
        for _ in range(3):
            with pytest.raises(TransitionError, match="already been requested"):
                ov.execute(ticket.token, ticket.payload, target)
    assert len(target.written) == 1


def test_a_changed_payload_is_refused_for_either_kind(ov, deployment, target):
    messages = []
    for ticket in approved_pair(ov):
        changed = {**ticket.payload, "classification": "critical", "capa_required": True}
        changed["description"] += " altered"
        with pytest.raises(GuardError) as error:
            ov.execute(ticket.token, changed, target)
        messages.append(str(error.value))
    assert messages[0] == messages[1]
    assert target.written == []
    reasons = [
        json.loads(r["data"])["reason"]
        for r in deployment.query("SELECT data FROM events WHERE type = 'blocked'")
    ]
    assert reasons == ["payload_changed"] * 2


def test_an_edit_executes_only_the_edited_payload(ov, target):
    real, _ = submit_until_pair(ov)
    final = {**real.payload, "classification": "critical"}
    ov.shown(real.token, "r-1")
    ov.decided(real.token, "r-1", "edit", final_payload=final)
    with pytest.raises(GuardError, match="not the one that was approved"):
        ov.execute(real.token, real.payload, target)
    assert ov.execute(real.token, final, target).executed
    assert [payload for _, payload in target.written] == [final]


# ----------------------------------------------------------------- the registry is down


def break_registry(how, ov, deployment, monkeypatch):
    if how == "connection closed":
        ov._planted.close()
    elif how in ("item_registry", "planted_errors"):
        with closing(open_planted_db(deployment.planted_db)) as db:
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute(f"DROP TABLE {how}")
    elif how == "file missing":
        ov._files = (ov._files[0], deployment.folder / "gone.db")
    elif how == "locked":
        # A second connection holding the file exclusively, as a stuck maintenance job might.
        ov._planted.execute("PRAGMA busy_timeout = 10")

        def locked(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr("reviewproof.guard.read_entry", locked)
    elif how == "unforeseen bug":

        def bug(*args, **kwargs):
            raise ZeroDivisionError

        monkeypatch.setattr("reviewproof.guard.commitment_of", bug)


@pytest.mark.parametrize(
    "how",
    ["connection closed", "item_registry", "file missing", "locked", "unforeseen bug"],
)
def test_with_the_registry_down_nothing_runs(ov, deployment, target, monkeypatch, how):
    real, planted = approved_pair(ov)
    break_registry(how, ov, deployment, monkeypatch)
    for ticket in (planted, real):
        with pytest.raises(GuardError, match=r"Nothing was executed"):
            ov.execute(ticket.token, ticket.payload, target)
    assert target.written == []
    assert target.rejected_reserved == []


def test_a_lost_planted_library_blocks_planted_items_only_safely(ov, deployment, target):
    real, planted = approved_pair(ov)
    break_registry("planted_errors", ov, deployment, None)
    with pytest.raises(GuardError):
        ov.execute(planted.token, planted.payload, target)
    # The real item cannot pass the independent reserved-ID test either: fail closed.
    with pytest.raises(GuardError):
        ov.execute(real.token, real.payload, target)
    assert target.written == []


def test_after_an_outage_the_real_item_can_run_and_the_planted_one_still_cannot(
    ov, deployment, target, monkeypatch
):
    real, planted = approved_pair(ov)
    with monkeypatch.context() as outage:
        break_registry("unforeseen bug", ov, deployment, outage)
        for ticket in (real, planted):
            with pytest.raises(GuardError):
                ov.execute(ticket.token, ticket.payload, target)
    assert ov.execute(real.token, real.payload, target).executed
    assert not ov.execute(planted.token, planted.payload, target).executed
    assert_nothing_planted_reached(target, deployment)


class Failing:
    """A connection that raises on one chosen statement, as storage that dies mid-run would."""

    def __init__(self, connection, counter):
        self._connection = connection
        self._counter = counter

    def execute(self, *args, **kwargs):
        self._counter.tick()
        return self._connection.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            setattr(self._connection, name, value)


class Counter_:
    def __init__(self, fail_at, forever):
        self.calls, self.fail_at, self.forever = 0, fail_at, forever

    def tick(self):
        self.calls += 1
        if self.calls == self.fail_at or (self.forever and self.calls > self.fail_at):
            raise sqlite3.OperationalError("disk I/O error")


def inject(ov, counter, which):
    """Make the chosen database fail, wherever the guard reaches it."""
    originals = (ov._db, ov._planted, ov._log._db, ov._records.db)
    if which in ("registry", "both"):
        ov._planted = Failing(originals[1], counter)
    if which in ("log", "both"):
        broken = Failing(originals[0], counter)
        ov._db = ov._log._db = ov._records.db = broken

    def restore():
        ov._db, ov._planted, ov._log._db, ov._records.db = originals
        for connection in originals[:2]:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
        ov._log._in_transaction = False

    return restore


@pytest.mark.parametrize("forever", [False, True], ids=["one failure", "stays down"])
@pytest.mark.parametrize("which", ["registry", "log", "both"])
@pytest.mark.parametrize("kind", ["planted", "real"])
def test_a_database_failing_at_any_statement(make, which, kind, forever):
    """Fail the 1st statement, then the 2nd, and so on, until a run needs no more."""
    deployment = make(rate=0.5)
    ov = deployment.open()
    deployment.load_planted(ov)
    fail_at, outcomes = 0, Counter()
    while True:
        fail_at += 1
        assert fail_at < 200, "the guard never got through"
        real, planted = approved_pair(ov)
        ticket = real if kind == "real" else planted
        target = TargetSystem()
        counter = Counter_(fail_at, forever)
        restore = inject(ov, counter, which)
        try:
            result = ov.execute(ticket.token, ticket.payload, target)
            outcomes["executed" if result.executed else "blocked"] += 1
        except UnrecordedExecutionError:
            assert kind == "real" and len(target.written) == 1
            outcomes["unrecorded"] += 1
        except GuardError:
            assert target.written == []
            outcomes["refused"] += 1
        except sqlite3.Error:
            assert target.written == []
            outcomes["storage error"] += 1
        finally:
            restore()
        assert not target.rejected_reserved
        assert len(target.written) <= (1 if kind == "real" else 0)
        if counter.calls < fail_at:  # this run needed fewer statements: all are covered
            break
    # A real item reads the restricted database twice: its row, then the reserved-ID check.
    assert fail_at >= 3, "the fault was not injected into every statement"
    assert outcomes["refused"] > 0
    assert deployment.verify().chain_error is None


# ----------------------------------------------------------------- someone tampers


def planted_item_id(deployment):
    [row] = deployment.query(
        "SELECT item_id FROM item_registry WHERE kind = 'planted'", planted=True
    )
    return row["item_id"]


def relabel_as_real(deployment, item_id):
    with (
        closing(open_planted_db(deployment.planted_db)) as db,
        triggers_removed(db, "item_registry"),
    ):
        db.execute(
            "UPDATE item_registry SET kind = 'real', planted_error_id = NULL, "
            "assigned_reviewer = NULL WHERE item_id = ?",
            (item_id,),
        )


def test_relabelling_a_planted_error_as_real_is_caught(ov, deployment, target):
    _, planted = approved_pair(ov)
    relabel_as_real(deployment, planted_item_id(deployment))
    with pytest.raises(GuardError, match="registry does not match the log"):
        ov.execute(planted.token, planted.payload, target)
    assert target.written == []
    assert target.rejected_reserved == []


def forge_commitment(deployment, item_id, rewrite_rest):
    """Rewrite the submission so that the relabelled row matches it."""
    from reviewproof.store.registry import commitment_of, read_entry

    with closing(open_planted_db(deployment.planted_db)) as registry:
        commitment = commitment_of(registry, read_entry(registry, item_id))
    with closing(open_oversight_db(deployment.db)) as db, triggers_removed(db):
        rows = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY event_id")]
        previous, rewriting = None, False
        for row in rows:
            forged = row["item_id"] == item_id and row["type"] == "submitted"
            if not forged and not rewriting:
                previous = row["hash"]
                continue
            data = json.loads(row["data"])
            if forged:
                data["commitment"] = commitment
            record = {**row, "data": data}
            if rewriting:
                record["prev_hash"] = previous
            text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            previous = compute_hash(record)
            db.execute(
                "UPDATE events SET data = ?, prev_hash = ?, hash = ? WHERE event_id = ?",
                (text, record["prev_hash"], previous, row["event_id"]),
            )
            rewriting = rewrite_rest


def test_forging_the_log_entry_as_well_breaks_the_chain_link(ov, deployment, target):
    _, planted = approved_pair(ov)
    item_id = planted_item_id(deployment)
    relabel_as_real(deployment, item_id)
    forge_commitment(deployment, item_id, rewrite_rest=False)
    with pytest.raises(GuardError):
        ov.execute(planted.token, planted.payload, target)
    assert target.written == []
    assert target.rejected_reserved == []


def test_even_with_the_whole_log_rewritten_the_reserved_id_stops_it(ov, deployment, target):
    """The last line of defence: the registry now says real and the whole chain agrees."""
    _, planted = approved_pair(ov)
    item_id = planted_item_id(deployment)
    relabel_as_real(deployment, item_id)
    forge_commitment(deployment, item_id, rewrite_rest=True)
    assert deployment.verify().ok  # only an exported head would show the rewrite
    with pytest.raises(GuardError, match="reserved"):
        ov.execute(planted.token, planted.payload, target)
    assert target.written == []
    assert target.rejected_reserved == []


def test_the_reserved_check_survives_a_changed_prefix(ov, deployment, target):
    import dataclasses

    _, planted = approved_pair(ov)
    item_id = planted_item_id(deployment)
    relabel_as_real(deployment, item_id)
    forge_commitment(deployment, item_id, rewrite_rest=True)
    ov._config = dataclasses.replace(ov._config, reserved_target_prefix="another-prefix-")
    with pytest.raises(GuardError, match="reserved"):
        ov.execute(planted.token, planted.payload, target)
    assert target.written == []


def test_turning_a_rejection_into_an_approval_is_caught(ov, deployment, target):
    real, _ = submit_until_pair(ov)
    ov.shown(real.token, "r-1")
    ov.decided(real.token, "r-1", "reject")
    with closing(open_oversight_db(deployment.db)) as db, triggers_removed(db):
        db.execute("UPDATE events SET decision = 'approve' WHERE type = 'decided'")
    with pytest.raises(GuardError):
        ov.execute(real.token, real.payload, target)
    assert target.written == []


def test_a_second_forged_decision_is_caught(ov, deployment, target):
    from reviewproof.models import Decision, EventType
    from reviewproof.store.eventlog import EventLog

    real, _ = submit_until_pair(ov)
    ov.shown(real.token, "r-1")
    ov.decided(real.token, "r-1", "approve")
    with closing(open_oversight_db(deployment.db)) as db:
        row = db.execute("SELECT * FROM events WHERE type = 'decided'").fetchone()
        EventLog(db).append(
            EventType.DECIDED,
            system_id=row["system_id"],
            item_id=row["item_id"],
            reviewer_id="intruder",
            decision=Decision.APPROVE,
        )
    with pytest.raises(GuardError, match="inconsistent"):
        ov.execute(real.token, real.payload, target)
    assert target.written == []


# ----------------------------------------------------------------- concurrency and crashes


def test_concurrent_execution_requests_run_a_real_item_once(ov, deployment):
    real, planted = approved_pair(ov)
    target, errors = TargetSystem(), []
    start = threading.Barrier(8)

    def request(ticket):
        try:
            with Oversight(deployment.config, QUEUE, deployment.db, deployment.planted_db) as own:
                start.wait()
                own.execute(ticket.token, ticket.payload, target)
        except TransitionError:
            pass
        except BaseException as error:  # reported in the main thread
            errors.append(error)

    threads = [threading.Thread(target=request, args=(t,)) for t in [real, planted] * 4]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(target.written) == 1
    assert_nothing_planted_reached(target, deployment)


CRASH = """
import json, os, sys
from reviewproof import Oversight
config, db, planted, token, payload, marker = sys.argv[1:7]

def executor(target_id, payload):
    with open(marker, "a") as file:
        file.write(target_id + "\\n")
    os._exit(9)  # the process dies in the middle of writing

with Oversight(config, "deviation-triage", db, planted) as ov:
    ov.execute(token, json.loads(payload), executor)
"""


def test_after_a_crash_during_execution_the_guard_does_not_retry(deployment, target):
    ov = deployment.open()
    [ticket] = ov.submit(item(0))
    approve(ov, ticket)
    marker = deployment.folder / "ran.txt"
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            CRASH,
            str(deployment.folder / "reviewproof.yaml"),
            str(deployment.db),
            str(deployment.planted_db),
            ticket.token,
            json.dumps(ticket.payload),
            str(marker),
        ],
        capture_output=True,
        timeout=120,
    )
    assert done.returncode == 9, done.stderr.decode()
    assert marker.read_text().splitlines() == ["QMS-REC-0000"]
    with pytest.raises(TransitionError, match="already been requested"):
        ov.execute(ticket.token, ticket.payload, target)
    assert target.written == []
    assert deployment.verify().ok


# ----------------------------------------------------------------- the executor fails


def test_a_failing_executor_is_logged_and_not_retried(ov, deployment, target):
    real, _ = approved_pair(ov)

    def broken(target_id, payload):
        raise ConnectionError("the quality system is unreachable: SECRET-DETAIL")

    with pytest.raises(ConnectionError):
        ov.execute(real.token, real.payload, broken)
    [row] = deployment.query("SELECT data FROM events WHERE type = 'execute_failed'")
    assert json.loads(row["data"]) == {"error_type": "ConnectionError"}
    with pytest.raises(TransitionError):
        ov.execute(real.token, real.payload, target)


def test_an_interrupted_executor_is_logged_too(ov, deployment):
    real, _ = approved_pair(ov)

    def interrupted(target_id, payload):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ov.execute(real.token, real.payload, interrupted)
    assert deployment.query("SELECT 1 AS x FROM events WHERE type = 'execute_failed'")


def test_if_the_result_cannot_be_logged_the_caller_is_told_it_ran(ov, deployment, target):
    real, _ = approved_pair(ov)
    with closing(open_oversight_db(deployment.db)) as db:
        db.execute(
            "CREATE TRIGGER stop BEFORE INSERT ON events WHEN NEW.type IN "
            "('executed', 'execute_failed') BEGIN SELECT RAISE(ABORT, 'full'); END"
        )
    with pytest.raises(UnrecordedExecutionError, match="Do not run the item again"):
        ov.execute(real.token, real.payload, target)
    assert len(target.written) == 1
    assert not issubclass(UnrecordedExecutionError, GuardError)

    # A failing executor whose failure cannot be logged still reports its own error.
    real2, _ = approved_pair(ov)

    def broken(target_id, payload):
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        ov.execute(real2.token, real2.payload, broken)


def test_when_even_the_block_cannot_be_logged_nothing_runs(ov, deployment, target, caplog):
    real, planted = approved_pair(ov)
    with closing(open_oversight_db(deployment.db)) as db:
        db.execute(
            "CREATE TRIGGER stop BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'x'); END"
        )
    for ticket in (real, planted):
        with pytest.raises(GuardError):
            ov.execute(ticket.token, ticket.payload, target)
    assert target.written == []
    assert "could not be logged" in caplog.text


def test_a_failure_in_the_last_check_runs_nothing(ov, target, monkeypatch):
    real, _ = approved_pair(ov)
    original = ov._planted

    class FailsOnReservedCheck:
        def execute(self, sql, *args):
            if "reserved_target_id = ?" in sql:
                raise sqlite3.OperationalError("gone")
            return original.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(original, name)

    ov._planted = FailsOnReservedCheck()
    with pytest.raises(GuardError):
        ov.execute(real.token, real.payload, target)
    ov._planted = original
    assert target.written == []
    # It was an outage, so the one attempt is still there.
    assert ov.execute(real.token, real.payload, target).executed
    assert len(target.written) == 1


# ----------------------------------------------------------------- misuse


def test_bad_arguments_run_nothing(ov, target):
    from reviewproof import OversightError, UnknownTokenError

    real, _ = approved_pair(ov)
    with pytest.raises(OversightError, match="executor"):
        ov.execute(real.token, real.payload, "not a function")
    with pytest.raises(OversightError, match="payload"):
        ov.execute(real.token, "text", target)
    with pytest.raises(UnknownTokenError):
        ov.execute("nope", real.payload, target)
    assert target.written == []


def test_logs_and_errors_never_name_a_planted_error(ov, deployment, target, caplog):
    _, planted = approved_pair(ov)
    with (
        closing(open_planted_db(deployment.planted_db)) as db,
        triggers_removed(db, "planted_errors"),
    ):
        db.execute("UPDATE planted_errors SET payload = 'not json'")
    with pytest.raises(GuardError) as error:
        ov.execute(planted.token, planted.payload, target)
    assert "DEMO-" not in str(error.value) + caplog.text
    assert error.value.__cause__ is None and error.value.__suppress_context__
    assert ITEMS[0]["description"] not in caplog.text
