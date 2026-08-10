from _pytest.capture import CaptureFixture

from learntrace import __version__
from learntrace.cli import main


def test_version(capsys: CaptureFixture[str]) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0

    assert capsys.readouterr().out.strip() == f"learntrace {__version__}"
