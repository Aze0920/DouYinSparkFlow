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
from utils.logger import setup_logger

logger = setup_logger("app", "DEBUG")

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
    note = {k: v for k, v in kwargs.items() if k not in {"qr_base64", "qr_url", "cookies"}}
    if note:
        logger.info("扫码状态 %s", note)


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


def _brief_payload(payload: Any, limit: int = 700) -> str:
    try:
        data = payload
        if isinstance(payload, dict):
            data = dict(payload)
            inner = data.get("data")
            if isinstance(inner, dict):
                inner = dict(inner)
                qrcode = inner.get("qrcode")
                if qrcode:
                    inner["qrcode"] = f"<omitted {len(str(qrcode))} chars>"
                data["data"] = inner
        text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    except Exception:
        text = str(payload)
    return text[:limit]


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
        logger.debug("资料接口 %s -> HTTP %s", url, getattr(resp, "status", "?"))
        if resp.status != 200:
            logger.warning("资料接口失败 %s HTTP %s body=%s", url, resp.status, (resp.text() or "")[:300])
            return {}
        payload = resp.json()
        if isinstance(payload, dict) and "data" in payload:
            return _walk_user(payload.get("data"))
        return _walk_user(payload)
    except Exception:
        logger.exception("资料接口异常 %s", url)
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
        logger.exception("页面解析用户信息失败")
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
            logger.info("已抓到账号资料 username=%s unique_id=%s", found["username"], found["unique_id"])
            break

    if not (found["username"] and found["unique_id"]):
        for url in (HOME + "/", HOME + "/user/self", HOME + "/chat"):
            if _stop.is_set():
                break
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                logger.debug("打开资料页 %s", page.url)
                time.sleep(1.2)
            except Exception:
                logger.exception("打开资料页失败 %s", url)
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
    logger.info("最终账号资料 username=%s unique_id=%s", found["username"], found["unique_id"])
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
                logger.info("页面截到二维码 selector=%s size=%s", selector, len(png))
                return base64.b64encode(png).decode("ascii")
        except Exception:
            logger.debug("截二维码失败 selector=%s", selector, exc_info=True)
            continue
    logger.warning("页面上没有截到二维码")
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
            logger.info("已点击登录入口 selector=%s", selector)
            time.sleep(0.8)
            return
        except Exception:
            logger.debug("点击登录入口失败 selector=%s", selector, exc_info=True)
            continue
    logger.warning("没有找到可点击的登录入口")


def _request_qr(context, fp: str) -> tuple[str, str, str]:
    try:
        resp = _http_get(context, SSO + "/get_qrcode/", _sso_params(fp))
        status = getattr(resp, "status", "?")
        body = ""
        try:
            body = resp.text()
        except Exception:
            body = ""
        logger.info("get_qrcode HTTP %s body_len=%s", status, len(body or ""))
        try:
            payload = resp.json()
        except Exception:
            logger.warning("get_qrcode 返回非 JSON: %s", (body or "")[:500])
            return "", "", ""
    except Exception:
        logger.exception("请求 get_qrcode 失败")
        return "", "", ""
    data = (payload or {}).get("data") or {}
    token = str(data.get("token") or "")
    qrcode = str(data.get("qrcode") or "")
    if qrcode.startswith("data:image"):
        qrcode = qrcode.split(",", 1)[-1]
    qr_url = str(data.get("qrcode_index_url") or data.get("url") or "")
    logger.info(
        "get_qrcode token=%s qr_png=%s qr_url=%s payload=%s",
        "yes" if token else "no",
        len(qrcode),
        "yes" if qr_url else "no",
        _brief_payload(payload),
    )
    return token, qrcode, qr_url


def _check_qr(context, fp: str, token: str) -> dict[str, Any]:
    params = _sso_params(fp)
    params["token"] = token
    try:
        resp = _http_get(context, SSO + "/check_qrconnect/", params)
        payload = resp.json() or {}
        data = payload.get("data") or {}
        logger.debug("check_qrconnect HTTP %s status=%s", getattr(resp, "status", "?"), data.get("status"))
        return data
    except Exception:
        logger.exception("check_qrconnect 失败")
        return {}


def _finish_login(page, context, redirect_url: str | None):
    if redirect_url:
        try:
            _http_get(context, redirect_url)
            logger.info("已跟随登录跳转")
        except Exception:
            logger.warning("跟随 redirect_url 失败，改用页面打开", exc_info=True)
            try:
                page.goto(redirect_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                logger.exception("页面打开 redirect_url 失败")
    try:
        page.goto(HOME + "/", wait_until="domcontentloaded", timeout=45000)
        logger.info("登录后回到首页 url=%s", page.url)
    except Exception:
        logger.exception("登录后打开首页失败")
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
        logger.info("扫码线程启动 replace_index=%s", replace_index)
        logger.info("正在启动浏览器")
        playwright, browser = get_browser()
        logger.info("浏览器已启动")
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
            logger.warning("写入 s_v_web_id cookie 失败", exc_info=True)
        try:
            logger.info("打开抖音首页 %s", HOME)
            page.goto(HOME + "/", wait_until="domcontentloaded", timeout=60000)
            logger.info("首页已打开 url=%s title=%s", page.url, page.title())
        except Exception:
            logger.exception("打开抖音首页失败")

        token, qr_png, qr_url = _request_qr(context, fp)
        if not qr_png:
            logger.info("接口未返回二维码图片，尝试从页面截取")
            _open_login_panel(page)
            time.sleep(1.2)
            qr_png = _capture_qr_image(page)

        if not qr_png and not qr_url and not token:
            logger.error("获取二维码失败：token、图片、链接都为空")
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
        cookie_names = [c.get("name") for c in cookies]
        logger.info("抓到 Cookie %s 个 names=%s", len(cookies), cookie_names)
        if not cookies or not _has_session(context):
            logger.error("没有有效登录 Cookie names=%s", cookie_names)
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
        logger.exception("扫码登录线程异常")
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
    logger.info("准备启动扫码会话 replace_index=%s", replace_index)
    _stop.set()
    if _thread and _thread.is_alive():
        logger.info("等待上一次扫码浏览器退出")
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
    logger.info("收到取消扫码登录")
    _stop.set()
    _set(status="idle", message="", qr_base64="", qr_url="", cookies=[])
    return snapshot()
