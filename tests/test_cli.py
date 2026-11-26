import pytest

from reviewproof import __version__
from reviewproof.cli.main import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"rp {__version__}"


def test_no_arguments_prints_help(capsys):
    assert main([]) == 0
    assert "usage: rp" in capsys.readouterr().out
