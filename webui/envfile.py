import json
import os
import re
import shutil
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


def env_path() -> Path:
    custom = os.getenv("CONFIG_ENV_FILE", "").strip()
    if custom:
        path = Path(custom)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    target = ROOT / "config" / ".env"
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy = ROOT / ".env"
    if not target.is_file() and legacy.is_file():
        shutil.copy2(legacy, target)
    return target


def load_env() -> dict:
    path = env_path()
    if not path.is_file():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def _dump_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\n")
    # JSON 里本身有双引号，不能再整体加引号，否则刷新后读不回来
    if "\n" in text or text.strip().startswith("#"):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def write_env(data: dict, extra: dict | None = None) -> Path:
    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_env()
    if extra:
        current.update(extra)
    current.update({k: v for k, v in data.items() if v is not None})
    tasks_raw = current.get("TASKS")
    tasks_obj = tasks_raw
    if isinstance(tasks_raw, str):
        try:
            tasks_obj = json.loads(tasks_raw)
        except json.JSONDecodeError:
            tasks_obj = []
    if isinstance(tasks_obj, list):
        valid_cookie_keys = {
            cookie_key(str(item.get("unique_id") or ""))
            for item in tasks_obj
            if item and item.get("unique_id")
        }
        current = {
            key: value
            for key, value in current.items()
            if not key.startswith("COOKIES_") or key in valid_cookie_keys
        }

    ordered_keys = [
        "PROXY_ADDRESS",
        "CRON_HOUR",
        "CRON_MINUTE",
        "CRON_SECOND",
        "TZ",
        "MESSAGE_TEMPLATE",
        "HITOKOTO_TYPES",
        "BROWSER_TIMEOUT",
        "FRIEND_LIST_WAIT_TIME",
        "TASK_RETRY_TIMES",
        "LOG_LEVEL",
        "WEB_PASSWORD",
        "GITHUB_REPO",
        "HEADLESS",
        "TASKS",
    ]
    lines = [
        "# DouYinSparkFlow 运行配置（由控制台自动生成，请勿手工折行）",
        "# JSON 必须保持单行",
        "",
    ]
    seen = set()
    for key in ordered_keys:
        if key in current:
            lines.append(f"{key}={_dump_value(current[key])}")
            seen.add(key)
    for key in sorted(current):
        if key.startswith("COOKIES_") and key not in seen:
            lines.append(f"{key}={_dump_value(current[key])}")
            seen.add(key)
    for key, value in current.items():
        if key not in seen:
            lines.append(f"{key}={_dump_value(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def cookie_key(unique_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", str(unique_id).strip())
    return f"COOKIES_{cleaned.upper()}"


def parse_accounts(env: dict) -> list:
    raw_tasks = env.get("TASKS", "[]")
    try:
        tasks = json.loads(raw_tasks) if isinstance(raw_tasks, str) else raw_tasks
    except json.JSONDecodeError:
        tasks = []
    accounts = []
    for task in tasks or []:
        unique_id = str(task.get("unique_id") or "").strip()
        cookies_raw = env.get(cookie_key(unique_id), "")
        cookies_ok = False
        parsed = None
        if cookies_raw:
            try:
                parsed = json.loads(cookies_raw) if isinstance(cookies_raw, str) else cookies_raw
                cookies_ok = isinstance(parsed, list) and len(parsed) > 0
            except json.JSONDecodeError:
                cookies_ok = False
        accounts.append(
            {
                "username": task.get("username") or "账号",
                "unique_id": unique_id,
                "targets": task.get("targets") or [],
                "cookies_set": cookies_ok,
                "cookie_count": len(parsed) if cookies_ok else 0,
            }
        )
    return accounts
