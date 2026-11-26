"""The integration guide's code must run, and its links must work."""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parents[1]
GUIDE = ROOT / "docs" / "guides" / "integration.md"


def test_the_python_in_the_integration_guide_runs(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    text = GUIDE.read_text(encoding="utf-8")
    snippets = re.findall(r"^```python\n(.*?)^```$", text, flags=re.MULTILINE | re.DOTALL)
    assert len(snippets) == 2
    namespace = {}
    for snippet in snippets:
        exec(compile(snippet, str(GUIDE), "exec"), namespace)  # noqa: S102
    assert namespace["written"] == ["QMS-301"]


def test_the_integration_guide_links_work():
    links = re.findall(r"\]\((?!https?://|#)([^)]+)\)", GUIDE.read_text(encoding="utf-8"))
    assert links
    for link in links:
        assert (GUIDE.parent / link).exists(), link
