"""The traceability matrix and execution record (step 3.4).

The record must never be generous: a requirement is passed only when every test tagged with
it ran and passed, for the shared adapter tests once for each adapter.
"""

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "trace_matrix", ROOT / "validation-pack" / "trace_matrix.py"
)
trace = importlib.util.module_from_spec(_SPEC)
sys.modules["trace_matrix"] = trace
_SPEC.loader.exec_module(trace)


def test_every_requirement_is_tagged_and_every_tag_is_a_requirement():
    assert trace.problems(ROOT) == []


def test_the_committed_matrix_is_up_to_date():
    committed = (ROOT / trace.MATRIX).read_text(encoding="utf-8")
    assert committed == trace.matrix(ROOT), "run: python validation-pack/trace_matrix.py"


def test_five_libraries_and_the_stand_in_run_the_shared_tests():
    assert trace.adapters(ROOT) == sorted([*trace.LIBRARIES, "tests/test_adapter_tool.py"])


# ----------------------------------------------------------------- a small project


REQUIREMENTS = """| ID | The software shall |
| --- | --- |
| REQ-001 | One. |
| REQ-002 | Two. |
| REQ-003 | Shared. |
"""

PLAIN = """import pytest


@pytest.mark.req("REQ-001")
@pytest.mark.parametrize("n", [1, 2])
def test_one(n):
    pass


@pytest.mark.req("REQ-002", "REQ-001")
def test_two():
    pass


def test_untagged():
    pass
"""

SHARED = """import pytest


class AdapterConformance:
    @pytest.mark.req("REQ-003")
    def test_shared(self):
        pass
"""

ADAPTER = """from conformance import AdapterConformance


class TestAdapter{n}(AdapterConformance):
    pass
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "validation-pack").mkdir()
    (tmp_path / "validation-pack" / "requirements.md").write_text(REQUIREMENTS, "utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_plain.py").write_text(PLAIN, "utf-8")
    (tests / "conformance.py").write_text(SHARED, "utf-8")
    for n in "AB":
        (tests / f"test_adapter_{n}.py").write_text(ADAPTER.format(n=n), "utf-8")
    return tmp_path


def case(classname, name, outcome=None):
    inside = f"<{outcome} message='x'/>" if outcome else ""
    return f"<testcase classname='{classname}' name='{name}'>{inside}</testcase>"


GOOD = [
    case("tests.test_plain", "test_one[1]"),
    case("tests.test_plain", "test_one[2]"),
    case("tests.test_plain", "test_two"),
    case("tests.test_plain", "test_untagged"),
    case("tests.test_adapter_A.TestAdapterA", "test_shared"),
    case("tests.test_adapter_B.TestAdapterB", "test_shared"),
]


def judged(project, cases, tmp_path):
    report = tmp_path / "results.xml"
    report.write_text(f"<testsuites><testsuite>{''.join(cases)}</testsuite></testsuites>", "utf-8")
    results = trace.judge(project, trace.run_results([report]))
    _, complete = trace.record(project, [report])
    return {key: result for key, (result, _) in results.items()}, complete, report


def without(name):
    return [c for c in GOOD if f"name='{name}'" not in c]


def replaced(old, new):
    assert old in GOOD
    return [new if c == old else c for c in GOOD]


ALL = {"REQ-001": "Passed", "REQ-002": "Passed", "REQ-003": "Passed"}


def test_everything_passing(project, tmp_path):
    assert judged(project, GOOD, tmp_path)[:2] == (ALL, True)


@pytest.mark.parametrize(
    ("cases", "expected"),
    [
        # One input of a test failing fails every requirement tagged on it.
        (
            replaced(GOOD[1], case("tests.test_plain", "test_one[2]", "failure")),
            {"REQ-001": "FAILED"},
        ),
        (
            replaced(GOOD[2], case("tests.test_plain", "test_two", "error")),
            {"REQ-001": "FAILED", "REQ-002": "FAILED"},
        ),
        # Skipped, even for one input, is not passed.
        (
            replaced(GOOD[1], case("tests.test_plain", "test_one[2]", "skipped")),
            {"REQ-001": "Incomplete"},
        ),
        (without("test_two"), {"REQ-001": "Incomplete", "REQ-002": "Incomplete"}),
        # A shared test must run for every adapter.
        (GOOD[:-1], {"REQ-003": "Incomplete"}),
        (
            replaced(GOOD[-1], case("tests.test_adapter_B.TestAdapterB", "test_shared", "failure")),
            {"REQ-003": "FAILED"},
        ),
        ([], {"REQ-001": "Incomplete", "REQ-002": "Incomplete", "REQ-003": "Incomplete"}),
    ],
    ids=[
        "one-input-fails",
        "error",
        "skipped",
        "not-run",
        "one-adapter-missing",
        "one-adapter-fails",
        "nothing-ran",
    ],
)
def test_the_record_is_never_generous(project, tmp_path, cases, expected):
    results, complete, _ = judged(project, cases, tmp_path)
    assert results == {**ALL, **expected}
    assert not complete


def test_a_failure_in_an_untagged_test_is_not_hidden(project, tmp_path):
    cases = replaced(GOOD[3], case("tests.test_plain", "test_untagged", "failure"))
    results, complete, report = judged(project, cases, tmp_path)
    assert results == ALL and not complete
    text, _ = trace.record(project, [report])
    assert "tests/test_plain.py: test_untagged" in text
    assert "1 failed" in text


def test_the_command_writes_a_record_and_can_require_everything(project, tmp_path, capsys):
    _, _, report = judged(project, GOOD[:-1], tmp_path)
    out = tmp_path / "record.md"
    arguments = ["--results", str(report), "--out", str(out)]
    assert trace.main(arguments, root=project) == 0
    text = out.read_text("utf-8")
    assert "| REQ-003 | Incomplete | test_shared did not run in tests/test_adapter_B.py |" in text
    assert "SHA-256" in text and "**[FILL]**" in text
    assert "2 passed, 0 failed, 1 incomplete, of 3" in capsys.readouterr().out
    assert trace.main(arguments, root=project) == 2, "a record is never overwritten"
    out.unlink()
    assert trace.main([*arguments, "--require-all"], root=project) == 1


def test_check_fails_when_out_of_date_or_incomplete(project, capsys):
    assert trace.main(["--check"], root=project) == 1
    assert trace.main([], root=project) == 0
    assert trace.main(["--check"], root=project) == 0
    with (project / "tests" / "test_plain.py").open("a", encoding="utf-8") as handle:
        handle.write('\n\n@pytest.mark.req("REQ-009")\ndef test_unknown():\n    pass\n')
    assert trace.main(["--check"], root=project) == 1
    assert "REQ-009, which is not a requirement" in capsys.readouterr().err
    requirements = project / "validation-pack" / "requirements.md"
    requirements.write_text(REQUIREMENTS + "| REQ-004 | Four. |\n", "utf-8")
    assert trace.main([], root=project) == 1
    assert "REQ-004 has no test" in capsys.readouterr().err
    assert "**No test.**" in (project / trace.MATRIX).read_text("utf-8")


def test_a_tag_must_be_plain_text(project):
    (project / "tests" / "test_other.py").write_text(
        "import pytest\n\nNAME = 'REQ-001'\n\n\n@pytest.mark.req(NAME)\ndef test_x():\n    pass\n",
        "utf-8",
    )
    with pytest.raises(ValueError, match="plain text"):
        trace.tagged(project)


# ----------------------------------------------------------------- adapters in scope


def test_the_adapter_names_are_the_packages_extras():
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    block = pyproject.split("[project.optional-dependencies]")[1].split("[project.urls]")[0]
    declared = set(re.findall(r"^([\w-]+) = \[", block, flags=re.MULTILINE)) - {"dev"}
    assert set(trace.EXTRAS) == declared
    assert sorted(trace.EXTRAS.values()) == sorted(trace.LIBRARIES)


@pytest.mark.parametrize(
    ("chosen", "left_out"),
    [
        (None, set()),
        (["none"], set(trace.LIBRARIES)),
        (["langgraph"], set(trace.LIBRARIES) - {trace.EXTRAS["langgraph"]}),
        (list(trace.EXTRAS), set()),
    ],
)
def test_which_adapter_tests_are_left_out(chosen, left_out):
    assert trace.left_out(chosen) == left_out


@pytest.mark.parametrize("chosen", [[], ["nope"], ["none", "strands"]])
def test_a_bad_choice_of_adapters_is_refused(chosen, project, tmp_path):
    with pytest.raises(ValueError, match="--adapters takes"):
        trace.left_out(chosen)


def test_leaving_an_adapter_out_is_said_and_hides_no_failure(project, tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "EXTRAS", {"b": "tests/test_adapter_B.py"})
    report = tmp_path / "results.xml"
    cases = replaced(GOOD[-1], case("tests.test_adapter_B.TestAdapterB", "test_shared", "failure"))
    report.write_text(f"<testsuite>{''.join(cases)}</testsuite>", "utf-8")
    text, complete = trace.record(project, [report], ["none"])
    assert "| REQ-003 | Passed |" in text, "B is out of scope"
    assert "left out of scope, whose tests are not counted: b." in text
    assert "tests/test_adapter_B.py: test_shared" in text and not complete
    counted, _ = trace.record(project, [report])
    assert "| REQ-003 | FAILED |" in counted
    assert "All five adapters are in scope." in counted


def test_the_command_refuses_adapters_without_results(project, capsys):
    assert trace.main(["--adapters", "none"], root=project) == 2
    assert trace.main(["--adapters", "nope"], root=project) == 2
    assert "error:" in capsys.readouterr().err


# ----------------------------------------------------------------- the commit in a record


@pytest.mark.skipif(shutil.which("git") is None, reason="needs Git")
def test_the_record_says_whether_what_was_tested_had_been_changed(tmp_path):
    def git(*arguments):
        identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(
            ["git", "-C", str(tmp_path), *identity, *arguments], check=True, capture_output=True
        )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "code.py").write_text("x = 1\n", "utf-8")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    clean = trace._commit(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{40}", clean)
    # The files a run leaves behind are not changes to what was tested.
    (tmp_path / "results.xml").write_text("<testsuite/>", "utf-8")
    (tmp_path / "evidence-record.md").write_text("x", "utf-8")
    assert trace._commit(tmp_path) == clean
    for change in ("src/code.py", "src/new.py"):
        (tmp_path / change).write_text("x = 2\n", "utf-8")
        assert trace._commit(tmp_path) == f"{clean} **with uncommitted changes**"


def test_outside_a_checkout_the_commit_is_not_made_up(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert trace._commit(tmp_path).startswith("not known")


# ----------------------------------------------------------------- results as pytest writes them


def run_pytest(project, *options):
    """A real run, so that the record maker is tested against real result files."""
    (project / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\nmarkers = req(id): x\n", "utf-8"
    )
    report = project / "results.xml"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            "pytest.ini",
            "--rootdir",
            ".",
            f"--junitxml={report}",
            *options,
        ],
        cwd=project,
        capture_output=True,
        timeout=300,
        check=False,
    )
    return report


def test_a_real_run_is_read_as_it_happened(project):
    report = run_pytest(project)
    results = trace.judge(project, trace.run_results([report]))
    assert {key: result for key, (result, _) in results.items()} == ALL
    text, complete = trace.record(project, [report])
    assert complete
    assert "Test runs: 6 passed, 0 failed, 0 skipped." in text
    assert "skipped as a whole" not in text


def test_a_file_that_cannot_be_loaded_is_never_hidden(project):
    """With this option the other tests run and pass; the record must still not be complete."""
    (project / "tests" / "test_broken.py").write_text("import not_a_real_module\n", "utf-8")
    report = run_pytest(project, "--continue-on-collection-errors")
    text, complete = trace.record(project, [report])
    assert not complete
    assert "- tests/test_broken.py: (the whole file)" in text
    assert "Tests that failed" in text


def test_a_file_skipped_as_a_whole_is_named(project):
    (project / "tests" / "test_optional.py").write_text(
        "import pytest\n\npytest.importorskip('not_a_real_library')\n", "utf-8"
    )
    text, complete = trace.record(project, [run_pytest(project)])
    assert complete, "no tagged test is in that file"
    assert "none of their tests ran: tests/test_optional.py." in text
