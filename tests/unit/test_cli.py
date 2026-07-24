from _pytest.capture import CaptureFixture

from learntrace import __version__
from learntrace.archive import build_parser
from learntrace.cli import main


def test_version(capsys: CaptureFixture[str]) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0

    assert capsys.readouterr().out.strip() == f"learntrace {__version__}"


def test_parser_accepts_project_dir_and_output() -> None:
    args = build_parser().parse_args(["sample-project", "--output", "learning-record.md"])
    assert args.project_dir == "sample-project"
    assert str(args.output) == "learning-record.md"
