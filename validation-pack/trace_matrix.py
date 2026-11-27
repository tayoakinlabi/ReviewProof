"""Trace each requirement to the tests tagged with it, and record the results of a test run.

Run from the root of the source, with nothing but Python:

    python validation-pack/trace_matrix.py             write validation-pack/trace-matrix.md
    python validation-pack/trace_matrix.py --check     fail if it is out of date or incomplete
    python validation-pack/trace_matrix.py --results results.xml --out record.md
    ... --results results.xml --out record.md --adapters langgraph
    ... --results results.xml --out record.md --adapters none

For a record, first run ``pytest --junitxml=results.xml``, then this script **in the same
environment**, because it notes the environment it runs in. A requirement is recorded as passed
only if every test tagged with it ran and passed. With ``--require-all``, the exit status is 1
unless every requirement passed and nothing in the run failed.

If you use only some adapters, or none, name them with ``--adapters``. The others' tests are then
not counted, and the record says which were left out. A failed test is listed whatever is left
out.

Tests are tagged ``@pytest.mark.req("REQ-001", ...)``. The tags are read from the source, so
the matrix is the same whichever optional libraries are installed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import platform
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = "validation-pack/trace-matrix.md"
SHARED = "tests/conformance.py"
SHARED_CLASS = "AdapterConformance"
#: Test files that need an optional library, and its name.
LIBRARIES = {
    "tests/test_adapter_langgraph.py": ("LangGraph", "langgraph"),
    "tests/test_adapter_openai_agents.py": ("OpenAI Agents SDK", "openai-agents"),
    "tests/test_adapter_pydantic_ai.py": ("Pydantic AI", "pydantic-ai-slim"),
    "tests/test_adapter_strands.py": ("Strands Agents", "strands-agents"),
    "tests/test_adapter_agent_framework.py": (
        "Microsoft Agent Framework",
        "agent-framework-core",
    ),
}
#: A change to any of these means the run was not of the commit named in the record.
TESTED_PATHS = ("src", "tests", "examples", "docs", "validation-pack", "pyproject.toml")
#: The names --adapters takes: each adapter's optional extra.
EXTRAS = {
    "langgraph": "tests/test_adapter_langgraph.py",
    "openai-agents": "tests/test_adapter_openai_agents.py",
    "pydantic-ai": "tests/test_adapter_pydantic_ai.py",
    "strands": "tests/test_adapter_strands.py",
    "agent-framework": "tests/test_adapter_agent_framework.py",
}
PASSED, FAILED, INCOMPLETE = "Passed", "FAILED", "Incomplete"
#: Stands for the test name when pytest reports on a file as a whole.
WHOLE_FILE = "(the whole file)"


@dataclass(frozen=True, order=True)
class Test:
    path: str
    name: str


def requirements(root: Path = ROOT) -> dict[str, str]:
    text = (root / "validation-pack" / "requirements.md").read_text(encoding="utf-8")
    return dict(re.findall(r"^\| (REQ-\d{3}) \| (.*?) \|$", text, flags=re.MULTILINE))


def _tags(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    found = []
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "pytest.mark.req":
            for argument in decorator.args:
                if not (isinstance(argument, ast.Constant) and isinstance(argument.value, str)):
                    raise ValueError(f"line {decorator.lineno}: a req tag must be plain text")
                found.append(argument.value)
    return found


def _parsed(root: Path) -> Iterable[tuple[str, ast.Module]]:
    for path in sorted((root / "tests").rglob("*.py")):
        yield path.relative_to(root).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def tagged(root: Path = ROOT) -> dict[str, list[Test]]:
    """Requirement ID to the tests tagged with it."""
    found: dict[str, set[Test]] = {}
    for path, tree in _parsed(root):
        owners = [tree, *(n for n in tree.body if isinstance(n, ast.ClassDef))]
        for owner in owners:
            for node in owner.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for requirement in _tags(node):
                        found.setdefault(requirement, set()).add(Test(path, node.name))
    return {requirement: sorted(tests) for requirement, tests in found.items()}


def adapters(root: Path = ROOT) -> list[str]:
    """The test files that run the shared adapter tests, each in its own class."""
    return [
        path
        for path, tree in _parsed(root)
        if any(
            isinstance(node, ast.ClassDef)
            and SHARED_CLASS in [ast.unparse(base) for base in node.bases]
            for node in tree.body
        )
    ]


def problems(root: Path = ROOT) -> list[str]:
    known, found = requirements(root), tagged(root)
    listed = [f"{r} has no test" for r in known if r not in found]
    listed += [f"a test is tagged {r}, which is not a requirement" for r in found if r not in known]
    return listed


def matrix(root: Path = ROOT) -> str:
    known, found = requirements(root), tagged(root)
    shared = len(adapters(root))
    lines = [
        "# Traceability matrix",
        "",
        "**Generated from the tests by `trace_matrix.py`. Do not edit.** Read the",
        "[requirements](requirements.md) with it.",
        "",
        "It shows which tests are **tagged** with each requirement. It does not show that they",
        "passed: for that, make a record from a test run, as the",
        "[test scripts](test-scripts.md) describe.",
        "Nor does a tag show that the tests are enough for your use. The deployment duties have",
        "no tests here: they are yours to test.",
        "",
        f"{len(known)} requirements, {len({t for tests in found.values() for t in tests})} tagged "
        "tests. A test may cover several requirements, and may run with several inputs.",
        "",
    ]
    for requirement, text in known.items():
        lines += [f"## {requirement}", "", text, ""]
        by_path: dict[str, list[str]] = {}
        for test in found.get(requirement, []):
            by_path.setdefault(test.path, []).append(test.name)
        if not by_path:
            lines.append("**No test.**")
        for path, names in by_path.items():
            note = ""
            if path == SHARED:
                note = f" (shared: runs once for each of {shared} adapter test files)"
            elif path in LIBRARIES:
                note = f" (needs {LIBRARIES[path][0]})"
            lines.append(f"- [`{path}`](../{path}){note}")
            lines += [f"  - `{name}`" for name in names]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ----------------------------------------------------------------- results of a run


def run_results(reports: Sequence[Path]) -> dict[tuple[str, str], list[str]]:
    """(test file or adapter class file, test name) to the outcome of each of its runs."""
    outcomes: dict[tuple[str, str], list[str]] = {}
    for report in reports:
        for case in ElementTree.parse(report).getroot().iter("testcase"):
            classname = case.get("classname", "")
            if classname:
                parts = classname.split(".")
                if parts[-1].startswith("Test"):
                    parts = parts[:-1]
                name = case.get("name", "").split("[")[0]
            else:
                # pytest reports a whole file that was skipped, or could not be loaded, as
                # one case with no class, named after the file.
                parts, name = case.get("name", "").split("."), WHOLE_FILE
            if parts[0] != "tests":
                parts = ["tests", *parts]
            kinds = {child.tag for child in case}
            if kinds & {"failure", "error"}:
                outcome = "failed"
            elif "skipped" in kinds:
                outcome = "skipped"
            else:
                outcome = "passed"
            outcomes.setdefault(("/".join(parts) + ".py", name), []).append(outcome)
    return outcomes


def _judge(test: Test, outcomes: dict, shared_by: Sequence[str]) -> tuple[str, str]:
    """(result, why) for one tagged test."""
    wanted = shared_by if test.path == SHARED else [test.path]
    missing, skipped = [], []
    for path in wanted:
        seen = outcomes.get((path, test.name), [])
        if "failed" in seen:
            return FAILED, f"{test.name} failed in {path}"
        if not seen:
            missing.append(path)
        elif "skipped" in seen:
            skipped.append(path)
    if missing:
        return INCOMPLETE, f"{test.name} did not run in {', '.join(missing)}"
    if skipped:
        return INCOMPLETE, f"{test.name} was skipped, at least in part, in {', '.join(skipped)}"
    return PASSED, ""


def left_out(chosen: Sequence[str] | None) -> set[str]:
    """The adapter test files not counted. None chooses every adapter; ["none"], no adapter."""
    if chosen is None:
        return set()
    unknown = set(chosen) - set(EXTRAS) - {"none"}
    if unknown or not chosen or ("none" in chosen and len(chosen) > 1):
        raise ValueError(f"--adapters takes 'none', or any of: {', '.join(EXTRAS)}")
    return {path for extra, path in EXTRAS.items() if extra not in chosen}


def judge(
    root: Path, outcomes: dict, excluded: set[str] | None = None
) -> dict[str, tuple[str, list[str]]]:
    excluded = excluded or set()
    found = tagged(root)
    # The stand-in is never left out: it is the shared code's own test.
    shared_by = [path for path in adapters(root) if path not in excluded]
    results = {}
    for requirement in requirements(root):
        judged = [
            _judge(test, outcomes, shared_by)
            for test in found.get(requirement, [])
            if test.path not in excluded
        ]
        states = {state for state, _ in judged}
        if FAILED in states:
            result = FAILED
        elif states == {PASSED}:
            result = PASSED
        else:
            result = INCOMPLETE
        reasons = [why for _, why in judged if why] or ([] if judged else ["no test is tagged"])
        results[requirement] = (result, reasons)
    return results


def _commit(root: Path) -> str:
    def git(*arguments: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(root), *arguments], capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        # Only what the tests depend on: the results file itself is a new file here.
        changed = git("status", "--porcelain", "--", *TESTED_PATHS)
    except (OSError, subprocess.CalledProcessError):
        return "not known (this is not a Git checkout)"
    return commit + (" **with uncommitted changes**" if changed else "")


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def record(
    root: Path, reports: Sequence[Path], chosen: Sequence[str] | None = None
) -> tuple[str, bool]:
    """The record of a run, and whether everything in it passed."""
    excluded = left_out(chosen)
    outcomes = run_results(reports)
    results = judge(root, outcomes, excluded)
    scope = "All five adapters are in scope."
    if excluded:
        names = sorted(extra for extra, path in EXTRAS.items() if path in excluded)
        scope = (
            f"**Adapters left out of scope, whose tests are not counted: {', '.join(names)}.** "
            "Nothing here says anything about them."
        )
    runs = [
        outcome for (_, name), seen in outcomes.items() if name != WHOLE_FILE for outcome in seen
    ]
    skipped_files = sorted(
        path for (path, name), seen in outcomes.items() if name == WHOLE_FILE and "skipped" in seen
    )
    failures = sorted(
        f"{path}: {name}" for (path, name), seen in outcomes.items() if "failed" in seen
    )
    counts = {state: sum(r == state for r, _ in results.values()) for state in (PASSED, FAILED)}
    incomplete = len(results) - counts[PASSED] - counts[FAILED]
    complete = counts[PASSED] == len(results) and not failures
    environment = [
        ("Record made (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        ("ReviewProof version", _version("reviewproof")),
        ("Source commit", _commit(root)),
        ("Python", platform.python_version()),
        ("Platform", platform.platform()),
        ("SQLite", sqlite3.sqlite_version),
        ("PyYAML", _version("PyYAML")),
        *((name, _version(package)) for name, package in LIBRARIES.values()),
    ]
    lines = [
        "# Test execution record",
        "",
        "Made by `trace_matrix.py`. The environment is that of the machine that made this record,",
        "which should be the one that ran the tests.",
        "",
        "| Item | Value |",
        "| --- | --- |",
        *(f"| {item} | {value} |" for item, value in environment),
        *(
            f"| Results file | `{report.name}`, SHA-256 "
            f"`{hashlib.sha256(report.read_bytes()).hexdigest()}` |"
            for report in reports
        ),
        "",
        "## Summary",
        "",
        scope,
        "",
        f"- Test runs: {runs.count('passed')} passed, {runs.count('failed')} failed, "
        f"{runs.count('skipped')} skipped.",
        f"- Requirements: {counts[PASSED]} passed, {counts[FAILED]} failed, {incomplete} "
        f"incomplete, of {len(results)}.",
        "",
    ]
    if skipped_files:
        lines += [
            f"Test files skipped as a whole, so none of their tests ran: "
            f"{', '.join(skipped_files)}.",
            "",
        ]
    if failures:
        lines += ["**Tests that failed, whether or not they are tagged:**", ""]
        lines += [f"- {failure}" for failure in failures] + [""]
    lines += [
        "**Passed** means that every test tagged with the requirement ran and passed here. It",
        "does not mean the requirement is enough for your use, and says nothing about your",
        "deployment duties. **Incomplete** means that a tagged test did not run or was skipped.",
        "",
        "## Results",
        "",
        "| Requirement | Result | Why, if not passed |",
        "| --- | --- | --- |",
        *(
            f"| {requirement} | {result} | {'; '.join(reasons)} |"
            for requirement, (result, reasons) in results.items()
        ),
        "",
        "## Assessment",
        "",
        "| | |",
        "| --- | --- |",
        "| Deviations found, and how each was resolved or justified | **[FILL]** |",
        "| Conclusion for the intended use | **[FILL]** |",
        "| Tests run by, and date | **[FILL]** |",
        "| Reviewed and approved by, and date | **[FILL]** |",
    ]
    return "\n".join(lines) + "\n", complete


def main(arguments: Sequence[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if out of date or incomplete")
    parser.add_argument("--results", nargs="+", type=Path, help="JUnit XML files of one test run")
    parser.add_argument("--out", type=Path, help="where to write the record")
    parser.add_argument("--require-all", action="store_true", help="exit 1 unless all passed")
    parser.add_argument(
        "--adapters", nargs="+", help="the adapters you use, or 'none' (default: all)"
    )
    options = parser.parse_args(arguments)
    try:
        left_out(options.adapters)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if options.adapters and not options.results:
        print("error: --adapters applies to a record: give --results too", file=sys.stderr)
        return 2

    if options.results:
        if options.out is None or options.out.exists():
            print("error: give --out a new file for the record", file=sys.stderr)
            return 2
        text, complete = record(root, options.results, options.adapters)
        options.out.write_text(text, encoding="utf-8", newline="\n")
        print(next(line for line in text.splitlines() if line.startswith("- Requirements:")))
        return 1 if options.require_all and not complete else 0

    found = problems(root)
    for problem in found:
        print(f"error: {problem}", file=sys.stderr)
    target = root / MATRIX
    if options.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != matrix(root):
            print(
                f"error: {MATRIX} is out of date. Run: python validation-pack/trace_matrix.py",
                file=sys.stderr,
            )
            return 1
    else:
        target.write_text(matrix(root), encoding="utf-8", newline="\n")
        print(f"wrote {MATRIX}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
