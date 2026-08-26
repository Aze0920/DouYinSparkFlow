"""读取抖音网页私信里的好友 / 群聊，并尽量解析火花天数。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from core.browser import get_browser
from utils.logger import setup_logger
from webui.cookie_probe import _probe_lock
from webui.qr_login import CHAT, UA

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


def harvest_api_conversations(payload: Any, out: list[dict] | None = None) -> list[dict]:
    rows = out if out is not None else []
    if isinstance(payload, dict):
        name = str(
            payload.get("name")
            or payload.get("nick_name")
            or payload.get("nickname")
            or payload.get("remark_name")
            or ""
        ).strip()
        ctype = payload.get("conversation_type")
        if ctype is None:
            ctype = payload.get("type")
        spark = _spark_from_obj(payload)
        looks_conv = any(
            key in payload
            for key in ("conversation_short_id", "conversation_id", "conversation_type", "conversation_core_info")
        )
        if name and (looks_conv or spark is not None):
            kind = "group" if str(ctype) in {"2", "3"} else "friend"
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
            if not name:
                continue
            text = str((item or {}).get("text") or "")
            kind = "group" if (item or {}).get("kind") == "group" else "friend"
            rows.append({"name": name, "kind": kind, "spark_days": parse_spark_days(text)})
    return rows


def list_conversations(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    if not _probe_lock.acquire(blocking=False):
        return {"ok": False, "items": [], "message": "正在检测另一个账号，请稍后再试"}
    playwright = None
    browser = None
    api_rows: list[dict] = []
    try:
        playwright, browser = get_browser()
        context = browser.new_context(
            user_agent=UA,
            locale="zh-CN",
            viewport={"width": 1280, "height": 860},
        )
        context.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9"})
        try:
            context.add_cookies(cookies)
        except Exception:
            logger.exception("写入 Cookie 失败")
            return {"ok": False, "items": [], "message": "Cookie 格式浏览器不接受，请重新登录"}

        def on_response(response):
            url = str(getattr(response, "url", "") or "")
            if "/im/" not in url and "conversation" not in url.lower():
                return
            try:
                data = response.json()
            except Exception:
                return
            harvest_api_conversations(data, api_rows)

        page = context.new_page()
        page.on("response", on_response)
        page.goto(CHAT, wait_until="domcontentloaded", timeout=25000)
        time.sleep(1.0)
        try:
            body = page.inner_text("body", timeout=2000) or ""
        except Exception:
            body = ""
        if any(hint in body for hint in ("扫码登录", "登录后免费畅享", "验证码登录", "打开「抖音APP」")):
            return {"ok": False, "items": [], "message": "Cookie 已失效，请重新登录后再选好友"}

        items: list[dict] = []
        for _ in range(10):
            items = merge_conversations(items, _collect_dom(page), api_rows)
            moved = False
            try:
                moved = bool(page.evaluate(SCROLL_JS))
            except Exception:
                moved = False
            if not moved and items:
                break
            time.sleep(0.4)

        items = merge_conversations(items, _collect_dom(page), api_rows)
        logger.info("读取会话列表 count=%s", len(items))
        if not items:
            return {"ok": False, "items": [], "message": "没有读到会话列表，请确认这个号能打开抖音网页私信"}
        return {"ok": True, "items": items, "message": f"已读取 {len(items)} 个会话"}
    except Exception as exc:
        logger.exception("读取会话列表失败")
        return {"ok": False, "items": [], "message": f"读取失败：{exc}"}
    finally:
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
