"""``rp qualify`` (step 3.1): the offline workflow, with files."""

import json
import re

import pytest
import yaml
from deploy import QUEUE
from qual_support import correct_items, defect_input, is_correct
from test_cli_commands import Folder

from reviewproof.cli.main import main


def rp(*arguments):
    return main([str(a) for a in arguments])


@pytest.fixture
def folder(tmp_path, capsys):
    folder = Folder(tmp_path)
    assert rp("init", *folder.databases) == 0
    files = []
    for number in range(12):
        path = tmp_path / f"q{number}.yaml"
        path.write_text(yaml.safe_dump(defect_input(number)), encoding="utf-8")
        files.append(path)
    assert rp("plant", "import", *folder.settings, *folder.planted, *files) == 0
    assert "[qualification]" in capsys.readouterr().out
    for number in range(12):
        for approver in ("qa-1", "qa-2"):
            assert (
                rp(
                    "plant",
                    "approve",
                    *folder.settings,
                    *folder.planted,
                    f"Q-{number:03d}",
                    "--approver",
                    approver,
                )
                == 0
            )
    (tmp_path / "correct.json").write_text(json.dumps(list(correct_items(8))), "utf-8")
    plan = {
        "queue": QUEUE,
        "planted_errors": [f"Q-{n:03d}" for n in range(12)],
        "correct_items": "correct.json",
        "pass": {"min_caught": 10, "max_false_rejects": 1},
    }
    (tmp_path / "plan.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
    capsys.readouterr()
    return folder


def qualify(folder, action, *arguments):
    common = [*folder.settings, *folder.databases, "--queue", QUEUE]
    return rp("qualify", action, *common, *arguments)


def start(folder, out="sheet.json", reviewer="r-1"):
    return qualify(
        folder,
        "start",
        "--reviewer",
        reviewer,
        "--plan",
        folder.path / "plan.yaml",
        "--out",
        folder.path / out,
    )


def answers_for(folder, sheet, extra=()):
    answers = [
        {"token": i["token"], "decision": "approve" if is_correct(i["payload"]) else "reject"}
        for i in sheet["items"]
    ]
    data = {
        "session_id": sheet["session_id"],
        "reviewer": sheet["reviewer"],
        "answers": [*extra, *answers],
    }
    path = folder.path / "answers.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_a_demo_reviewer_completes_a_session(folder, capsys):
    assert qualify(folder, "status", "--reviewer", "r-1") == 0
    assert "r-1: not qualified" in capsys.readouterr().out
    assert start(folder) == 0
    assert "20 items" in capsys.readouterr().out
    sheet = json.loads((folder.path / "sheet.json").read_text("utf-8"))
    assert set(sheet) == {"session_id", "reviewer", "queue", "items"}
    assert all(set(i) == {"token", "payload"} for i in sheet["items"])
    worksheet = (folder.path / "sheet.json").read_text("utf-8")
    assert not re.search(r"Q-\d{3}|planted|defect|min_caught", worksheet)

    assert qualify(folder, "finish", "--answers", answers_for(folder, sheet)) == 0
    out = capsys.readouterr().out
    assert "reviewer r-1: PASSED" in out
    assert "Defects caught: 12 of 12 (pass mark: at least 10)" in out
    assert "wrongly rejected or changed: 0 of 8" in out
    assert "Confidential" in out
    assert qualify(folder, "status", "--reviewer", "r-1") == 0
    assert "r-1: qualified" in capsys.readouterr().out


def test_a_corrected_answers_file_can_be_run_again(folder, capsys):
    assert start(folder) == 0
    sheet = json.loads((folder.path / "sheet.json").read_text("utf-8"))
    bad = {"token": "wrong-token", "decision": "approve"}
    good_first = {"token": sheet["items"][0]["token"], "decision": "approve"}
    path = answers_for(folder, sheet, extra=[good_first, bad])
    assert qualify(folder, "finish", "--answers", path) == 2
    assert "no session item has that token" in capsys.readouterr().err
    assert qualify(folder, "finish", "--answers", answers_for(folder, sheet)) == 0
    assert "already recorded" in capsys.readouterr().out


def test_other_refusals_stop_the_import_of_answers(folder, capsys):
    assert start(folder) == 0
    sheet = json.loads((folder.path / "sheet.json").read_text("utf-8"))
    data = yaml.safe_load(answers_for(folder, sheet).read_text("utf-8"))
    data["reviewer"] = "someone-else"
    (folder.path / "answers.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    assert qualify(folder, "finish", "--answers", folder.path / "answers.yaml") == 2
    assert "another reviewer" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    [
        "- 1",
        "session_id: a\nreviewer: r\nanswers: text",
        "session_id: a\nreviewer: r\nanswers: [text]",
        "session_id: a\nreviewer: r\nanswers: [{token: t}]",
        "session_id: a\nreviewer: r\nanswers: [{token: t, decision: approve, other: 1}]",
        "answers: []",
    ],
)
def test_bad_answers_files(folder, content, capsys):
    path = folder.path / "answers.yaml"
    path.write_text(content, encoding="utf-8")
    assert qualify(folder, "finish", "--answers", path) == 2
    assert "error:" in capsys.readouterr().err


def test_abandon(folder, capsys):
    assert start(folder) == 0
    sheet = json.loads((folder.path / "sheet.json").read_text("utf-8"))
    capsys.readouterr()
    assert qualify(folder, "abandon", sheet["session_id"]) == 0
    assert "ABANDONED" in capsys.readouterr().out
    assert qualify(folder, "abandon", sheet["session_id"]) == 2


def test_a_refused_start_leaves_no_worksheet(folder, capsys):
    assert start(folder) == 0
    assert start(folder, out="second.json") == 2
    assert "unfinished session" in capsys.readouterr().err
    assert not (folder.path / "second.json").exists()


def test_a_worksheet_is_never_overwritten(folder, capsys):
    (folder.path / "sheet.json").write_text("keep", encoding="utf-8")
    assert start(folder) == 2
    assert (folder.path / "sheet.json").read_text("utf-8") == "keep"
    assert start(folder, out="other.json") == 0  # no session was started by the refusal


def test_verify_covers_qualification(folder, capsys):
    assert start(folder) == 0
    capsys.readouterr()
    assert rp("verify", *folder.databases) == 0
    assert "Item registry: matches the log." in capsys.readouterr().out
