"""用已有 Cookie 检测登录是否有效，并抓取昵称、抖音号。"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

from core.browser import get_browser, make_context
from utils.logger import setup_logger
from webui.qr_login import (
    CHAT_URLS,
    HOME,
    _cookies_for_save,
    _has_session,
    _page_signals,
    extract_profile,
    is_display_unique_id,
    wait_chat_access,
)
from webui.session_store import load_state_path, save_state

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


def probe_cookies(cookies: list[dict[str, Any]], unique_id: str = "", region: str = "") -> dict[str, Any]:
    if not _probe_lock.acquire(blocking=False):
        return {"ok": False, "valid": False, "message": "正在检测另一个账号，请稍后再试"}

    playwright = None
    browser = None
    context = None
    lease = None
    try:
        logger.info("开始检测 Cookie 共 %s 条 unique_id=%s", len(cookies), unique_id or "-")
        if str(region or "").strip():
            try:
                from webui.proxy import lease_proxy, proxy_enabled

                # 总开关关掉就当没设地区：直连检测，不取 IP、不探活
                if proxy_enabled():
                    lease = lease_proxy(region)
            except Exception:
                logger.exception("检测 Cookie 时提取代理失败，改走直连")
        proxy = lease.server if lease else None
        playwright, browser = get_browser()
        state = load_state_path(unique_id)
        try:
            context = make_context(browser, storage_state=state, cookies=cookies, proxy=proxy)
        except Exception:
            if not proxy:
                raise
            logger.exception("用代理建上下文失败，改走直连")
            context = make_context(browser, storage_state=state, cookies=cookies)

        page = context.new_page()
        profile = extract_profile(page, context, allow_stop=False)
        chat_reachable = False
        chat_state = "empty"
        wait_s = 35 if proxy else 20
        nav_ms = 30000 if proxy else 20000
        # /chat 和 ?isPopup=1 两套壳，有的号只在其中一个渲染会话列表。
        # 只等 commit：真正要看的是列表元素，交给 wait_chat_access。
        for url in CHAT_URLS:
            try:
                page.goto(url, wait_until="commit", timeout=nav_ms)
                chat_reachable = True
            except Exception as exc:
                logger.warning("打开私信页超时（%s）url=%s，这次没法顺带验证私信功能，不影响登录态判断", type(exc).__name__, url)
                continue
            chat_state = wait_chat_access(page, timeout_s=wait_s)
            if chat_state in {"chat", "login", "challenge"}:
                break
        signals = _page_signals(page)
        if not signals:
            try:
                page.goto(HOME + "/", wait_until="commit", timeout=20000)
                time.sleep(1.5)
                signals = _page_signals(page)
            except Exception:
                signals = {}

        has_session = _has_session(context)
        login_wall = chat_state == "login" or bool(signals.get("hasScan") or signals.get("hasEnjoy"))
        chat_ok = chat_state == "chat"
        challenged = chat_state == "challenge" or bool(signals.get("hasChallenge") or signals.get("hasVerify"))
        username = str(profile.get("username") or "").strip()
        got_uid = str(profile.get("unique_id") or "").strip()
        if got_uid and not is_display_unique_id(got_uid):
            got_uid = ""
        named = bool(username) and username not in {got_uid, "抖音账号"}
        account_id = got_uid or str(unique_id or "").strip()

        # 判「掉线」必须有实锤，只有两种：
        #   1) 私信页把我们弹到了扫码墙（login_wall）——账号确实需要重新登录；
        #   2) 压根没有登录态 cookie（not has_session）——Cookie 本身就是坏的。
        # 除此之外，只要还揣着 session、又没撞见扫码墙，那就得先「确认」到点东西
        # 才敢说有效：私信列表出来了（chat_ok）或抓到了昵称/抖音号（named）。
        positive = chat_ok or named
        # 什么都没确认到、可又没有掉线的实锤 —— 这次纯粹是没连通抖音
        # （代理挂了或网太慢，所有请求全超时），只能算「无法确认」。
        # 绝不能据此判掉线、更不能白推一条「账号掉线」通知，用户回头一看号好好的。
        undecided = has_session and not login_wall and not positive
        valid = bool(positive and not login_wall) or undecided
        saved = _cookies_for_save(context) or cookies
        if positive and not login_wall:
            save_state(context, account_id)
            who = (username or "已登录") + (f" · 抖音号 {got_uid}" if got_uid else "")
            if chat_ok:
                message = f"Cookie 有效 · 网页私信可打开 · {who}"
            elif challenged:
                message = (
                    f"登录态正常 · {who}。"
                    "私信页在做安全验证（滑块/验证码），会话列表出不来，续火花也会失败。"
                    "账号没掉线，换条线路或稍后再试"
                )
            elif chat_reachable:
                snippet = str(signals.get("snippet") or "").strip()
                extra = f"（页面有：{snippet[:40]}）" if snippet else ""
                message = (
                    f"登录态正常 · {who}。"
                    f"私信页打开了，但会话列表没出来{extra}。"
                    "不是 Cookie 失效，多半是页面改版或被限制，续火花会同样打不开列表"
                )
            else:
                message = (
                    f"登录态正常 · {who}。"
                    "这次没打开私信页（线路超时），没能顺带验证私信，稍后可再检测一次"
                )
        elif undecided:
            message = (
                "这次没连上抖音（代理可能失效，或网络太慢导致请求全部超时），"
                "账号状态无法确认。登录态 Cookie 还在，账号大概率没问题，"
                "请稍后或换条线路再检测一次——本次不作掉线处理。"
            )
        elif login_wall:
            message = "首页 Cookie 可能还在，但网页私信在要求扫码。请重新扫码登录这个号，不要只贴 JSON Cookie"
        else:
            message = "Cookie 无效：没有可用的登录态，请重新扫码登录"
        logger.info(
            "Cookie 检测结果 valid=%s undecided=%s username=%s unique_id=%s wall=%s challenge=%s session=%s chat=%s 私信页可达=%s snippet=%s",
            valid,
            undecided,
            username,
            got_uid,
            login_wall,
            challenged,
            has_session,
            chat_state,
            chat_reachable,
            str(signals.get("snippet") or "")[:80],
        )
        return {
            "ok": True,
            "valid": valid,
            "undecided": undecided,
            "username": username,
            "unique_id": got_uid,
            "avatar": str(profile.get("avatar") or "").strip(),
            "cookies": saved,
            "chat_ok": chat_ok,
            "message": message,
        }
    except Exception as exc:
        logger.exception("检测 Cookie 失败")
        return {"ok": False, "valid": False, "username": "", "unique_id": "", "cookies": cookies, "message": f"检测失败：{exc}"}
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
        # 浏览器全关掉才算真的不再用这条 IP
        if lease:
            lease.release(f"检测 {unique_id or '-'}")
        _probe_lock.release()
