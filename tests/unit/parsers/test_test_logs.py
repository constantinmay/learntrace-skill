from pathlib import Path

import pytest

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
    assert result.events[0].summary == (
        "已有测试日志记录：2 个通过，1 个失败，1 个跳过，耗时 0.10 秒。"
    )
    assert result.events[0].source_refs[0].ref == "logs/pytest.log:2"
    assert result.events[1].summary == (
        "测试日志记录用例 tests/test_parser.py::test_missing 出现失败。"
    )
    assert result.events[1].source_refs[1].type is SourceType.FILE
    assert result.events[1].source_refs[1].ref == "tests/test_parser.py"
    validator = ContractValidator()
    assert all(validator.is_valid("observable_event", event.to_dict()) for event in result.events)


def test_reports_unrecognized_log(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("command started\ncommand finished\n", encoding="utf-8")

    result = parse_test_logs(tmp_path, [Path("run.log")])

    assert len(result.events) == 1
    assert "格式未识别" in result.events[0].summary
    assert result.warnings[0].code == "unsupported_test_log_format"


def test_does_not_treat_application_log_counts_or_statuses_as_test_results(
    tmp_path: Path,
) -> None:
    log = tmp_path / "application.log"
    log.write_text(
        "1 failed\n"
        "2 errors\n"
        "worker recovered from 2 errors during startup\n"
        "request 1 failed but succeeded after retry\n"
        "FAILED deployment health-check\n"
        "ERROR server.py\n"
        "ERROR cache warmup\n",
        encoding="utf-8",
    )

    result = parse_test_logs(tmp_path, [Path("application.log")])

    assert len(result.events) == 1
    assert "格式未识别" in result.events[0].summary
    assert "测试日志记录用例" not in result.events[0].summary
    assert result.warnings[0].code == "unsupported_test_log_format"


def test_parses_pytest_case_when_session_marker_is_present(tmp_path: Path) -> None:
    log = tmp_path / "truncated-pytest.log"
    log.write_text(
        "============================= test session starts =============================\n"
        "ERROR tests/test_server.py::test_startup\n",
        encoding="utf-8",
    )

    result = parse_test_logs(tmp_path, [Path("truncated-pytest.log")])

    assert result.warnings == ()
    assert len(result.events) == 1
    assert result.events[0].summary == (
        "测试日志记录用例 tests/test_server.py::test_startup 出现错误。"
    )


def test_ignores_application_counts_around_a_real_pytest_summary(tmp_path: Path) -> None:
    log = tmp_path / "mixed.log"
    log.write_text(
        "worker recovered from 2 errors during startup\n"
        "===== 1 failed, 3 passed in 0.12s =====\n"
        "request 1 failed but succeeded after retry\n",
        encoding="utf-8",
    )

    result = parse_test_logs(tmp_path, [Path("mixed.log")])

    assert result.warnings == ()
    assert len(result.events) == 1
    assert result.events[0].summary == ("已有测试日志记录：3 个通过，1 个失败，耗时 0.12 秒。")
    assert result.events[0].source_refs[0].ref == "mixed.log:2"


def test_parses_generic_junit_summary_and_error_case(tmp_path: Path) -> None:
    log = tmp_path / "maven.log"
    log.write_text(
        "ERROR tests/test_api.py::test_timeout - RuntimeError\n"
        "Tests run: 4, Failures: 1, Errors: 1, Skipped: 1\n",
        encoding="utf-8",
    )

    result = parse_test_logs(tmp_path, [Path("maven.log")])

    assert len(result.events) == 2
    assert "共运行 4 个" in result.events[0].summary
    assert "1 个错误" in result.events[0].summary
    assert result.events[1].summary.endswith("出现错误。")


def test_parses_maven_surefire_prefixed_summary_and_failed_case(tmp_path: Path) -> None:
    log = tmp_path / "maven.log"
    log.write_text(
        "[ERROR] com.example.ParserTest.parsesInput -- Time elapsed: 0.004 s "
        "<<< FAILURE!\n"
        "[ERROR] Tests run: 2, Failures: 1, Errors: 0, Skipped: 0\n",
        encoding="utf-8",
    )

    result = parse_test_logs(tmp_path, [Path("maven.log")])

    assert result.warnings == ()
    assert len(result.events) == 2
    assert "共运行 2 个" in result.events[0].summary
    assert result.events[1].summary == (
        "测试日志记录用例 com.example.ParserTest.parsesInput 出现失败。"
    )
    assert len(result.events[1].source_refs) == 1


def test_keeps_multiple_test_run_summaries(tmp_path: Path) -> None:
    log = tmp_path / "runs.log"
    log.write_text("1 passed in 0.01s\n2 passed in 0.02s\n", encoding="utf-8")

    result = parse_test_logs(tmp_path, [Path("runs.log")])

    assert len(result.events) == 2
    assert result.events[0].source_refs[0].ref == "runs.log:1"
    assert result.events[1].source_refs[0].ref == "runs.log:2"


def test_reports_empty_test_log(tmp_path: Path) -> None:
    log = tmp_path / "empty.log"
    log.write_text("\n", encoding="utf-8")

    result = parse_test_logs(tmp_path, [Path("empty.log")])

    assert result.events == ()
    assert result.warnings[0].code == "empty_test_log"


def test_reports_invalid_utf8_without_stopping_other_test_logs(tmp_path: Path) -> None:
    (tmp_path / "bad.log").write_bytes(b"\xff\xfe")
    (tmp_path / "good.log").write_text("1 passed in 0.01s\n", encoding="utf-8")

    result = parse_test_logs(tmp_path, [Path("bad.log"), Path("good.log")])

    assert len(result.events) == 1
    assert "1 个通过" in result.events[0].summary
    assert [warning.code for warning in result.warnings] == ["invalid_utf8"]


def test_reports_oversized_test_log(tmp_path: Path) -> None:
    log = tmp_path / "large.log"
    log.write_bytes(b"a" * 1_048_577)

    result = parse_test_logs(tmp_path, [Path("large.log")])

    assert result.events == ()
    assert result.warnings[0].code == "file_too_large"


@pytest.mark.parametrize(
    ("node", "expected_file_ref"),
    [
        (r"tests\test_parser.py::test_safe", "tests/test_parser.py"),
        ("../../outside/test_parser.py::test_escape", None),
        ("/outside/test_parser.py::test_absolute", None),
        (r"C:\outside\test_parser.py::test_drive", None),
        (r"\\server\share\test_parser.py::test_unc", None),
    ],
)
def test_only_adds_confirmed_project_relative_case_file_references(
    tmp_path: Path,
    node: str,
    expected_file_ref: str | None,
) -> None:
    log = tmp_path / "pytest.log"
    log.write_text(
        "============================= test session starts =============================\n"
        f"FAILED {node} - AssertionError\n",
        encoding="utf-8",
    )

    result = parse_test_logs(tmp_path, [Path("pytest.log")])

    assert len(result.events) == 1
    file_refs = [ref.ref for ref in result.events[0].source_refs if ref.type is SourceType.FILE]
    assert file_refs == ([] if expected_file_ref is None else [expected_file_ref])


def test_does_not_add_case_file_reference_through_outside_symlink(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("PRIVATE = True\n", encoding="utf-8")
    link = tests / "linked_test.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks is not permitted on this platform")
    log = project / "pytest.log"
    log.write_text(
        "============================= test session starts =============================\n"
        "FAILED tests/linked_test.py::test_private - AssertionError\n",
        encoding="utf-8",
    )

    result = parse_test_logs(project, [Path("pytest.log")])

    assert len(result.events) == 1
    assert all(ref.type is not SourceType.FILE for ref in result.events[0].source_refs)
