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

# /api 下面只认这些第一段。扫描器爱编 /api/v1、/api/v2、/api/auditPublishing。
API_FIRST = {
    "me",
    "login",
    "register",
    "logout",
    "users",
    "cards",
    "invite",
    "settings",
    "recharge",
    "regions",
    "announcement",
    "notify",
    "wxpusher",
    "status",
    "github",
    "config",
    "account",
    "logs",
    "douyin",
    "run",
    "update",
    "wechat",
}

# /api/account 是真接口；/api/account/auth/form 是扫目录。
ACCOUNT_SECONDS = {
    "check",
    "conversations",
    "copy-cookies",
    "import-cookie",
}

_REQUEST_LOG_RE = re.compile(
    r"请求(?:失败|异常)? (GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD) (\S+)"
)


def is_app_path(path: str) -> bool:
    if not path:
        return False
    if path in APP_PAGES:
        return True
    if path.startswith("/static/"):
        return True
    if path == "/api":
        return False
    if not path.startswith("/api/"):
        return False
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return False
    if parts[1] not in API_FIRST:
        return False
    if parts[1] == "account":
        if len(parts) == 2:
            return True
        return parts[2] in ACCOUNT_SECONDS
    return True


def is_known_request(app, method: str, path: str) -> bool:
    """以当前 FastAPI 注册表为准：没挂上的路径一律当探针。"""
    if path in APP_PAGES or path.startswith("/static/"):
        return True
    try:
        from starlette.routing import Match
    except Exception:
        return is_app_path(path)
    scope = {"type": "http", "method": (method or "GET").upper(), "path": path}
    routes = getattr(getattr(app, "router", None), "routes", None) or []
    for route in routes:
        matcher = getattr(route, "matches", None)
        if not callable(matcher):
            continue
        try:
            match, _child = matcher(scope)
        except Exception:
            continue
        if match in (Match.FULL, Match.PARTIAL):
            return True
    return False


def is_probe_log_line(line: str) -> bool:
    match = _REQUEST_LOG_RE.search(line)
    if not match:
        return False
    return not is_app_path(match.group(2))


def filter_probe_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if not is_probe_log_line(line)]
