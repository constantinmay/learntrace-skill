from pathlib import Path


def execution_probe() -> None:
    """若被错误执行会留下标记；静态解析流程不得调用本函数。"""
    Path("execution-marker.txt").write_text("executed", encoding="utf-8")
