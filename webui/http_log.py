"""把互联网扫描器和本站真实请求分开，避免运行日志被 404 探针淹没。"""

from __future__ import annotations

import re

APP_PAGES = {
    "/",
    "/home",
    "/tasks",
    "/logs",
    "/users",
    "/cards",
    "/settings",
    "/invite",
    "/accounts",
    "/mine",
    "/terms",
    "/privacy",
    "/favicon.ico",
}

_REQUEST_LOG_RE = re.compile(
    r"请求(?:失败|异常)? (GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD) (\S+)"
)


def is_app_path(path: str) -> bool:
    if not path:
        return False
    if path in APP_PAGES:
        return True
    if path == "/api" or path.startswith("/api/"):
        return True
    return path.startswith("/static/")


def is_probe_log_line(line: str) -> bool:
    match = _REQUEST_LOG_RE.search(line)
    if not match:
        return False
    return not is_app_path(match.group(2))


def filter_probe_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if not is_probe_log_line(line)]
