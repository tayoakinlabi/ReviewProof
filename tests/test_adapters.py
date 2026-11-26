"""The plain-function adapter, the decorator and the integration example (step 1.12)."""

import importlib.util
import json
from contextlib import closing
from pathlib import Path

import pytest
from deploy import DEMO, ITEMS, item

from reviewproof import Decision, Item, OversightError
from reviewproof.adapters import run_review
from reviewproof.store import open_oversight_db, open_planted_db
from reviewproof.store.eventlog import verify

EXAMPLE = DEMO / "integrate.py"


def load_example():
    spec = importlib.util.spec_from_file_location("integrate_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_integration_is_under_ten_lines():
    text = EXAMPLE.read_text(encoding="utf-8")
    section = text.split("# --- integration start ---")[1].split("# --- integration end ---")[0]
    lines = [line for line in section.splitlines() if line.strip()]
    assert 0 < len(lines) < 10, lines


def test_the_example_runs_and_executes_only_real_items(tmp_path):
    written = []
    db, planted_db = load_example().main(
        tmp_path,
        executor=lambda target, payload: written.append((target, payload)),
        planted_rate=0.4,
    )
    assert sorted(target for target, _ in written) == [f"QMS-{n:04d}" for n in range(20)]
    assert sorted(p["deviation_id"] for _, p in written) == [i["deviation_id"] for i in ITEMS]
    with closing(open_oversight_db(db)) as log, closing(open_planted_db(planted_db)) as registry:
        assert verify(log, registry).ok
        count = registry.execute(
            "SELECT COUNT(*) FROM item_registry WHERE kind = 'planted'"
        ).fetchone()[0]
    assert count > 0


def test_the_examples_default_executor_works(tmp_path, capsys):
    load_example().main(tmp_path)
    assert capsys.readouterr().out.count("wrote DEV-") == 20


# ----------------------------------------------------------------- run_review


@pytest.mark.parametrize("decision", ["approve", Decision.APPROVE])
def test_run_review_approves_and_executes(ov, decision):
    ran = []
    results = run_review(ov, item(0), "r-1", lambda p: decision, lambda t, p: ran.append(t))
    assert all(r.decision is Decision.APPROVE for r in results)
    assert ran == ["QMS-REC-0000"]


def test_run_review_rejects_without_executing(ov):
    results = run_review(
        ov, item(0), "r-1", lambda p: "reject", lambda *a: pytest.fail("must not run")
    )
    assert results[0].execution is None


def test_run_review_edits_and_executes_the_edit(ov):
    ran = []

    def ask(payload):
        payload["description"] += " Checked."
        return ("edit", payload)

    run_review(ov, item(0), "r-1", ask, lambda t, p: ran.append(p["description"]))
    assert ran == [ITEMS[0]["description"] + " Checked."]


def test_what_the_reviewer_changes_in_place_does_not_count_as_approved(ov):
    ran = []

    def sneaky(payload):
        payload["classification"] = "critical"
        return "approve"

    run_review(ov, item(0), "r-1", sneaky, lambda t, p: ran.append(p["classification"]))
    assert ran == [ITEMS[0]["classification"]]


def test_every_ticket_is_reviewed_even_if_one_fails(deployment, ov):
    asked = []

    def ask(payload):
        asked.append(payload)
        return "approve" if len(asked) % 2 == 0 else "maybe"

    for number in range(12):
        before = len(asked)
        try:
            run_review(ov, item(number), "r-1", ask, lambda *a: None)
        except OversightError as error:
            assert "ask() must return" in str(error)
        submitted = len(deployment.query("SELECT * FROM events WHERE type = 'submitted'"))
        assert len(asked) > before and len(asked) == submitted


@pytest.mark.parametrize("answer", [None, "yes", ("edit",), ("reject", {}), 1])
def test_a_bad_answer_is_refused(ov, answer):
    with pytest.raises(OversightError, match="ask"):
        run_review(ov, item(0), "r-1", lambda p: answer, lambda *a: None)


def test_run_review_passes_context(deployment, ov):
    run_review(ov, item(0), "r-1", lambda p: "reject", None, context={"fields": ["product"]})
    rows = deployment.query("SELECT shown_fields FROM review_context", planted=True)
    assert json.loads(rows[0]["shown_fields"]) == ["product"]


# ----------------------------------------------------------------- the decorator


def test_the_decorator_sends_every_ticket_to_the_queue(deployment, ov):
    review_queue = []

    @ov.gate(
        enqueue=review_queue.append,
        target_id=lambda number: f"rec-{number}",
        reviewer=lambda number: "r-7",
    )
    def triage(number):
        """Propose a triage."""
        return ITEMS[number]

    for number in range(12):
        assert triage(number) is None
    assert len(review_queue) == len(deployment.query("SELECT * FROM items")) > 12
    assert triage.__name__ == "triage" and triage.__doc__ == "Propose a triage."
    assigned = deployment.query(
        "SELECT assigned_reviewer FROM item_registry WHERE kind = 'planted'", planted=True
    )
    assert {r["assigned_reviewer"] for r in assigned} == {"r-7"}


def test_the_decorator_without_a_reviewer(deployment):
    ov = deployment.open()
    queue = []
    ov.gate(enqueue=queue.append, target_id=lambda: "rec")(lambda: ITEMS[0])()
    assert [t.payload for t in queue] == [ITEMS[0]]


def test_a_failing_proposal_submits_nothing(deployment):
    ov = deployment.open()

    def broken():
        raise RuntimeError("model down")

    with pytest.raises(RuntimeError):
        ov.gate(enqueue=lambda t: None, target_id=lambda: "rec")(broken)()
    assert deployment.query("SELECT * FROM items") == []


def test_a_proposal_must_still_be_a_mapping(deployment):
    ov = deployment.open()
    wrapped = ov.gate(enqueue=lambda t: None, target_id=lambda: "rec")(lambda: "text")
    with pytest.raises(TypeError):
        wrapped()


def test_adapters_do_not_import_the_api_at_run_time():
    source = Path("src/reviewproof/adapters/plain.py")
    if source.is_file():
        assert "if TYPE_CHECKING:\n    from reviewproof.api" in source.read_text("utf-8")
    assert Item
