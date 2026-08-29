"""每个抖音账号一份浏览器快照（Cookie + localStorage）和会话列表缓存。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from utils.logger import setup_logger
from webui import safe_io

logger = setup_logger("app", "DEBUG")

ROOT = Path(__file__).resolve().parent.parent
SESSION_DIR = ROOT / "data" / "sessions"
CHAT_CACHE_TTL = -1


def safe_account_id(unique_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(unique_id or "").strip())[:80]


def state_path(unique_id: str) -> Path:
    return SESSION_DIR / f"{safe_account_id(unique_id)}.state.json"


def chats_path(unique_id: str) -> Path:
    return SESSION_DIR / f"{safe_account_id(unique_id)}.chats.json"


def load_state_path(unique_id: str) -> str | None:
    sid = safe_account_id(unique_id)
    if not sid:
        return None
    path = state_path(unique_id)
    try:
        if path.is_file() and path.stat().st_size > 40:
            return str(path)
    except OSError:
        return None
    return None


def save_state(context, unique_id: str) -> bool:
    sid = safe_account_id(unique_id)
    if not sid or context is None:
        return False
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    final = state_path(unique_id)
    # 快照就是这个号的登录态。直接往正式文件上写，中途出错就把能用的登录态换成半个文件，
    # 账号当场变成掉线。先写临时文件，写全了再顶上去。
    tmp = final.with_name(final.name + ".writing")
    try:
        context.storage_state(path=str(tmp))
        if tmp.stat().st_size <= 40:
            raise ValueError(f"快照内容太短（{tmp.stat().st_size} 字节），不覆盖旧快照")
        safe_io.commit(tmp, final)
        logger.info("已保存账号快照 unique_id=%s", unique_id)
        return True
    except Exception:
        logger.exception("保存账号快照失败 unique_id=%s，保留上一份快照", unique_id)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def load_chats(unique_id: str, max_age: float = CHAT_CACHE_TTL) -> dict[str, Any] | None:
    sid = safe_account_id(unique_id)
    if not sid:
        return None
    path = chats_path(unique_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return None
    try:
        at = float(data.get("at") or 0)
    except (TypeError, ValueError):
        at = 0
    if at and max_age >= 0 and (time.time() - at) > max_age:
        return None
    return data


def save_chats(unique_id: str, items: list[dict], self_avatar: str = "") -> None:
    sid = safe_account_id(unique_id)
    if not sid:
        return
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": time.time(),
        "items": items or [],
        "self_avatar": str(self_avatar or ""),
    }
    # 续火花线程在写，页面同时可能在读，非原子写会让读到的那次直接是坏 JSON
    safe_io.write_json(chats_path(unique_id), payload, indent=None)


def clear_browser_state(unique_id: str) -> None:
    path = state_path(unique_id)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def clear_account_session(unique_id: str) -> None:
    clear_browser_state(unique_id)
    path = chats_path(unique_id)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
