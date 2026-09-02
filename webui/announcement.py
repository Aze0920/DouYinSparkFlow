"""登录弹窗公告：管理员在后台写一段公告，用户登录成功后弹窗提醒。

存储在 config/announcement.json，字段极简：
- enabled：总开关，关掉就当没有公告，用户端一律不弹。
- title / content：公告标题与正文（纯文本，前端做转义 + 换行渲染，不解析 HTML）。
- version：公告版本号，取自最后一次「实质改动」的时间戳。
  用户端「今日不再提醒」按 version+日期 记在浏览器里，
  管理员一旦改了公告内容，version 变化，之前点过「今日不再提醒」的人会重新看到新公告。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from utils.logger import setup_logger
from webui import safe_io

ROOT = Path(__file__).resolve().parent.parent
ANNOUNCEMENT_FILE = ROOT / "config" / "announcement.json"
logger = setup_logger("app", "DEBUG")

MAX_TITLE = 60
MAX_CONTENT = 4000


def default_announcement() -> dict:
    return {"enabled": False, "title": "", "content": "", "version": 0}


def load_announcement() -> dict:
    data = default_announcement()
    if ANNOUNCEMENT_FILE.is_file():
        try:
            raw = json.loads(ANNOUNCEMENT_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update({k: v for k, v in raw.items() if k in data})
        except Exception:
            logger.exception("读取公告配置失败")
    data["enabled"] = bool(data.get("enabled"))
    data["title"] = str(data.get("title") or "").strip()[:MAX_TITLE]
    data["content"] = str(data.get("content") or "").strip()[:MAX_CONTENT]
    try:
        data["version"] = int(data.get("version") or 0)
    except (TypeError, ValueError):
        data["version"] = 0
    return data


def save_announcement(payload: dict | None) -> dict:
    data = load_announcement()
    payload = payload or {}
    before = (data["enabled"], data["title"], data["content"])
    if "enabled" in payload:
        data["enabled"] = bool(payload.get("enabled"))
    if "title" in payload:
        data["title"] = str(payload.get("title") or "").strip()[:MAX_TITLE]
    if "content" in payload:
        data["content"] = str(payload.get("content") or "").strip()[:MAX_CONTENT]
    after = (data["enabled"], data["title"], data["content"])
    # 只有实质内容变了（或从没有版本号）才刷新 version，
    # 否则管理员在设置页点一次「保存」不该把所有人的「今日不再提醒」清掉。
    if after != before or not data.get("version"):
        data["version"] = int(time.time())
    safe_io.write_json(ANNOUNCEMENT_FILE, data)
    return data


def announcement_active(data: dict | None = None) -> bool:
    """真正会弹给用户看的公告：开关开着且正文非空。"""
    data = data or load_announcement()
    return bool(data.get("enabled") and data.get("content"))


def public_announcement(data: dict | None = None) -> dict:
    """给已登录用户的公告视图：只有 active 时才带上标题/正文。"""
    data = data or load_announcement()
    active = announcement_active(data)
    return {
        "enabled": bool(data.get("enabled")),
        "active": active,
        "title": data.get("title") if active else "",
        "content": data.get("content") if active else "",
        "version": data.get("version") or 0,
    }


def admin_announcement(data: dict | None = None) -> dict:
    """给管理员编辑用的完整视图。"""
    data = data or load_announcement()
    return {
        "enabled": bool(data.get("enabled")),
        "title": data.get("title") or "",
        "content": data.get("content") or "",
        "version": data.get("version") or 0,
    }
