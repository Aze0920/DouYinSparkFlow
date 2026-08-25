"""抖音扫码登录：弹出二维码，确认后抓取 Cookie、昵称和抖音号。"""
from __future__ import annotations

import base64
import json
import random
import string
import threading
import time
from typing import Any

from core.browser import get_browser

SSO = "https://sso.douyin.com"
HOME = "https://www.douyin.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_state: dict[str, Any] = {
    "status": "idle",
    "message": "",
    "qr_base64": "",
    "qr_url": "",
    "username": "",
    "unique_id": "",
    "cookies": [],
    "replace_index": -1,
    "started_at": 0,
}


def _set(**kwargs):
    with _lock:
        _state.update(kwargs)


def snapshot(include_cookies: bool = False) -> dict[str, Any]:
    with _lock:
        data = {
            "status": _state.get("status") or "idle",
            "message": _state.get("message") or "",
            "qr_base64": _state.get("qr_base64") or "",
            "qr_url": _state.get("qr_url") or "",
            "username": _state.get("username") or "",
            "unique_id": _state.get("unique_id") or "",
            "replace_index": int(_state.get("replace_index") or -1),
            "started_at": _state.get("started_at") or 0,
        }
        if include_cookies and data["status"] == "success":
            data["cookies"] = _state.get("cookies") or []
        return data


def gen_verify_fp() -> str:
    chars = string.ascii_letters + string.digits

    def block(n: int) -> str:
        return "".join(random.choice(chars) for _ in range(n))

    return f"verify_{block(8)}_{block(4)}_{block(4)}_{block(4)}_{block(12)}"


def _sso_params(fp: str) -> dict[str, str]:
    return {
        "service": HOME,
        "need_logo": "false",
        "need_short_url": "false",
        "passport_jssdk_version": "1.0.20",
        "aid": "6383",
        "account_sdk_source": "sso",
        "sdk_version": "2.2.7",
        "language": "zh",
        "verifyFp": fp,
        "fp": fp,
    }


def _cookie_map(context) -> dict[str, str]:
    return {c.get("name"): c.get("value") or "" for c in context.cookies()}


def _has_session(context) -> bool:
    names = set(_cookie_map(context))
    return bool(names & {"sessionid", "sessionid_ss", "sid_guard"})


def _cookies_for_save(context) -> list[dict[str, Any]]:
    out = []
    for item in context.cookies():
        row = {
            "name": item.get("name"),
            "value": item.get("value"),
            "domain": item.get("domain") or ".douyin.com",
            "path": item.get("path") or "/",
        }
        expires = item.get("expires")
        if expires not in (None, -1):
            row["expires"] = expires
        if "httpOnly" in item:
            row["httpOnly"] = item["httpOnly"]
        if "secure" in item:
            row["secure"] = item["secure"]
        same_site = item.get("sameSite")
        if same_site in ("Strict", "Lax", "None"):
            row["sameSite"] = same_site
        out.append(row)
    return out


def _walk_user(obj: Any, found: dict[str, str] | None = None) -> dict[str, str]:
    found = found if found is not None else {"username": "", "unique_id": ""}
    if isinstance(obj, dict):
        nick = (
            obj.get("nickname")
            or obj.get("screen_name")
            or obj.get("nick_name")
            or obj.get("user_name")
        )
        uid = (
            obj.get("unique_id")
            or obj.get("uniqueId")
            or obj.get("uniq_id")
            or obj.get("display_id")
            or obj.get("short_id")
        )
        if nick and not found["username"] and isinstance(nick, str) and nick not in ("douyin", "抖音"):
            found["username"] = nick.strip()
        if uid and not found["unique_id"] and isinstance(uid, (str, int)):
            text = str(uid).strip()
            if text and text.lower() not in ("none", "null"):
                found["unique_id"] = text
        for value in obj.values():
            _walk_user(value, found)
            if found["username"] and found["unique_id"]:
                return found
    elif isinstance(obj, list):
        for value in obj:
            _walk_user(value, found)
            if found["username"] and found["unique_id"]:
                return found
    return found


def _http_get(context, url: str, params: dict | None = None):
    kwargs = {
        "params": params or {},
        "headers": {"Referer": HOME + "/", "User-Agent": UA},
    }
    try:
        return context.request.get(url, timeout=20000, **kwargs)
    except TypeError:
        return context.request.get(url, **kwargs)


def _try_json(context, url: str, params: dict | None = None) -> dict[str, str]:
    try:
        resp = _http_get(context, url, params)
        if resp.status != 200:
            return {}
        payload = resp.json()
        if isinstance(payload, dict) and "data" in payload:
            return _walk_user(payload.get("data"))
        return _walk_user(payload)
    except Exception:
        return {}


def _try_page_user(page) -> dict[str, str]:
    try:
        return page.evaluate(
            """() => {
              const blob = [...document.querySelectorAll("script")]
                .map((s) => s.textContent || "")
                .join("\\n");
              const nick = blob.match(/"nickname"\\s*:\\s*"([^"]+)"/);
              const uid =
                blob.match(/"uniqueId"\\s*:\\s*"([^"]+)"/) ||
                blob.match(/"unique_id"\\s*:\\s*"([^"]+)"/) ||
                blob.match(/"uniq_id"\\s*:\\s*"([^"]+)"/) ||
                blob.match(/"display_id"\\s*:\\s*"([^"]+)"/);
              return {
                username: nick ? nick[1] : "",
                unique_id: uid ? uid[1] : "",
              };
            }"""
        ) or {}
    except Exception:
        return {}


def extract_profile(page, context) -> dict[str, str]:
    found = {"username": "", "unique_id": ""}
    probes = [
        (HOME + "/passport/web/account/info/", None),
        (HOME + "/webcast/user/me/", {"aid": "1128"}),
        (HOME + "/webcast/user/me/", {"aid": "6383"}),
        (
            HOME + "/aweme/v1/web/user/profile/self/",
            {"device_platform": "webapp", "aid": "6383", "publish_video_strategy_type": "2"},
        ),
    ]
    for url, params in probes:
        got = _try_json(context, url, params)
        if got.get("username") and not found["username"]:
            found["username"] = got["username"]
        if got.get("unique_id") and not found["unique_id"]:
            found["unique_id"] = got["unique_id"]
        if found["username"] and found["unique_id"]:
            break

    if not (found["username"] and found["unique_id"]):
        for url in (HOME + "/", HOME + "/user/self", HOME + "/chat"):
            if _stop.is_set():
                break
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(1.2)
            except Exception:
                continue
            got = _try_page_user(page)
            if got.get("username") and not found["username"]:
                found["username"] = got["username"]
            if got.get("unique_id") and not found["unique_id"]:
                found["unique_id"] = got["unique_id"]
            if found["username"] and found["unique_id"]:
                break

    cookies = _cookie_map(context)
    if not found["unique_id"]:
        found["unique_id"] = (
            cookies.get("uid_tt")
            or cookies.get("uid_tt_ss")
            or ""
        )
    if not found["username"]:
        found["username"] = found["unique_id"] or "抖音账号"
    return found


def _capture_qr_image(page) -> str:
    selectors = [
        "#animate_qrcode_container img",
        "img[src*='qrcode']",
        "img[alt*='二维码']",
        "[class*='qrcode'] img",
        "[class*='qr-code'] img",
        "[class*='Qrcode'] img",
    ]
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if loc.count() == 0:
                continue
            loc.wait_for(state="visible", timeout=2500)
            png = loc.screenshot()
            if png:
                return base64.b64encode(png).decode("ascii")
        except Exception:
            continue
    return ""


def _open_login_panel(page):
    for selector in [
        "text=登录",
        "button:has-text('登录')",
        "div:has-text('登录'):visible",
        "[class*='login']",
    ]:
        loc = page.locator(selector).first
        try:
            if loc.count() == 0:
                continue
            loc.click(timeout=2500)
            time.sleep(0.8)
            return
        except Exception:
            continue


def _request_qr(context, fp: str) -> tuple[str, str, str]:
    try:
        resp = _http_get(context, SSO + "/get_qrcode/", _sso_params(fp))
        payload = resp.json()
    except Exception:
        return "", "", ""
    data = (payload or {}).get("data") or {}
    token = str(data.get("token") or "")
    qrcode = str(data.get("qrcode") or "")
    if qrcode.startswith("data:image"):
        qrcode = qrcode.split(",", 1)[-1]
    qr_url = str(data.get("qrcode_index_url") or data.get("url") or "")
    return token, qrcode, qr_url


def _check_qr(context, fp: str, token: str) -> dict[str, Any]:
    params = _sso_params(fp)
    params["token"] = token
    try:
        resp = _http_get(context, SSO + "/check_qrconnect/", params)
        return (resp.json() or {}).get("data") or {}
    except Exception:
        return {}


def _finish_login(page, context, redirect_url: str | None):
    if redirect_url:
        try:
            _http_get(context, redirect_url)
        except Exception:
            try:
                page.goto(redirect_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
    try:
        page.goto(HOME + "/", wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    deadline = time.time() + 45
    while time.time() < deadline and not _stop.is_set():
        if _has_session(context):
            return True
        time.sleep(1)
    return _has_session(context)


def _worker(replace_index: int):
    playwright = None
    browser = None
    try:
        _set(
            status="loading",
            message="正在生成二维码…",
            qr_base64="",
            qr_url="",
            username="",
            unique_id="",
            cookies=[],
            replace_index=replace_index,
            started_at=time.time(),
        )
        playwright, browser = get_browser()
        context = browser.new_context(
            user_agent=UA,
            locale="zh-CN",
            viewport={"width": 1280, "height": 860},
        )
        context.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9"})
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()
        fp = gen_verify_fp()
        try:
            context.add_cookies(
                [
                    {"name": "s_v_web_id", "value": fp, "domain": ".douyin.com", "path": "/"},
                    {"name": "s_v_web_id", "value": fp, "domain": ".snssdk.com", "path": "/"},
                ]
            )
        except Exception:
            pass
        try:
            page.goto(HOME + "/", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass

        token, qr_png, qr_url = _request_qr(context, fp)
        if not qr_png:
            _open_login_panel(page)
            time.sleep(1.2)
            qr_png = _capture_qr_image(page)

        if not qr_png and not qr_url and not token:
            _set(status="error", message="获取二维码失败，请稍后点「刷新二维码」再试")
            return

        _set(
            status="waiting",
            message="请用抖音 App 扫码，并在手机上确认登录",
            qr_base64=qr_png,
            qr_url=qr_url,
        )

        deadline = time.time() + 180
        last_shot = time.time()
        while time.time() < deadline and not _stop.is_set():
            if token:
                data = _check_qr(context, fp, token)
                code = str(data.get("status") or "")
                nick = str(data.get("nickname") or "")
                if code == "2":
                    _set(
                        status="scanned",
                        message=("已扫码" + (f"：{nick}" if nick else "") + "，请在手机上确认登录"),
                    )
                elif code == "5":
                    _set(status="expired", message="二维码已过期，请刷新后再扫")
                    return
                elif code in ("3", "4") or data.get("redirect_url"):
                    _set(status="scanned", message="已确认，正在抓取账号信息和 Cookie…")
                    if not _finish_login(page, context, data.get("redirect_url")):
                        _set(status="error", message="登录确认了，但没有拿到 Cookie，请刷新二维码重试")
                        return
                    break
            elif _has_session(context):
                _set(status="scanned", message="已登录，正在抓取账号信息…")
                break
            else:
                if time.time() - last_shot > 12:
                    shot = _capture_qr_image(page)
                    if shot:
                        _set(qr_base64=shot)
                    last_shot = time.time()
            time.sleep(1.4)
        else:
            if _stop.is_set():
                _set(status="idle", message="", qr_base64="")
                return
            _set(status="expired", message="等待扫码超时，请刷新二维码")
            return

        if _stop.is_set():
            _set(status="idle", message="", qr_base64="")
            return

        profile = extract_profile(page, context)
        cookies = _cookies_for_save(context)
        if not cookies or not _has_session(context):
            _set(status="error", message="没有拿到有效登录 Cookie，请重新扫码")
            return
        _set(
            status="success",
            message="登录成功，已自动抓取用户名、抖音号和 Cookie",
            username=profile.get("username") or "抖音账号",
            unique_id=profile.get("unique_id") or "",
            cookies=cookies,
            qr_base64="",
            qr_url="",
        )
    except Exception as exc:
        _set(status="error", message=f"扫码登录失败：{exc}")
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


def start_qr_login(replace_index: int = -1) -> dict[str, Any]:
    global _thread
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=8)
    _stop.clear()
    _set(
        status="loading",
        message="正在生成二维码…",
        qr_base64="",
        qr_url="",
        username="",
        unique_id="",
        cookies=[],
        replace_index=replace_index,
        started_at=time.time(),
    )
    _thread = threading.Thread(target=_worker, args=(replace_index,), daemon=True)
    _thread.start()
    return snapshot()


def cancel_qr_login() -> dict[str, Any]:
    _stop.set()
    _set(status="idle", message="", qr_base64="", qr_url="", cookies=[])
    return snapshot()
