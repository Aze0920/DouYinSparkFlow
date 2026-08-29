"""续火花之后隔 11-13 小时随机做一次登录态保活。

抖音的 sessionid 是滑动过期的：带着有效登录态访问一次，服务端就会重新签发并延长
有效期，我们再把新 Cookie 覆盖回快照。所以保活只需要轻量访问一次，不必刷视频。

计时从「续火花跑完」开始，随机 11-13 小时后触发，这样一天大约两次且不会卡在固定点。
"""
from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

from utils.logger import setup_logger
from webui import safe_io

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "keepalive.json"
MIN_HOURS = 11.0
MAX_HOURS = 13.0

logger = setup_logger("app", "DEBUG")
_lock = threading.Lock()


def _load() -> dict:
    if not STATE_FILE.is_file():
        return {}
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        logger.exception("读取保活状态失败")
        return {}


def _save(data: dict) -> None:
    safe_io.write_json(STATE_FILE, data)


def _clean_ids(unique_ids) -> list[str]:
    # 先判 x 再转字符串：str(None) 会得到 "None"，那是个看着挺像账号的假 id
    return [str(x).strip() for x in (unique_ids or []) if x and str(x).strip()]


def next_delay_seconds() -> float:
    return random.uniform(MIN_HOURS, MAX_HOURS) * 3600


def schedule_after_task(unique_ids, now: float | None = None) -> dict:
    """续火花（无论成败）跑完后排下一次保活。"""
    ids = _clean_ids(unique_ids)
    if not ids:
        return {}
    now = time.time() if now is None else now
    with _lock:
        data = _load()
        for uid in ids:
            item = dict(data.get(uid) or {})
            item["last_task_at"] = now
            item["next_at"] = now + next_delay_seconds()
            data[uid] = item
        _save(data)
        return data


def ensure_scheduled(unique_ids, now: float | None = None) -> list[str]:
    """给还没有排期的账号补一次。

    计时本来从续火花跑完开始，但从没跑过任务的账号恰恰最容易过期 —— 它们没有任何
    访问在给 sessionid 续期。所以首次见到就按同样的 11-13 小时排上。
    """
    ids = _clean_ids(unique_ids)
    if not ids:
        return []
    now = time.time() if now is None else now
    added = []
    with _lock:
        data = _load()
        for uid in ids:
            if data.get(uid):
                continue
            data[uid] = {"first_seen_at": now, "next_at": now + next_delay_seconds()}
            added.append(uid)
        if added:
            _save(data)
    return added


def due_ids(now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    data = _load()
    out = []
    for uid, item in data.items():
        try:
            next_at = float((item or {}).get("next_at") or 0)
        except (TypeError, ValueError):
            continue
        if next_at and now >= next_at:
            out.append(uid)
    return out


def mark_checked(unique_id: str, ok: bool, message: str = "", now: float | None = None) -> None:
    """记录一次保活结果，并把下一次排到 11-13 小时之后。"""
    uid = str(unique_id or "").strip()
    if not uid:
        return
    now = time.time() if now is None else now
    with _lock:
        data = _load()
        item = dict(data.get(uid) or {})
        item["last_check_at"] = now
        item["last_ok"] = bool(ok)
        item["last_message"] = str(message or "")[:200]
        item["next_at"] = now + next_delay_seconds()
        data[uid] = item
        _save(data)


def forget(unique_id: str) -> None:
    uid = str(unique_id or "").strip()
    if not uid:
        return
    with _lock:
        data = _load()
        if data.pop(uid, None) is not None:
            _save(data)


def snapshot(unique_id: str = "") -> dict:
    data = _load()
    if unique_id:
        return dict(data.get(str(unique_id).strip()) or {})
    return data
