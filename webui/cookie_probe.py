"""用已有 Cookie 检测登录是否有效，并抓取昵称、抖音号。"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

from core.browser import get_browser
from utils.logger import setup_logger
from webui.qr_login import (
    HOME,
    UA,
    _cookies_for_save,
    _has_session,
    _page_signals,
    extract_profile,
    is_display_unique_id,
)

logger = setup_logger("app", "DEBUG")

SESSION_NAMES = {"sessionid", "sessionid_ss", "sid_guard", "sid_tt", "sid_ucp_v1"}
_probe_lock = threading.Lock()


def parse_cookie_payload(raw: Any) -> list[dict[str, Any]]:
    obj = raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        if not text:
            raise ValueError("请粘贴 Cookie JSON")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Cookie 不是合法 JSON") from exc
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except json.JSONDecodeError as exc:
                raise ValueError("Cookie 不是合法 JSON") from exc
    if isinstance(obj, dict):
        if isinstance(obj.get("cookies"), list):
            obj = obj["cookies"]
        elif obj.get("name") and obj.get("value") is not None:
            obj = [obj]
        else:
            obj = [
                {"name": key, "value": value}
                for key, value in obj.items()
                if key not in {"cookies", "Cookie"} and isinstance(value, (str, int, float))
            ]
    if not isinstance(obj, list):
        raise ValueError("Cookie 必须是 JSON 数组，例如 [{\"name\":\"sessionid\",\"value\":\"...\"}]")

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in obj:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("Name") or "").strip()
        value = item.get("value")
        if value is None:
            value = item.get("Value")
        if not name or value is None:
            continue
        domain = str(item.get("domain") or item.get("Domain") or ".douyin.com").strip() or ".douyin.com"
        if "://" in domain:
            host = urlparse(domain).hostname or ""
            domain = host or ".douyin.com"
        if domain.startswith("www."):
            domain = domain[3:]
        if domain in {"douyin.com", "snssdk.com"}:
            domain = "." + domain
        path = str(item.get("path") or item.get("Path") or "/") or "/"
        key = (name, domain)
        if key in seen:
            continue
        seen.add(key)
        row = {"name": name, "value": str(value), "domain": domain, "path": path}
        expires = item.get("expires")
        if expires in (None, "", -1):
            expires = item.get("expirationDate") or item.get("Expiry")
        try:
            if expires not in (None, "", -1):
                exp = float(expires)
                if exp > 0:
                    row["expires"] = exp
        except (TypeError, ValueError):
            pass
        http_only = item.get("httpOnly")
        if http_only is None:
            http_only = item.get("http_only")
        if isinstance(http_only, bool):
            row["httpOnly"] = http_only
        secure = item.get("secure")
        if isinstance(secure, bool):
            row["secure"] = secure
        rows.append(row)

    if not rows:
        raise ValueError("没有解析到任何 Cookie")
    if not any(item["name"] in SESSION_NAMES for item in rows):
        raise ValueError("JSON 里没有 sessionid，请确认复制的是登录后的 Cookie")
    return rows


def probe_cookies(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    if not _probe_lock.acquire(blocking=False):
        return {"ok": False, "valid": False, "message": "正在检测另一个账号，请稍后再试"}

    playwright = None
    browser = None
    try:
        logger.info("开始检测 Cookie 共 %s 条", len(cookies))
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
            return {"ok": True, "valid": False, "username": "", "unique_id": "", "cookies": cookies, "message": "Cookie 格式浏览器不接受，请用扫码登录或换一份 JSON"}

        page = context.new_page()
        profile = extract_profile(page, context, allow_stop=False)
        signals: dict[str, Any] = {}
        try:
            page.goto(HOME + "/", wait_until="domcontentloaded", timeout=25000)
            time.sleep(0.8)
            signals = _page_signals(page)
        except Exception:
            logger.warning("打开首页检查登录态失败", exc_info=True)
            if not profile.get("unique_id"):
                profile = extract_profile(page, context, allow_stop=False)
            signals = _page_signals(page)

        has_session = _has_session(context)
        login_wall = bool(signals.get("hasScan") or signals.get("hasEnjoy"))
        username = str(profile.get("username") or "").strip()
        unique_id = str(profile.get("unique_id") or "").strip()
        if unique_id and not is_display_unique_id(unique_id):
            unique_id = ""
        named = bool(username) and username not in {unique_id, "抖音账号"}
        valid = bool((named or has_session) and not login_wall)
        if login_wall and not named:
            valid = False
        saved = _cookies_for_save(context) or cookies
        if valid:
            message = (
                f"Cookie 有效 · {username or '已登录'}"
                + (f" · 抖音号 {unique_id}" if unique_id else "")
            )
        elif login_wall:
            message = "Cookie 已失效，需要重新扫码或更换 JSON"
        elif not has_session:
            message = "Cookie 无效：没有可用的登录态"
        else:
            message = "Cookie 还在，但没抓到昵称和抖音号，可能被风控，建议重新扫码"
        logger.info(
            "Cookie 检测结果 valid=%s username=%s unique_id=%s wall=%s session=%s",
            valid,
            username,
            unique_id,
            login_wall,
            has_session,
        )
        return {
            "ok": True,
            "valid": valid,
            "username": username,
            "unique_id": unique_id,
            "avatar": str(profile.get("avatar") or "").strip(),
            "cookies": saved,
            "message": message,
        }
    except Exception as exc:
        logger.exception("检测 Cookie 失败")
        return {"ok": False, "valid": False, "username": "", "unique_id": "", "cookies": cookies, "message": f"检测失败：{exc}"}
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
