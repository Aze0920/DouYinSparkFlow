"""抖音扫码登录：弹出二维码，确认后抓取 Cookie、昵称和抖音号。"""
from __future__ import annotations

import base64
import json
import random
import string
import threading
import time
from pathlib import Path
from typing import Any

from core.browser import get_browser
from utils.logger import setup_logger

logger = setup_logger("app", "DEBUG")

SSO = "https://sso.douyin.com"
HOME = "https://www.douyin.com"
CHAT = "https://www.douyin.com/chat"
DEBUG_SHOT = Path(__file__).resolve().parent.parent / "logs" / "qr-debug.png"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_commands: list[dict[str, Any]] = []
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
    "verify_methods": [],
    "verify_account": "",
    "verify_need_code": False,
    "verify_need_password": False,
    "verify_image": "",
}


def _set(**kwargs):
    with _lock:
        _state.update(kwargs)
    note = {k: v for k, v in kwargs.items() if k not in {"qr_base64", "qr_url", "cookies", "verify_image"}}
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
            "verify_methods": list(_state.get("verify_methods") or []),
            "verify_account": _state.get("verify_account") or "",
            "verify_need_code": bool(_state.get("verify_need_code")),
            "verify_need_password": bool(_state.get("verify_need_password")),
            "verify_image": _state.get("verify_image") or "",
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


def _all_cookie_list(context) -> list:
    rows = []
    seen = set()
    urls = [None, HOME, CHAT, "https://sso.douyin.com/", "https://www.douyin.com/passport/"]
    for url in urls:
        try:
            chunk = context.cookies([url]) if url else context.cookies()
        except TypeError:
            chunk = context.cookies(url) if url else context.cookies()
        except Exception:
            continue
        for item in chunk or []:
            key = (item.get("name"), item.get("domain"), item.get("path"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
    return rows


def _cookie_map(context) -> dict[str, str]:
    return {c.get("name"): c.get("value") or "" for c in _all_cookie_list(context)}


def _has_session(context) -> bool:
    cookies = _cookie_map(context)
    names = set(cookies)
    if names & {"sessionid", "sessionid_ss", "sid_guard", "sid_tt", "sid_ucp_v1"}:
        return True
    if cookies.get("LOGIN_STATUS") in ("1", "true", "True"):
        return True
    return False


def _page_signals(page) -> dict[str, Any]:
    try:
        return page.evaluate(
            """() => {
              const text = (document.body && document.body.innerText) || "";
              return {
                href: location.href,
                hasUser: localStorage.getItem("HasUserLogin") || "",
                loginStatus: localStorage.getItem("LOGIN_STATUS") || "",
                hasScan: text.includes("扫码登录"),
                hasEnjoy: text.includes("登录后免费畅享") || text.includes("打开「抖音APP」"),
                hasVerify: text.includes("身份验证") || text.includes("完成身份验证"),
                hasChat: !!(document.querySelector("[class*='conversation']") || document.querySelector("[class*='Conversation']")),
              };
            }"""
        ) or {}
    except Exception:
        logger.debug("读取页面登录信号失败", exc_info=True)
        return {}


def _page_logged_in(page) -> bool:
    flags = _page_signals(page)
    if not flags:
        return False
    if flags.get("hasVerify"):
        return False
    if str(flags.get("hasUser") or "") in ("1", "true", "True"):
        logger.info("页面登录信号 HasUserLogin=%s", flags)
        return True
    if str(flags.get("loginStatus") or "") in ("1", "true", "True"):
        logger.info("页面登录信号 LOGIN_STATUS=%s", flags)
        return True
    if flags.get("hasChat") and not flags.get("hasScan") and not flags.get("hasEnjoy"):
        logger.info("页面登录信号 私信列表已出现 %s", flags)
        return True
    return False


def _login_modal_visible(page) -> bool:
    flags = _page_signals(page)
    return bool(flags.get("hasScan") or flags.get("hasEnjoy"))


SNIFF_URL_HINTS = (
    "sso.douyin.com",
    "passport",
    "verify",
    "captcha",
    "check_qr",
    "get_qrcode",
    "sms",
    "safe",
    "auth",
    "login",
    "secsdk",
    "identity",
    "activate",
)
VERIFY_WAY_LABELS = {
    "sms": "接收短信验证码",
    "sms_verify": "接收短信验证码",
    "mobile_sms": "接收短信验证码",
    "receive_sms": "接收短信验证码",
    "uplink_sms": "发送短信验证",
    "uplink_sms_verify": "发送短信验证",
    "send_sms": "发送短信验证",
    "sms_uplink": "发送短信验证",
    "email": "邮箱验证",
    "email_verify": "邮箱验证",
    "pwd": "密码验证",
    "password": "密码验证",
    "pwd_verify": "密码验证",
    "face": "人脸验证",
    "face_verify": "人脸验证",
    "voice": "语音验证码",
    "voice_verify": "语音验证码",
    "question": "安全问题",
}
VERIFY_SCAN_JS = """(keys) => {
  const bodyText = (document.body && document.body.innerText) || "";
  const visible = bodyText.includes("身份验证") || bodyText.includes("完成身份验证") || bodyText.includes("确保为本人操作");
  const methods = [];
  const seen = new Set();
  const push = (label, id) => {
    const t = String(label || "").replace(/\\s+/g, " ").trim();
    if (!t || t.length > 48 || seen.has(t)) return;
    seen.add(t);
    methods.push({ id: String(id || t), label: t });
  };
  const nodes = document.querySelectorAll("button, [role=button], li, a, p, span, div");
  for (const el of nodes) {
    if (el.children && el.children.length > 6) continue;
    const t = (el.innerText || "").replace(/\\s+/g, " ").trim();
    if (!t || t.length > 40) continue;
    for (const k of keys) {
      if (t === k || t.includes(k)) {
        push(t.length <= 24 ? t : k, t);
        break;
      }
    }
  }
  const lines = bodyText.split(/\\n+/).map((s) => s.trim()).filter(Boolean);
  const head = lines.findIndex((l) => l.includes("身份验证"));
  let account = "";
  if (head >= 0) {
    for (let i = head + 1; i < Math.min(lines.length, head + 12); i++) {
      const line = lines[i];
      if (!line || line.length > 20) continue;
      if (/验证|短信|邮箱|密码|人脸|保障|账号安全|本人操作/.test(line)) continue;
      account = line;
      break;
    }
  }
  const inputs = [...document.querySelectorAll("input")];
  const needCode = inputs.some((el) => {
    const hint = ((el.placeholder || "") + (el.name || "") + (el.getAttribute("maxlength") || "")).toLowerCase();
    return hint.includes("验证码") || hint.includes("code") || el.maxLength === 4 || el.maxLength === 6;
  });
  const needPassword = inputs.some((el) => (el.type || "") === "password");
  return { visible, methods, account, needCode, needPassword, href: location.href, snippet: bodyText.slice(0, 500) };
}"""


def _interesting_url(url: str) -> bool:
    text = (url or "").lower()
    return any(hint in text for hint in SNIFF_URL_HINTS)


def _uniq_methods(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    seen = set()
    for item in rows or []:
        label = str(item.get("label") or item.get("id") or "").strip()
        ident = str(item.get("id") or label).strip()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append({"id": ident, "label": label})
    return out


def _collect_methods_from_obj(obj: Any, acc: list[dict[str, str]], depth: int = 0):
    if depth > 8 or obj is None:
        return
    if isinstance(obj, dict):
        way = obj.get("verify_way") or obj.get("verifyWay") or obj.get("verify_type") or obj.get("auth_type")
        name = obj.get("name") or obj.get("title") or obj.get("desc") or obj.get("label") or obj.get("text")
        mobile = obj.get("mobile") or obj.get("phone") or obj.get("mask_mobile")
        if way or (isinstance(name, str) and any(k in name for k in ("验证", "短信", "邮箱", "密码", "人脸"))):
            ident = str(way or name or "")
            label = str(name or VERIFY_WAY_LABELS.get(str(way), "") or way or "")
            if mobile:
                label = f"{label}（{mobile}）" if label else str(mobile)
            if label:
                acc.append({"id": ident or label, "label": label})
        for key in (
            "verify_ways",
            "verify_way_list",
            "verify_options",
            "available_verify_ways",
            "auth_list",
            "methods",
            "verify_method_list",
        ):
            if key in obj:
                _collect_methods_from_obj(obj.get(key), acc, depth + 1)
        for value in obj.values():
            if isinstance(value, (dict, list)):
                _collect_methods_from_obj(value, acc, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _collect_methods_from_obj(item, acc, depth + 1)


def _ingest_packet(url: str, payload: Any, bag: dict[str, Any]):
    if not isinstance(payload, dict):
        return
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        data = payload
    token = data.get("token")
    if token:
        bag["token"] = str(token)
    status = data.get("status")
    if status is not None and status != "":
        bag["qr_status"] = str(status)
        logger.info("抓包登录状态 %s status=%s", url[:160], status)
    redirect = data.get("redirect_url") or data.get("redirectUrl")
    if redirect:
        bag["redirect"] = str(redirect)
    nick = data.get("nickname") or data.get("screen_name") or data.get("name")
    if isinstance(nick, str) and nick.strip() and nick not in ("douyin", "抖音"):
        bag["account"] = nick.strip()
    methods: list[dict[str, str]] = []
    _collect_methods_from_obj(payload, methods)
    if methods:
        bag["methods"] = _uniq_methods((bag.get("methods") or []) + methods)
        logger.info("抓包验证方式 %s", bag["methods"])
    text_blob = json.dumps(payload, ensure_ascii=False)
    if "身份验证" in text_blob or "verify_way" in text_blob or "verify_ways" in text_blob:
        bag["need_verify"] = True


def _attach_sniffer(page, bag: dict[str, Any]):
    def on_response(response):
        url = getattr(response, "url", "") or ""
        if not _interesting_url(url):
            return
        try:
            status = int(getattr(response, "status", 0) or 0)
            if status < 200 or status >= 400:
                return
            try:
                body = response.json()
            except Exception:
                return
            logger.info("抓包 %s %s %s", status, url[:180], _brief_payload(body, 900))
            _ingest_packet(url, body, bag)
        except Exception:
            logger.debug("抓包响应失败 %s", url[:160], exc_info=True)

    def on_request(request):
        url = getattr(request, "url", "") or ""
        if not _interesting_url(url):
            return
        try:
            method = getattr(request, "method", "") or ""
            post = getattr(request, "post_data", None) or ""
            if method in ("POST", "PUT") and post:
                logger.info("抓包请求 %s %s body=%s", method, url[:180], str(post)[:500])
        except Exception:
            return

    target = page.context
    target.on("response", on_response)
    target.on("request", on_request)


def _scan_verify_ui(page) -> dict[str, Any]:
    keys = list(dict.fromkeys(list(VERIFY_WAY_LABELS.values()) + [
        "接收短信验证码",
        "发送短信验证",
        "短信验证码",
        "发送短信",
        "邮箱验证",
        "密码验证",
        "人脸验证",
        "语音验证码",
        "安全问题",
    ]))
    merged = {
        "visible": False,
        "methods": [],
        "account": "",
        "needCode": False,
        "needPassword": False,
        "snippet": "",
    }
    for scope in _iter_scopes(page):
        try:
            part = scope.evaluate(VERIFY_SCAN_JS, keys) or {}
        except Exception:
            continue
        if part.get("visible"):
            merged["visible"] = True
        if part.get("needCode"):
            merged["needCode"] = True
        if part.get("needPassword"):
            merged["needPassword"] = True
        if part.get("account") and not merged["account"]:
            merged["account"] = str(part.get("account") or "")
        if part.get("snippet") and not merged["snippet"]:
            merged["snippet"] = str(part.get("snippet") or "")
        merged["methods"].extend(part.get("methods") or [])
    merged["methods"] = _uniq_methods(merged["methods"])
    return merged


def _capture_verify_image(page) -> str:
    selectors = [
        "div:has-text('身份验证')",
        "text=身份验证",
    ]
    for scope in _iter_scopes(page):
        for selector in selectors:
            try:
                loc = scope.locator(selector).last
                if loc.count() == 0:
                    continue
                box = loc.bounding_box()
                if box and box["width"] < 180:
                    continue
                png = loc.screenshot()
                if png and len(png) > 1200:
                    return base64.b64encode(png).decode("ascii")
            except Exception:
                continue
    try:
        png = page.screenshot()
        if png:
            return base64.b64encode(png).decode("ascii")
    except Exception:
        logger.debug("身份验证截图失败", exc_info=True)
    return ""


def _click_verify_method(page, method_id: str) -> bool:
    label = str(method_id or "").strip()
    if not label:
        return False
    candidates = [label]
    for prefix in ("接收", "发送"):
        if label.startswith(prefix) is False and prefix in label:
            pass
    if "（" in label:
        candidates.append(label.split("（", 1)[0].strip())
    for scope in _iter_scopes(page):
        for text in candidates:
            try:
                loc = scope.get_by_text(text, exact=True)
                if loc.count() == 0:
                    loc = scope.get_by_text(text, exact=False)
                loc.first.click(timeout=2500)
                logger.info("已点击验证方式 %s", text)
                return True
            except Exception:
                continue
    logger.warning("没有点到验证方式 %s", label)
    return False


def _fill_verify_code(page, code: str, password: str = "") -> bool:
    ok = False
    if password:
        for scope in _iter_scopes(page):
            try:
                loc = scope.locator("input[type='password']").first
                if loc.count():
                    loc.fill(password, timeout=2000)
                    ok = True
                    logger.info("已填入账号密码")
                    break
            except Exception:
                continue
    if code:
        selectors = [
            "input[placeholder*='验证码']",
            "input[placeholder*='校验码']",
            "input[autocomplete='one-time-code']",
            "input[type='tel']",
            "input[maxlength='6']",
            "input[maxlength='4']",
            "input[type='number']",
        ]
        for scope in _iter_scopes(page):
            for selector in selectors:
                try:
                    loc = scope.locator(selector).first
                    if loc.count() == 0:
                        continue
                    loc.fill(code, timeout=2000)
                    ok = True
                    logger.info("已填入验证码 selector=%s", selector)
                    break
                except Exception:
                    continue
            if ok and code:
                break
    for scope in _iter_scopes(page):
        for text in ("确定", "下一步", "验证", "提交", "完成", "确认"):
            try:
                loc = scope.get_by_text(text, exact=True)
                if loc.count() == 0:
                    continue
                loc.first.click(timeout=2000)
                logger.info("已点击 %s", text)
                return True
            except Exception:
                continue
    if not ok:
        logger.warning("没有找到验证码/密码输入框")
    return ok


def _pop_command() -> dict[str, Any] | None:
    with _lock:
        if not _commands:
            return None
        return _commands.pop(0)


def _push_command(cmd: dict[str, Any]):
    with _lock:
        _commands.append(cmd)


def _clear_commands():
    with _lock:
        _commands.clear()


def _status() -> str:
    with _lock:
        return str(_state.get("status") or "idle")


def _publish_verify(ui: dict[str, Any], sniff: dict[str, Any], shot: str = ""):
    methods = _uniq_methods((ui.get("methods") or []) + (sniff.get("methods") or []))
    account = str(ui.get("account") or sniff.get("account") or "")
    need_code = bool(ui.get("needCode") or sniff.get("need_code"))
    need_password = bool(ui.get("needPassword"))
    payload = {
        "status": "verify",
        "message": "扫码成功，请选择身份验证方式",
        "qr_base64": "",
        "qr_url": "",
        "verify_need_code": need_code,
        "verify_need_password": need_password,
    }
    if methods:
        payload["verify_methods"] = methods
        payload["message"] = "扫码成功，请选择一种身份验证方式"
    if account:
        payload["verify_account"] = account
        payload["message"] = f"扫码成功，请完成「{account}」的身份验证"
    if shot:
        payload["verify_image"] = shot
    if need_code:
        payload["message"] = (payload["message"] + "，并填写验证码").replace("，并填写验证码，并填写验证码", "，并填写验证码")
    with _lock:
        same = all(_state.get(key) == value for key, value in payload.items() if key != "verify_image")
        has_image = bool(_state.get("verify_image"))
    if same and (not shot or has_image):
        return
    _set(**payload)


def _cookies_for_save(context) -> list[dict[str, Any]]:
    out = []
    for item in _all_cookie_list(context):
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


def _iter_scopes(page):
    yield page
    try:
        for frame in page.frames:
            yield frame
    except Exception:
        return


def _capture_qr_image(page) -> str:
    selectors = [
        "#animate_qrcode_container img",
        "img[src*='qrcode']",
        "img[src*='qr']",
        "img[alt*='二维码']",
        "img[alt*='扫码']",
        "[class*='qrcode'] img",
        "[class*='qr-code'] img",
        "[class*='Qrcode'] img",
        "[class*='qrcode'] canvas",
        "[class*='scan'] img",
        "div:has-text('扫码登录') img",
        "div:has-text('打开「抖音APP」') img",
        "div:has-text('扫一扫') img",
        "div:has-text('登录后免费畅享') img",
    ]
    for scope in _iter_scopes(page):
        for selector in selectors:
            try:
                loc = scope.locator(selector).first
                if loc.count() == 0:
                    continue
                loc.wait_for(state="visible", timeout=1200)
                box = loc.bounding_box()
                if box and (box["width"] < 90 or box["height"] < 90):
                    continue
                png = loc.screenshot()
                if png and len(png) > 800:
                    logger.info("页面截到二维码 selector=%s size=%s", selector, len(png))
                    return base64.b64encode(png).decode("ascii")
            except Exception:
                continue
    logger.warning("页面上没有截到二维码 url=%s frames=%s", getattr(page, "url", ""), len(getattr(page, "frames", []) or []))
    return ""


def _wait_chat_qr(page) -> str:
    logger.info("打开抖音私信页 %s", CHAT)
    page.goto(CHAT, wait_until="domcontentloaded", timeout=60000)
    logger.info("私信页已打开 url=%s title=%s", page.url, page.title())
    try:
        page.wait_for_selector("text=扫码登录", timeout=12000)
        logger.info("已出现扫码登录弹窗")
    except Exception:
        logger.warning("12 秒内没等到「扫码登录」文案，继续截图尝试")
    qr_png = ""
    for attempt in range(12):
        if _stop.is_set():
            return ""
        qr_png = _capture_qr_image(page)
        if qr_png:
            return qr_png
        logger.info("第 %s 次未截到二维码，继续等", attempt + 1)
        time.sleep(1)
    try:
        DEBUG_SHOT.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(DEBUG_SHOT))
        logger.error("截二维码失败，已保存调试图 %s", DEBUG_SHOT)
    except Exception:
        logger.exception("保存调试截图失败")
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


def _follow_login_redirect(page, context, redirect_url: str | None):
    if not redirect_url:
        return
    try:
        page.goto(str(redirect_url), wait_until="domcontentloaded", timeout=45000)
        logger.info("已打开登录跳转 url=%s", page.url)
        return
    except Exception:
        logger.warning("页面打开 redirect_url 失败，改用请求", exc_info=True)
    try:
        _http_get(context, redirect_url)
        logger.info("已请求登录跳转")
    except Exception:
        logger.exception("跟随 redirect_url 失败")


def _finish_login(page, context, redirect_url: str | None):
    _follow_login_redirect(page, context, redirect_url)
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
            verify_methods=[],
            verify_account="",
            verify_need_code=False,
            verify_need_password=False,
            verify_image="",
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
        sniff: dict[str, Any] = {"methods": [], "token": "", "redirect": "", "account": "", "qr_status": ""}
        _attach_sniffer(page, sniff)
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
            qr_png = _wait_chat_qr(page)
        except Exception:
            logger.exception("打开抖音私信页失败")
            qr_png = ""

        token, qr_url = "", ""
        if not qr_png:
            logger.info("页面未截到二维码，再试 SSO 接口")
            token, api_png, qr_url = _request_qr(context, fp)
            qr_png = api_png
        if sniff.get("token") and not token:
            token = str(sniff.get("token") or "")
            logger.info("从页面抓包拿到扫码 token")

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
        last_shot = 0
        last_cookie_log = 0
        missing_qr = 0
        had_qr = bool(qr_png)
        verify_shot_done = False
        verify_gone = 0
        while time.time() < deadline and not _stop.is_set():
            cmd = _pop_command()
            if cmd:
                action = str(cmd.get("type") or "")
                if action == "choose":
                    _click_verify_method(page, str(cmd.get("id") or ""))
                    deadline = max(deadline, time.time() + 180)
                elif action == "code":
                    _fill_verify_code(page, str(cmd.get("code") or ""), str(cmd.get("password") or ""))
                    deadline = max(deadline, time.time() + 120)

            if sniff.get("token") and not token:
                token = str(sniff.get("token") or "")

            ui = _scan_verify_ui(page)
            in_verify = bool(ui.get("visible") or ui.get("methods") or sniff.get("methods") or sniff.get("need_verify") or _status() == "verify")
            if ui.get("visible") or ui.get("methods") or sniff.get("methods") or sniff.get("need_verify"):
                deadline = max(deadline, time.time() + 240)
                shot = ""
                if not verify_shot_done:
                    shot = _capture_verify_image(page)
                    verify_shot_done = True
                    logger.info(
                        "已进入身份验证 methods=%s account=%s snippet=%s",
                        ui.get("methods"),
                        ui.get("account"),
                        (ui.get("snippet") or "")[:180],
                    )
                _publish_verify(ui, sniff, shot)
                in_verify = True
            elif _status() == "verify":
                _publish_verify(ui, sniff)
                in_verify = True

            sniff_status = str(sniff.get("qr_status") or "")
            if not in_verify:
                if sniff_status == "2" and _status() == "waiting":
                    _set(status="scanned", message="已扫码，请在手机上确认登录")
                elif sniff_status in ("3", "4") or sniff.get("redirect"):
                    redirect = str(sniff.pop("redirect", "") or "")
                    sniff["qr_status"] = ""
                    _set(status="scanned", message="已确认，正在进入下一步…")
                    _follow_login_redirect(page, context, redirect or None)

            if token and not in_verify:
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
                    _set(status="scanned", message="已确认，正在进入下一步…")
                    _follow_login_redirect(page, context, data.get("redirect_url"))
                    token = ""

            if _has_session(context) and not in_verify:
                logger.info("已检测到登录 Cookie names=%s", list(_cookie_map(context)))
                _set(status="scanned", message="已登录，正在抓取账号信息…")
                break
            if _page_logged_in(page) and not in_verify:
                logger.info("页面已进入登录后状态")
                _set(status="scanned", message="已确认登录，正在抓取账号信息…")
                break
            if _has_session(context) and in_verify and not ui.get("visible"):
                logger.info("身份验证完成，已拿到登录 Cookie")
                _set(status="scanned", message="验证通过，正在抓取账号信息…")
                break
            if in_verify and not ui.get("visible"):
                verify_gone += 1
                if verify_gone >= 4:
                    logger.info("身份验证弹窗已关闭，刷新页面拿 Cookie")
                    try:
                        page.goto(CHAT, wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        logger.debug("验证后刷新失败", exc_info=True)
                    verify_gone = 0
            else:
                verify_gone = 0

            now = time.time()
            if now - last_cookie_log > 5:
                logger.info("等待扫码中 url=%s status=%s cookies=%s", page.url, _status(), list(_cookie_map(context)))
                last_cookie_log = now
            if _status() != "verify" and now - last_shot > 2:
                shot = _capture_qr_image(page)
                if shot:
                    had_qr = True
                    missing_qr = 0
                    _set(qr_base64=shot)
                elif had_qr:
                    missing_qr += 1
                    logger.info("二维码已消失 %s 次，检查是否进入身份验证", missing_qr)
                    if missing_qr >= 2 and not ui.get("visible"):
                        _set(status="scanned", message="二维码已消失，正在确认登录态…")
                last_shot = now
            time.sleep(1)
        else:
            if _stop.is_set():
                _set(status="idle", message="", qr_base64="")
                return
            _set(status="expired", message="身份验证超时，请刷新二维码重试" if _status() == "verify" else "等待扫码超时，请刷新二维码")
            return

        if _stop.is_set():
            _set(status="idle", message="", qr_base64="")
            return

        for i in range(12):
            if _has_session(context):
                break
            logger.info("登录后等待 Cookie %s/12 names=%s", i + 1, list(_cookie_map(context)))
            try:
                page.goto(CHAT, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                logger.debug("刷新私信页失败", exc_info=True)
            time.sleep(1)

        profile = extract_profile(page, context)
        cookies = _cookies_for_save(context)
        cookie_names = [c.get("name") for c in cookies]
        logger.info("抓到 Cookie %s 个 names=%s", len(cookies), cookie_names)
        if not cookies:
            logger.error("没有拿到任何 Cookie")
            _set(status="error", message="没有拿到有效登录 Cookie，请重新扫码")
            return
        if not _has_session(context):
            logger.warning("没有 sessionid，仍继续保存当前 Cookie")
        if not profile.get("unique_id"):
            profile["unique_id"] = _cookie_map(context).get("uid_tt") or _cookie_map(context).get("uid_tt_ss") or ""
        if not profile.get("unique_id"):
            _set(status="error", message="登录成功，但没有读到抖音号，请再扫一次")
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
    _clear_commands()
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
        verify_methods=[],
        verify_account="",
        verify_need_code=False,
        verify_need_password=False,
        verify_image="",
    )
    _thread = threading.Thread(target=_worker, args=(replace_index,), daemon=True)
    _thread.start()
    return snapshot()


def cancel_qr_login() -> dict[str, Any]:
    logger.info("收到取消扫码登录")
    _stop.set()
    _clear_commands()
    _set(
        status="idle",
        message="",
        qr_base64="",
        qr_url="",
        cookies=[],
        verify_methods=[],
        verify_account="",
        verify_need_code=False,
        verify_need_password=False,
        verify_image="",
    )
    return snapshot()


def choose_verify_method(method_id: str) -> dict[str, Any]:
    ident = str(method_id or "").strip()
    logger.info("用户选择身份验证方式 %s", ident)
    if ident:
        _push_command({"type": "choose", "id": ident})
        _set(message=f"已选择「{ident}」，正在打开验证…")
    return snapshot()


def submit_verify_code(code: str, password: str = "") -> dict[str, Any]:
    logger.info(
        "用户提交身份验证 code_len=%s has_password=%s",
        len(str(code or "").strip()),
        "yes" if str(password or "").strip() else "no",
    )
    _push_command({"type": "code", "code": str(code or "").strip(), "password": str(password or "")})
    _set(message="已提交验证信息，正在确认…")
    return snapshot()
