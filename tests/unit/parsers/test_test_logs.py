from pathlib import Path

from learntrace.models import ContractValidator, SourceType
from learntrace.parsers import parse_test_logs


def test_parses_pytest_summary_and_failed_case(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "pytest.log"
    log.parent.mkdir()
    log.write_text(
        "FAILED tests/test_parser.py::test_missing - AssertionError\n"
        "====== 1 failed, 2 passed, 1 skipped in 0.10s ======\n",
        encoding="utf-8",
    )

    result = parse_test_logs(tmp_path, [Path("logs/pytest.log")])

    assert result.warnings == ()
    assert len(result.events) == 2
    assert result.events[0].summary == "已有测试日志记录：2 个通过，1 个失败，1 个跳过。"
    assert result.events[0].source_refs[0].ref == "logs/pytest.log:2"
    assert result.events[1].summary == "测试日志记录用例 tests/test_parser.py::test_missing 失败。"
    assert result.events[1].source_refs[1].type is SourceType.FILE
    assert result.events[1].source_refs[1].ref == "tests/test_parser.py"
    validator = ContractValidator()
    assert all(validator.is_valid("observable_event", event.to_dict()) for event in result.events)


def test_reports_unrecognized_log(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("command started\ncommand finished\n", encoding="utf-8")

    result = parse_test_logs(tmp_path, [Path("run.log")])

    assert result.events == ()
    assert result.warnings[0].code == "unrecognized_test_log"
