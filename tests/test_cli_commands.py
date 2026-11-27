"""The ``rp`` commands (step 2.6)."""

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone

import golden_log
import pytest
from deploy import DEMO, EXAMPLES, ITEMS, QUEUE
from helpers import triggers_removed
from test_alerts import Receiver

from reviewproof.cli import common
from reviewproof.cli.main import build_parser, main
from reviewproof.store import open_oversight_db
from reviewproof.strictload import load_file


class Folder:
    def __init__(self, path):
        self.path = path
        for name in ("reviewproof.yaml", "item.schema.json", "items.json"):
            shutil.copy(DEMO / name, path / name)
        self.settings = ["--config", str(path / "reviewproof.yaml")]
        self.databases = [
            "--db",
            str(path / "oversight.db"),
            "--planted",
            str(path / "planted.db"),
        ]
        self.planted = ["--planted", str(path / "planted.db")]


@pytest.fixture
def folder(tmp_path):
    return Folder(tmp_path)


def rp(*arguments):
    return main([str(a) for a in arguments])


@pytest.fixture
def ready(folder, capsys):
    assert rp("init", *folder.databases) == 0
    capsys.readouterr()
    return folder


def plant(folder, *arguments):
    action, rest = arguments[0], arguments[1:]
    where = folder.settings + ([] if action == "draft" else folder.planted)
    return rp("plant", action, *where, *rest)


# ----------------------------------------------------------------- init


def test_init(folder, capsys):
    assert rp("init", *folder.databases) == 0
    assert "Ready" in capsys.readouterr().out
    assert rp("init", *folder.databases) == 0  # repeatable
    (folder.path / "planted.db").unlink()
    assert rp("init", *folder.databases) == 2
    assert "only one of the two" in capsys.readouterr().err


# ----------------------------------------------------------------- plant


def test_import_approve_list_retire(ready, capsys):
    files = [str(p) for p in EXAMPLES[:2]]
    assert plant(ready, "import", *files) == 0
    assert capsys.readouterr().out.count("awaiting approval  (0 of 2 approvals)") == 2

    assert plant(ready, "approve", "DEMO-OM-1", "--approver", "qa-1") == 0
    assert "awaiting second approval  (1 of 2" in capsys.readouterr().out
    assert plant(ready, "approve", "DEMO-OM-1", "--approver", "qa-1") == 2
    assert "already approved" in capsys.readouterr().err
    assert plant(ready, "approve", "DEMO-OM-1", "--approver", "qa-2") == 0
    capsys.readouterr()

    assert plant(ready, "list", "--status", "approved") == 0
    out = capsys.readouterr().out
    assert "DEMO-OM-1" in out and "1 planted error(s)." in out
    assert "DEV-2026" not in out  # no content

    assert plant(ready, "retire", "DEMO-OM-1", "--by", "qa-1", "--reason", "spotted") == 0
    assert "retired" in capsys.readouterr().out
    assert plant(ready, "list", "--queue", QUEUE) == 0
    assert "2 planted error(s)." in capsys.readouterr().out


def test_import_reports_each_refusal_and_keeps_the_good_ones(ready, tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("planted_error_id: X\n", encoding="utf-8")
    assert plant(ready, "import", str(bad), str(EXAMPLES[0])) == 2
    captured = capsys.readouterr()
    assert "refused:" in captured.err and "missing key" in captured.err
    assert "imported DEMO-OM-1" in captured.out


def test_list_shows_what_is_held_back(ready, capsys):
    assert plant(ready, "import", str(EXAMPLES[0])) == 0
    for approver in ("qa-1", "qa-2"):
        assert plant(ready, "approve", "DEMO-OM-1", "--approver", approver) == 0
    (ready.path / "item.schema.json").write_text('{"required": ["site"]}', encoding="utf-8")
    capsys.readouterr()
    assert plant(ready, "list") == 0
    assert "held back:" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("options", "field", "expected"),
    [
        (["--mutator", "value-swap", "--new-value", "LOT-Z99999"], "lot_number", "LOT-Z99999"),
        (["--mutator", "value-swap", "--new-value", "true"], "capa_required", True),
        (["--mutator", "omission", "--remove-index", "0"], "immediate_actions", None),
        (["--mutator", "classification-flip"], "classification", "major"),
        (["--mutator", "classification-flip", "--to", "critical"], "classification", "critical"),
    ],
)
def test_draft(folder, options, field, expected, capsys):
    out = folder.path / "draft.yaml"
    code = plant(
        folder,
        "draft",
        "--queue",
        QUEUE,
        "--from",
        folder.path / "items.json",
        *(["--item", "0"] if field != "classification" or "--to" in options else []),
        *([] if field != "classification" or "--to" in options else ["--item", "1"]),
        "--field",
        field,
        "--id",
        "PE-1",
        "--author",
        "me",
        "--out",
        out,
        *options,
    )
    assert code == 0, capsys.readouterr().err
    draft = load_file(out)
    if expected is None:
        assert len(draft["payload"][field]) == len(ITEMS[0][field]) - 1
    elif field == "classification" and "--to" not in options:
        assert draft["payload"][field] != ITEMS[1][field]
    else:
        assert draft["payload"][field] == expected
    assert "checklist" in capsys.readouterr().out


def draft_arguments(folder, **changes):
    values = {
        "--queue": QUEUE,
        "--from": folder.path / "items.json",
        "--mutator": "value-swap",
        "--field": "lot_number",
        "--new-value": "LOT-Z99999",
        "--id": "PE-1",
        "--author": "me",
        "--out": folder.path / "d.yaml",
        **changes,
    }
    return [str(part) for k, v in values.items() if v is not None for part in (k, v)]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"--queue": "nope"}, "not in the settings"),
        ({"--item": 99}, "--item must be"),
        ({"--new-value": None}, "needs --new-value"),
        ({"--field": "nope"}, "not a property"),
        ({"--from": "absent.json"}, "cannot read"),
    ],
)
def test_draft_refusals(folder, changes, message, capsys):
    assert plant(folder, "draft", *draft_arguments(folder, **changes)) == 2
    assert message in capsys.readouterr().err


def test_draft_from_a_single_item_and_from_nonsense(folder, capsys):
    single = folder.path / "one.json"
    single.write_text(json.dumps(ITEMS[0]), encoding="utf-8")
    assert plant(folder, "draft", *draft_arguments(folder, **{"--from": single})) == 0
    single.write_text('"text"', encoding="utf-8")
    arguments = draft_arguments(folder, **{"--from": single, "--out": folder.path / "e.yaml"})
    assert plant(folder, "draft", *arguments) == 2


def test_plant_without_databases(folder, capsys):
    assert plant(folder, "list") == 2
    assert "error:" in capsys.readouterr().err


# ----------------------------------------------------------------- system


def test_system_register(ready, capsys):
    arguments = [*ready.settings, *ready.databases, "--queue", QUEUE]
    assert rp("system", "register", *arguments, "--model-version", "2.0") == 0
    out = capsys.readouterr().out
    assert "0 reviewer(s) flagged" in out and "update" in out
    assert rp("system", "register", *arguments) == 2
    assert "nothing that identifies" in capsys.readouterr().err


def test_system_register_lists_flagged_reviewers(ready, capsys):
    from reviewproof import Item, Oversight

    config = ready.path / "reviewproof.yaml"
    with Oversight(config, QUEUE, ready.path / "oversight.db", ready.path / "planted.db") as ov:
        for ticket in ov.submit(Item(payload=ITEMS[0], target_id="rec")):
            ov.shown(ticket.token, "r-7")
    arguments = [*ready.settings, *ready.databases, "--queue", QUEUE]
    assert rp("system", "register", *arguments, "--prompt-version", "2") == 0
    assert "  r-7" in capsys.readouterr().out


# ----------------------------------------------------------------- report and alerts


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    return golden_log.build(tmp_path_factory.mktemp("golden-cli"))


def evidence(golden):
    return [
        "--config",
        golden.db.parent / "reviewproof.yaml",
        "--db",
        golden.db,
        "--planted",
        golden.planted_db,
    ]


def test_report_to_a_file_and_to_the_screen(golden, tmp_path, capsysbinary):
    out = tmp_path / "report.html"
    assert rp("report", *evidence(golden), "--out", out) == 0
    assert "Review oversight report" in out.read_text(encoding="utf-8")
    capsysbinary.readouterr()
    assert rp("report", *evidence(golden), "--format", "csv") == 0
    assert capsysbinary.readouterr().out.startswith(b"scope_type,")


def test_report_options(golden, tmp_path, capsysbinary):
    head = tmp_path / "head.json"
    assert rp("verify", "--db", golden.db, "--export-head", head) == 0
    capsysbinary.readouterr()
    options = ["--individuals", "--queue", QUEUE, "--since", "2026-03-01"]
    assert rp("report", *evidence(golden), *options, "--expect-head", head) == 0
    page = capsysbinary.readouterr().out.decode("utf-8")
    assert "Individual reviewers" in page and "matches the head hash" in page


def test_report_when_text_output_has_no_buffer(golden, monkeypatch):
    import io

    screen = io.StringIO()
    monkeypatch.setattr("sys.stdout", screen)
    assert rp("report", *evidence(golden), "--format", "csv") == 0
    assert screen.getvalue().startswith("scope_type,")


@pytest.mark.parametrize("arguments", [["--queue", "nope"], ["--expect-head", "absent.json"]])
def test_report_errors(golden, arguments, capsys):
    assert rp("report", *evidence(golden), *arguments) == 2
    assert "error:" in capsys.readouterr().err


def test_report_without_settings(golden, tmp_path, capsys):
    assert rp("report", "--config", tmp_path / "none.yaml") == 2
    assert "error:" in capsys.readouterr().err


def tampered_copy(golden, tmp_path):
    for name in ("oversight.db", "planted.db", "reviewproof.yaml", "item.schema.json"):
        shutil.copy(golden.db.parent / name, tmp_path / name)
    db = open_oversight_db(tmp_path / "oversight.db")
    with triggers_removed(db):
        db.execute("UPDATE events SET decision = 'reject' WHERE type = 'decided'")
    db.close()
    return [
        "--config",
        tmp_path / "reviewproof.yaml",
        "--db",
        tmp_path / "oversight.db",
        "--planted",
        tmp_path / "planted.db",
    ]


def test_a_failed_check_replaces_the_report_rather_than_leaving_an_old_one(
    golden, tmp_path, capsys
):
    arguments = tampered_copy(golden, tmp_path)
    out = tmp_path / "weekly.html"
    out.write_text("last week's good report", encoding="utf-8")
    assert rp("report", *arguments, "--out", out) == 1
    page = out.read_text(encoding="utf-8")
    assert "no results are shown" in page and "last week" not in page
    assert "FAILED" in capsys.readouterr().err
    assert rp("report", *arguments, "--format", "csv", "--out", tmp_path / "x.csv") == 1
    assert not (tmp_path / "x.csv").exists()


def test_alerts_without_sending(golden, capsys):
    assert rp("alerts", *evidence(golden)) == 0
    out = capsys.readouterr().out
    assert "2 items need attention in queue deviation-triage" in out


def settings_with_webhook(golden, tmp_path, url, **limits):
    for name in ("oversight.db", "planted.db", "reviewproof.yaml", "item.schema.json"):
        shutil.copy(golden.db.parent / name, tmp_path / name)
    text = (tmp_path / "reviewproof.yaml").read_text(encoding="utf-8")
    extra = f"alerts:\n  qa_lead:\n    webhook: '{url}'\n" if url else ""
    if limits:
        extra += "reporting:\n" + "".join(f"  {k}: {v}\n" for k, v in limits.items())
    (tmp_path / "reviewproof.yaml").write_text(text + extra, encoding="utf-8")
    return [
        "--config",
        tmp_path / "reviewproof.yaml",
        "--db",
        tmp_path / "oversight.db",
        "--planted",
        tmp_path / "planted.db",
    ]


def test_a_queue_below_its_threshold_fires_exactly_one_alert(golden, tmp_path, no_proxy, capsys):
    server = Receiver()
    try:
        # Speed is allowed here, so only the catch rate is outside its limit.
        arguments = settings_with_webhook(golden, tmp_path, server.url, fast_approval_share_max=1.0)
        assert rp("alerts", *arguments, "--send") == 0
        [(_, _, body)] = server.requests
        sent = json.loads(body)
        assert [a["rule"] for a in sent["alerts"]] == ["catch_rate"]
        assert sent["queue"] == QUEUE
        assert "r-" not in body.decode() and "PE-" not in body.decode()
    finally:
        server.stop()
    assert "Sent by webhook." in capsys.readouterr().err


def test_nothing_is_sent_when_nothing_is_wrong(golden, tmp_path, no_proxy, capsys):
    server = Receiver()
    try:
        arguments = settings_with_webhook(
            golden, tmp_path, server.url, fast_approval_share_max=1.0, catch_rate_min=0.1
        )
        assert rp("alerts", *arguments, "--send") == 0
        assert server.requests == []
    finally:
        server.stop()
    assert "nothing outside its limit (" in capsys.readouterr().out


def test_an_alert_that_cannot_be_sent(golden, tmp_path, capsys):
    arguments = settings_with_webhook(golden, tmp_path, None)
    assert rp("alerts", *arguments, "--send") == 3
    assert "no alert route" in capsys.readouterr().err


def test_failed_evidence_alerts_the_qa_lead(golden, tmp_path, no_proxy, capsys):
    server = Receiver()
    try:
        arguments = tampered_copy(golden, tmp_path)
        text = (tmp_path / "reviewproof.yaml").read_text(encoding="utf-8")
        (tmp_path / "reviewproof.yaml").write_text(
            text + f"alerts:\n  qa_lead:\n    webhook: '{server.url}'\n", encoding="utf-8"
        )
        assert rp("alerts", *arguments) == 1
        assert server.requests == []  # only with --send
        assert rp("alerts", *arguments, "--send") == 1
        [(_, _, body)] = server.requests
        assert json.loads(body)["alerts"][0]["rule"] == "evidence"
    finally:
        server.stop()


def test_failed_evidence_with_nowhere_to_send_it(golden, tmp_path, capsys):
    assert rp("alerts", *tampered_copy(golden, tmp_path), "--send") == 3


def test_alerts_that_cannot_run(tmp_path, capsys):
    assert rp("alerts", "--config", tmp_path / "none.yaml") == 2


# ----------------------------------------------------------------- periods


NOW = datetime(2026, 3, 10, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30d", NOW - timedelta(days=30)),
        ("12h", NOW - timedelta(hours=12)),
        ("4w", NOW - timedelta(weeks=4)),
        ("2026-03-01", datetime(2026, 3, 1, tzinfo=timezone.utc)),
        ("2026-03-01T10:00:00+02:00", datetime(2026, 3, 1, 8, tzinfo=timezone.utc)),
    ],
)
def test_periods(text, expected):
    assert common.since(text, NOW) == expected


@pytest.mark.parametrize("text", ["30", "30m", "last week", "-1d", "999999999999d"])
def test_bad_periods(text):
    with pytest.raises(argparse.ArgumentTypeError, match="not a period"):
        common.since(text, NOW)


# ----------------------------------------------------------------- documentation


def commands(parser, path=()):
    """Every command and subcommand with its options."""
    subparsers = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    options = [o for a in parser._actions for o in a.option_strings if o not in ("-h", "--help")]
    if not subparsers:
        yield path, options
        return
    for name, child in subparsers[0].choices.items():
        yield from commands(child, (*path, name))


def test_every_command_and_option_is_documented():
    text = (DEMO.parents[1] / "docs" / "cli.md").read_text(encoding="utf-8")
    sections = {block.splitlines()[0].strip(): block for block in text.split("\n## ")[1:]}
    found = list(commands(build_parser()))
    assert len(found) == 10
    for path, options in found:
        title = f"`rp {' '.join(path)}`"
        assert title in sections, title
        for option in options:
            assert f"`{option}" in sections[title], f"{title}: {option}"


@pytest.mark.parametrize("path", [p for p, _ in commands(build_parser())], ids=" ".join)
def test_every_command_has_help(path, capsys):
    with pytest.raises(SystemExit) as stopped:
        main([*path, "--help"])
    assert stopped.value.code == 0
    out = capsys.readouterr().out
    assert "usage: rp" in out and "Exit status" in out
