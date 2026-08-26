"""读取抖音网页私信里的好友 / 群聊，并尽量解析火花天数。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from core.browser import get_browser
from utils.logger import setup_logger
from webui.cookie_probe import _probe_lock
from webui.qr_login import CHAT

logger = setup_logger("app", "DEBUG")

EXTRACT_JS = """() => {
  const sels = [
    '.conversationConversationItemwrapper',
    '[class*="conversationItemwrapper"]',
    '[class*="ConversationItemwrapper"]',
    '[class*="conversation-item"]',
  ];
  let items = [];
  for (const sel of sels) {
    items = Array.from(document.querySelectorAll(sel));
    if (items.length) break;
  }
  return items.map((el) => {
    const titleEl = el.querySelector('[class*="title"],[class*="Title"],[class*="nickName"],[class*="nickname"],[class*="NickName"]');
    const rawName = (titleEl ? titleEl.innerText : el.innerText) || '';
    const name = String(rawName).split('\\n').map((x) => x.trim()).filter(Boolean)[0] || '';
    const text = String(el.innerText || '').replace(/\\s+/g, ' ').trim();
    const cls = String(el.className || '');
    const isGroup = /group|Group|群聊/.test(cls + ' ' + text) || /\\d+\\s*人/.test(text);
    return { name, kind: isGroup ? 'group' : 'friend', text, cls };
  }).filter((row) => row.name);
}"""

SCROLL_JS = """() => {
  const el = document.querySelector('.conversationConversationListwrapper, [class*="conversationListwrapper"], [class*="ConversationListwrapper"]');
  let box = el;
  if (!box) {
    const item = document.querySelector('[class*="conversationItemwrapper"], [class*="ConversationItemwrapper"]');
    box = item && item.parentElement;
  }
  if (!box) return false;
  const before = box.scrollTop;
  box.scrollTop += 720;
  return box.scrollTop !== before;
}"""

SPARK_KEY_RE = re.compile(r"(streak|spark|fire.?day|consecutive|huohua)", re.I)
SPARK_TEXT_RES = [
    re.compile(r"火花\s*[xX×]?\s*(\d{1,4})"),
    re.compile(r"连续(?:互发|互相关心|关心)?\s*(\d{1,4})\s*天"),
    re.compile(r"(\d{1,4})\s*天(?:火花|连续)"),
]


def parse_spark_days(text: str) -> int | None:
    raw = re.sub(r"\d+\s*天前", "", str(text or ""))
    raw = re.sub(r"\d+\s*小时前", "", raw)
    for pat in SPARK_TEXT_RES:
        found = pat.search(raw)
        if found:
            try:
                n = int(found.group(1))
            except (TypeError, ValueError):
                continue
            if 1 <= n <= 9999:
                return n
    return None


def _spark_from_obj(obj: Any) -> int | None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if SPARK_KEY_RE.search(str(key or "")):
                try:
                    n = int(value)
                    if 1 <= n <= 9999:
                        return n
                except (TypeError, ValueError):
                    pass
            nested = _spark_from_obj(value)
            if nested:
                return nested
    elif isinstance(obj, list):
        for item in obj:
            nested = _spark_from_obj(item)
            if nested:
                return nested
    elif isinstance(obj, str) and obj.startswith("{") and len(obj) < 8000:
        try:
            return _spark_from_obj(json.loads(obj))
        except Exception:
            return parse_spark_days(obj)
    return parse_spark_days(str(obj or ""))


NAME_KEYS = ("remark_name", "nick_name", "nickname", "display_name", "name")
NOISE_NAMES = {"私信", "消息", "搜索", "发起聊天", "没有消息", "陌生人消息"}


def _pick_name(payload: dict) -> str:
    for key in NAME_KEYS:
        val = str(payload.get(key) or "").strip()
        if val and val not in {"null", "undefined"}:
            return val
    return ""


def _nested_name(payload: dict) -> str:
    name = _pick_name(payload)
    if name:
        return name
    for key in ("user_info", "user", "participant", "conversation_core_info", "core_info", "owner"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            name = _pick_name(nested)
            if name:
                return name
    for key in ("participants", "users", "members"):
        people = payload.get(key)
        if isinstance(people, list) and people and isinstance(people[0], dict):
            name = _pick_name(people[0])
            if name:
                return name
    return ""


def harvest_api_conversations(payload: Any, out: list[dict] | None = None) -> list[dict]:
    rows = out if out is not None else []
    if isinstance(payload, dict):
        name = _nested_name(payload)
        ctype = payload.get("conversation_type")
        if ctype is None:
            ctype = payload.get("type")
        spark = _spark_from_obj(payload)
        looks_conv = any(
            key in payload
            for key in ("conversation_short_id", "conversation_id", "conversation_type", "conversation_core_info")
        )
        looks_user = bool(_pick_name(payload)) and any(key in payload for key in ("sec_uid", "short_id"))
        if name and name not in NOISE_NAMES and (looks_conv or looks_user or spark is not None):
            kind = "group" if str(ctype) in {"2", "3", "10"} else "friend"
            if "群" in name:
                kind = "group"
            rows.append({"name": name, "kind": kind, "spark_days": spark})
        for value in payload.values():
            harvest_api_conversations(value, rows)
    elif isinstance(payload, list):
        for item in payload:
            harvest_api_conversations(item, rows)
    return rows


def merge_conversations(*groups: list[dict]) -> list[dict]:
    by_name: dict[str, dict] = {}
    for group in groups:
        for item in group or []:
            name = str((item or {}).get("name") or "").strip()
            if not name:
                continue
            kind = "group" if (item or {}).get("kind") == "group" else "friend"
            spark = (item or {}).get("spark_days")
            try:
                spark = int(spark) if spark not in (None, "") else None
            except (TypeError, ValueError):
                spark = parse_spark_days(str(spark or ""))
            row = by_name.get(name) or {"name": name, "kind": kind, "spark_days": None}
            if kind == "group":
                row["kind"] = "group"
            if spark:
                current = int(row.get("spark_days") or 0)
                if spark > current:
                    row["spark_days"] = spark
            by_name[name] = row
    friends = [row for row in by_name.values() if row["kind"] != "group"]
    groups_out = [row for row in by_name.values() if row["kind"] == "group"]
    friends.sort(key=lambda x: x["name"])
    groups_out.sort(key=lambda x: x["name"])
    return friends + groups_out


def _is_im_url(url: str) -> bool:
    raw = str(url or "").lower()
    return any(token in raw for token in ("/im/", "imapi", "conversation", "im/user"))


def _row_kind(name: str, text: str, flag: str = "") -> str:
    if str(flag or "") == "group":
        return "group"
    blob = f"{name} {text}"
    if "群聊" in blob or "群" in name or re.search(r"\d+\s*人", text or ""):
        return "group"
    return "friend"


def _collect_dom(page) -> list[dict]:
    rows: list[dict] = []
    scopes = [page]
    try:
        scopes.extend(page.frames)
    except Exception:
        pass
    for scope in scopes:
        try:
            part = scope.evaluate(EXTRACT_JS) or []
        except Exception:
            continue
        for item in part:
            name = str((item or {}).get("name") or "").strip()
            if not name or name in NOISE_NAMES:
                continue
            text = str((item or {}).get("text") or "")
            kind = _row_kind(name, text, str((item or {}).get("kind") or ""))
            rows.append({"name": name, "kind": kind, "spark_days": parse_spark_days(text)})
    return rows


def _collect_from_locators(item_loc) -> list[dict]:
    from core.tasks import _item_title

    rows: list[dict] = []
    if item_loc is None:
        return rows
    try:
        elements = item_loc.all()
    except Exception:
        return rows
    for element in elements:
        try:
            name = str(_item_title(element) or "").split("\n")[0].strip()
        except Exception:
            name = ""
        if not name or name in NOISE_NAMES:
            continue
        try:
            text = str(element.inner_text(timeout=800) or "")
        except Exception:
            text = name
        rows.append({"name": name, "kind": _row_kind(name, text), "spark_days": parse_spark_days(text)})
    return rows


def list_conversations(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    if not _probe_lock.acquire(blocking=False):
        return {"ok": False, "items": [], "message": "正在检测另一个账号，请稍后再试"}
    playwright = None
    browser = None
    context = None
    api_rows: list[dict] = []
    try:
        from core.browser import make_context
        from core.tasks import (
            CONVERSATION_ITEM_SELECTORS,
            CONVERSATION_LIST_SELECTORS,
            _dump_chat_debug,
            _find_locator,
            _looks_like_login,
            _scroll_list,
            _wait_locator,
        )

        playwright, browser = get_browser()
        context = make_context(browser)
        try:
            context.add_cookies(cookies)
        except Exception:
            logger.exception("写入 Cookie 失败")
            return {"ok": False, "items": [], "message": "Cookie 格式浏览器不接受，请重新登录"}

        def on_response(response):
            url = str(getattr(response, "url", "") or "")
            if not _is_im_url(url):
                return
            try:
                data = response.json()
            except Exception:
                return
            harvest_api_conversations(data, api_rows)

        page = context.new_page()
        page.on("response", on_response)
        page.goto(CHAT, wait_until="domcontentloaded", timeout=25000)
        time.sleep(0.8)
        if _looks_like_login(page):
            _dump_chat_debug(page, "picker")
            return {"ok": False, "items": [], "message": "Cookie 已失效，请重新登录后再选好友"}

        item_loc, scope, item_sel = _wait_locator(page, CONVERSATION_ITEM_SELECTORS, timeout_ms=15000)
        list_loc, _, list_sel = _find_locator(page, CONVERSATION_LIST_SELECTORS)
        logger.info("选择好友等待会话列表 item=%s list=%s", item_sel or "无", list_sel or "无")

        items: list[dict] = []
        for _ in range(8):
            items = merge_conversations(items, _collect_from_locators(item_loc), _collect_dom(page), api_rows)
            moved = False
            try:
                moved = bool(_scroll_list(scope, list_loc, item_loc))
            except Exception:
                try:
                    moved = bool(page.evaluate(SCROLL_JS))
                except Exception:
                    moved = False
            item_loc, scope, _ = _find_locator(page, CONVERSATION_ITEM_SELECTORS)
            list_loc, _, _ = _find_locator(page, CONVERSATION_LIST_SELECTORS)
            if items and not moved:
                break
            time.sleep(0.35)

        items = merge_conversations(items, _collect_from_locators(item_loc), _collect_dom(page), api_rows)
        logger.info("读取会话列表 count=%s api=%s url=%s", len(items), len(api_rows), getattr(page, "url", ""))
        if not items:
            _dump_chat_debug(page, "picker")
            return {
                "ok": False,
                "items": [],
                "message": "私信页没出现好友列表。Cookie 检测过首页不等于网页私信能打开，请确认这个号能打开抖音网页私信",
            }
        return {"ok": True, "items": items, "message": f"已读取 {len(items)} 个会话"}
    except Exception as exc:
        logger.exception("读取会话列表失败")
        return {"ok": False, "items": [], "message": f"读取失败：{exc}"}
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if playwright:
                playwright.stop()
        except Exception:
            pass
        _probe_lock.release()
