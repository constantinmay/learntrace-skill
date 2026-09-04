"""CLI launcher for the loopback-only LearnTrace web server."""

from __future__ import annotations

import socket
import threading
import webbrowser
from pathlib import Path

import uvicorn

from learntrace.ui.app import create_app
from learntrace.ui.config import UISettings


def _available_port(host: str) -> int:
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def run_ui(
    project: Path, *, host: str = "127.0.0.1", port: int = 0, open_browser: bool = True
) -> int:
    selected_port = port or _available_port(host)
    settings = UISettings.create(project, host=host, port=selected_port)
    app = create_app(settings)
    display_host = "[::1]" if host == "::1" else host
    base = f"http://{display_host}:{selected_port}"
    print(f"LearnTrace UI: {base}")
    print("Data remains on this machine.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(base,)).start()
    uvicorn.run(app, host=host, port=selected_port, access_log=False)
    return 0
