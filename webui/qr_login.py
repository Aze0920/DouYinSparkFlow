"""抖音扫码登录：弹出二维码，确认后抓取 Cookie、昵称和抖音号。"""
from __future__ import annotations

import base64
import json
import random
import re
import string
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

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


def is_image_qr_src(url: str) -> bool:
    raw = str(url or "").strip()
    if raw.startswith("data:image"):
        return True
    if not raw.startswith(("http://", "https://")):
        return False
    low = raw.lower().split("?", 1)[0]
    return low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))


APP_LINK_HOSTS = {"v.douyin.com", "v.iesdouyin.com"}
LANDING_HOSTS = {"api.amemv.com", "aweme.snssdk.com", "www.amemv.com"}


def is_app_jump_url(url: str) -> bool:
    raw = str(url or "").strip()
    if raw.startswith(("snssdk1128://", "aweme://", "sslocal://")):
        return True
    if not raw.startswith(("http://", "https://")):
        return False
    if raw.startswith("data:"):
        return False
    return not is_image_qr_src(raw)


def jump_url_host(url: str) -> str:
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    try:
        return (urlparse(raw).hostname or "").lower()
    except Exception:
        return ""


def is_douyin_app_link(url: str) -> bool:
    return jump_url_host(url) in APP_LINK_HOSTS


def is_login_landing_url(url: str) -> bool:
    host = jump_url_host(url)
    if not host:
        return False
    if host in LANDING_HOSTS:
        return True
    return host.endswith(".amemv.com") or host.endswith(".snssdk.com")


def jump_priority(url: str) -> tuple[int, int]:
    raw = str(url or "").strip()
    if not is_app_jump_url(raw):
        return (0, 0)
    if raw.startswith(("snssdk1128://", "aweme://", "sslocal://")):
        return (100, len(raw))
    if is_login_landing_url(raw):
        return (80, len(raw))
    if is_douyin_app_link(raw):
        return (20, len(raw))
    return (40, len(raw))


def pick_best_jump(*urls: str) -> str:
    best = ""
    best_key = (0, 0)
    for url in urls:
        key = jump_priority(url)
        if key > best_key:
            best_key = key
            best = str(url or "").strip()
    return best


def extract_jump_from_data(data: dict | None) -> str:
    if not isinstance(data, dict):
        return ""
    found: list[str] = []
    for key in (
        "short_url",
        "qrcode_short_url",
        "qr_short_url",
        "schema",
        "schema_url",
        "open_url",
        "qrcode_index_url",
        "qrcode_url",
        "url",
    ):
        raw = str(data.get(key) or "").strip()
        if is_app_jump_url(raw):
            found.append(raw)
    return pick_best_jump(*found)


def decode_qr_payload(png_b64: str) -> str:
    raw = str(png_b64 or "").strip()
    if raw.startswith("data:image"):
        raw = raw.split(",", 1)[-1]
    if len(raw) < 80:
        return ""
    try:
        import cv2
        import numpy as np
    except Exception:
        return ""
    try:
        buf = base64.b64decode(raw)
        arr = np.frombuffer(buf, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return ""
        val, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        val = str(val or "").strip()
        return val if is_app_jump_url(val) else ""
    except Exception:
        logger.debug("解码登录二维码失败", exc_info=True)
        return ""


def with_inapp_params(url: str) -> str:
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return raw
    extra = []
    if "append_common_params=" not in raw:
        extra.append("append_common_params=1")
    if "hide_nav_bar=" not in raw:
        extra.append("hide_nav_bar=1")
    if not extra:
        return raw
    sep = "&" if "?" in raw else "?"
    return raw + sep + "&".join(extra)


def douyin_webview_scheme(url: str, prefix: str = "snssdk1128") -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith(("snssdk1128://", "aweme://", "sslocal://")):
        return raw
    if not raw.startswith(("http://", "https://")):
        return ""
    return f"{prefix}://webview?url={quote(raw, safe='')}&from=webview&refer=web"


def android_intent_https(url: str) -> str:
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    hostpath = parsed.netloc + (parsed.path or "/")
    if parsed.query:
        hostpath += "?" + parsed.query
    return "intent://" + hostpath + "#Intent;scheme=https;package=com.ss.android.ugc.aweme;end"


def douyin_app_scheme(url: str) -> str:
    return douyin_webview_scheme(url, "snssdk1128")


def douyin_universal_link(schema: str) -> str:
    schema = str(schema or "").strip()
    if not schema:
        return ""
    return "https://www.douyin.com/open/sdk/ul?schema=" + quote(schema, safe="")


def _jump_fields(url: str) -> dict[str, str]:
    jump = str(url or "").strip()
    empty = {
        "app_jump_url": "",
        "app_scheme": "",
        "app_scheme_ios": "",
        "app_open_url": "",
        "app_open_url_android": "",
    }
    if not is_app_jump_url(jump):
        return empty
    if jump.startswith(("snssdk1128://", "aweme://", "sslocal://")):
        scheme = jump
        scheme_ios = ("aweme://" + jump.split("://", 1)[-1]) if "://" in jump else jump
        return {
            "app_jump_url": jump,
            "app_scheme": scheme,
            "app_scheme_ios": scheme_ios,
            "app_open_url": douyin_universal_link(scheme_ios),
            "app_open_url_android": scheme,
        }
    target = with_inapp_params(jump) if is_login_landing_url(jump) else jump
    scheme = douyin_webview_scheme(target, "snssdk1128")
    scheme_ios = douyin_webview_scheme(target, "aweme")
    intent = android_intent_https(jump)
    return {
        "app_jump_url": jump,
        "app_scheme": scheme,
        "app_scheme_ios": scheme_ios,
        "app_open_url": douyin_universal_link(scheme_ios),
        "app_open_url_android": intent or scheme,
    }

_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_commands: list[dict[str, Any]] = []
_state: dict[str, Any] = {
    "status": "idle",
    "message": "",
    "qr_base64": "",
    "qr_url": "",
    "app_jump_url": "",
    "app_scheme": "",
    "app_scheme_ios": "",
    "app_open_url": "",
    "app_open_url_android": "",
    "username": "",
    "unique_id": "",
    "cookies": [],
    "replace_index": -1,
    "started_at": 0,
    "verify_methods": [],
    "verify_account": "",
    "verify_need_code": False,
    "verify_need_password": False,
    "verify_info": "",
    "verify_kind": "",
    "verify_uplink_from": "",
    "verify_uplink_to": "",
    "verify_uplink_content": "",
    "verify_error": "",
    "verify_resend_at": 0,
    "live_html": "",
    "live_hash": "",
    "live_w": 0,
    "live_h": 0,
}
_live_box = {"x": 0, "y": 0, "w": 0, "h": 0}


def _set(**kwargs):
    with _lock:
        _state.update(kwargs)
    note = {
        k: v
        for k, v in kwargs.items()
        if k not in {
            "qr_base64",
            "qr_url",
            "app_jump_url",
            "app_scheme",
            "app_scheme_ios",
            "app_open_url",
            "app_open_url_android",
            "cookies",
            "verify_image",
            "live_image",
            "live_html",
            "live_hash",
            "live_w",
            "live_h",
        }
    }
    if note:
        logger.info("扫码状态 %s", note)


def snapshot(include_cookies: bool = False) -> dict[str, Any]:
    with _lock:
        data = {
            "status": _state.get("status") or "idle",
            "message": _state.get("message") or "",
            "qr_base64": _state.get("qr_base64") or "",
            "qr_url": _state.get("qr_url") or "",
            "app_jump_url": _state.get("app_jump_url") or "",
            "app_scheme": _state.get("app_scheme") or "",
            "app_scheme_ios": _state.get("app_scheme_ios") or "",
            "app_open_url": _state.get("app_open_url") or "",
            "app_open_url_android": _state.get("app_open_url_android") or "",
            "username": _state.get("username") or "",
            "unique_id": _state.get("unique_id") or "",
            "replace_index": int(_state.get("replace_index") or -1),
            "started_at": _state.get("started_at") or 0,
            "verify_methods": list(_state.get("verify_methods") or []),
            "verify_account": _state.get("verify_account") or "",
            "verify_need_code": bool(_state.get("verify_need_code")),
            "verify_need_password": bool(_state.get("verify_need_password")),
            "verify_info": _state.get("verify_info") or "",
            "verify_kind": _state.get("verify_kind") or "",
            "verify_uplink_from": _state.get("verify_uplink_from") or "",
            "verify_uplink_to": _state.get("verify_uplink_to") or "",
            "verify_uplink_content": _state.get("verify_uplink_content") or "",
            "verify_error": _state.get("verify_error") or "",
            "verify_resend_left": max(0, int((_state.get("verify_resend_at") or 0) - time.time())),
            "live_html": _state.get("live_html") or "",
            "live_hash": _state.get("live_hash") or "",
            "live_image": _state.get("live_image") or "",
            "live_w": int(_state.get("live_w") or 0),
            "live_h": int(_state.get("live_h") or 0),
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
        "need_short_url": "true",
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
                hasVerify: text.includes("身份验证") && (text.includes("为保障账号安全") || text.includes("确保为本人操作")),
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
    "mobile_sms_verify": "接收短信验证码",
    "receive_sms": "接收短信验证码",
    "down_sms": "接收短信验证码",
    "uplink_sms": "发送短信验证",
    "uplink_sms_verify": "发送短信验证",
    "mobile_up_sms_verify": "发送短信验证",
    "mobile_uplink_sms_verify": "发送短信验证",
    "send_sms": "发送短信验证",
    "sms_uplink": "发送短信验证",
    "up_sms": "发送短信验证",
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
VERIFY_SCAN_JS = """() => {
  const empty = {
    visible: false, methods: [], account: "", needCode: false, needPassword: false,
    info: "", sendTo: "", smsContent: "", fromMobile: "", error: "", step: "", needGetCode: false,
  };
  const bodyText = (document.body && document.body.innerText) || "";
  const isGate = bodyText.includes("身份验证") && (
    bodyText.includes("为保障账号安全") || bodyText.includes("确保为本人操作")
  );
  if (!isGate && !/我已发送|编辑短信内容|短信已发送至/.test(bodyText)) return empty;
  let dialog = null;
  let best = 1e12;
  for (const el of document.querySelectorAll("div, section, article, [role=dialog]")) {
    const t = (el.innerText || "").replace(/\\s+/g, " ").trim();
    if (t.length < 12 || t.length > 900) continue;
    if (t.includes("扫码登录") || t.includes("验证码登录") || t.includes("打开「抖音APP」")) continue;
    const mfa = t.includes("身份验证")
      || t.includes("短信已发送至")
      || (t.includes("接收短信验证码") && /验证|重新发送|请输入/.test(t));
    if (!mfa) continue;
    let score = t.length;
    if (t.includes("短信已发送至") || /\\d+\\s*s后重新发送/.test(t)) score -= 200;
    if (t.includes("接收短信验证码") || t.includes("请输入验证码")) score -= 80;
    if (score < best) { best = score; dialog = el; }
  }
  const root = dialog || document.body;
  const text = (root.innerText || "");
  const compact = text.replace(/\\s+/g, " ").trim();
  const smsSent = /短信已发送至|\\d+\\s*s后重新发送/.test(compact);
  const isUplink = /我已发送|编辑短信内容/.test(compact);
  const isCode = smsSent || /请输入验证码|验证码发送太频繁/.test(compact);
  const step = isUplink ? "uplink" : (isCode ? "sms" : "choose");
  const methods = [];
  const seen = new Set();
  const allow = /接收短信验证码|发送短信验证|邮箱验证|密码验证|人脸验证/;
  const deny = /获取验证码|用户协议|隐私政策|登录即代表|选择其他验证方式|无法验证通过/;
  if (step === "choose") {
    for (const el of root.querySelectorAll("button, [role=button], li, a, div, p, span")) {
      const t = (el.innerText || "").replace(/\\s+/g, " ").trim();
      if (!t || t.length > 10 || seen.has(t)) continue;
      if (t.includes("接收短信验证码") && t.includes("发送短信验证")) continue;
      if (deny.test(t) && !allow.test(t)) continue;
      if (!allow.test(t)) continue;
      seen.add(t);
      methods.push({ id: t, label: t });
    }
  }
  const junkAcc = /验证|短信|保障|账号安全|本人操作|读屏|扫码|登录|聊天|协议|隐私|畅享|如何/;
  const lines = text.split(/\\n+/).map((s) => s.trim()).filter(Boolean);
  let account = "";
  const head = lines.findIndex((l) => l.includes("身份验证"));
  if (head >= 0) {
    for (let i = head + 1; i < Math.min(lines.length, head + 12); i++) {
      const line = lines[i];
      if (!line || line.length > 16 || junkAcc.test(line)) continue;
      account = line;
      break;
    }
  }
  const sendTo = (compact.match(/发送至[:：]?\\s*(\\d{8,})/) || compact.match(/(1069\\d{6,})/) || [])[1] || "";
  const smsContent = (compact.match(/编辑短信内容[:：]?\\s*(\\S+)/) || compact.match(/短信内容[:：]?\\s*(\\S+)/) || [])[1] || "";
  const fromMobile = (compact.match(/(1[3-9][0-9\\*]{9})/) || [])[1] || "";
  const err = (compact.match(/验证码发送太频繁[^ ]*|验证码错误[^ ]*|验证失败[^ ]*|验证码不正确[^ ]*|请稍后再试|发送失败[^ ]*/) || [])[0] || "";
  const needGetCode = !smsSent && [...root.querySelectorAll("button, [role=button], span, div, a")].some((el) => {
    const t = (el.innerText || "").replace(/\\s+/g, " ").trim();
    if (t !== "获取验证码") return false;
    let n = el;
    for (let i = 0; i < 6 && n && n !== document.body; i++) {
      const ctx = (n.innerText || "").replace(/\\s+/g, " ");
      if (ctx.length > 400) break;
      if (/验证码登录|密码登录/.test(ctx)) return false;
      n = n.parentElement;
    }
    return true;
  });
  return {
    visible: true,
    methods,
    account,
    needCode: step === "sms",
    needPassword: false,
    info: compact.slice(0, 400),
    sendTo: (sendTo || "").replace(/[:：]/g, ""),
    smsContent: (smsContent || "").replace(/[:：]/g, ""),
    fromMobile,
    error: err,
    step,
    needGetCode,
  };
}"""


def _interesting_url(url: str) -> bool:
    text = (url or "").lower()
    return any(hint in text for hint in SNIFF_URL_HINTS)


def _extract_mobile(text: str) -> str:
    found = re.search(r"1[3-9][\d*]{9}", str(text or ""))
    return found.group(0) if found else ""


def _clean_verify_account(text: str) -> str:
    raw = str(text or "").strip()
    if not raw or len(raw) > 16:
        return ""
    junk = ("开启读屏", "读屏标签", "抖音聊天", "扫码登录", "如何扫码", "验证码登录", "密码登录", "获取验证码", "用户协议", "身份验证")
    if any(item in raw for item in junk):
        return ""
    return raw


def _human_verify_label(way: str = "", name: str = "", mobile: str = "") -> str:
    ident = str(way or "").strip()
    raw = str(name or ident).strip()
    mapped = VERIFY_WAY_LABELS.get(ident) or VERIFY_WAY_LABELS.get(ident.lower())
    if not mapped and re.fullmatch(r"[a-zA-Z0-9_]+", raw or ""):
        mapped = VERIFY_WAY_LABELS.get(raw.lower())
    if mapped:
        label = mapped
    elif re.fullmatch(r"[a-zA-Z0-9_]+", raw or ""):
        label = "短信验证"
    else:
        label = raw or "验证方式"
    phone = mobile or _extract_mobile(raw) or _extract_mobile(ident)
    if phone and phone not in label:
        label = f"{label}（{phone}）"
    return label


def _method_kind(text: str) -> str:
    blob = str(text or "").lower()
    if "up" in blob and "sms" in blob:
        return "uplink"
    if "发送短信" in blob and "验证码" not in blob:
        return "uplink"
    if "sms" in blob or "短信" in blob:
        return "sms"
    if "email" in blob or "邮箱" in blob:
        return "email"
    if "pwd" in blob or "password" in blob or "密码" in blob:
        return "password"
    return ""


def _uniq_methods(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    seen = set()
    for item in rows or []:
        ident = str(item.get("id") or item.get("label") or "").strip()
        raw_label = str(item.get("label") or ident).strip()
        way = ident if re.fullmatch(r"[a-zA-Z0-9_]+", ident or "") else ""
        if not way and re.fullmatch(r"[a-zA-Z0-9_]+", raw_label or ""):
            way = raw_label
        label = _human_verify_label(way, raw_label)
        kind = _method_kind(way + " " + label)
        key = way.lower() or label
        if not label or key in seen:
            continue
        junk = ("用户协议", "隐私政策", "登录即代表", "获取验证码", "选择其他", "无法验证", "刷新二维码")
        if any(j in label for j in junk) and "接收短信验证码" not in label and "发送短信验证" not in label:
            continue
        if "接收短信验证码" in label and "发送短信验证" in label:
            continue
        if not way and not any(k in label for k in ("接收短信验证码", "发送短信验证", "邮箱验证", "密码验证", "人脸验证")):
            continue
        seen.add(key)
        out.append({"id": way or label, "label": label, "kind": kind})
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
            label = _human_verify_label(str(way or ""), str(name or ""), str(mobile or ""))
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


def _collect_verify_detail(obj: Any, bag: dict[str, Any], depth: int = 0):
    if depth > 8 or obj is None:
        return
    if isinstance(obj, list):
        for item in obj:
            _collect_verify_detail(item, bag, depth + 1)
        return
    if not isinstance(obj, dict):
        return
    for key, value in obj.items():
        if value in (None, "", [], {}):
            continue
        low = str(key).lower()
        text = str(value)
        found_1069 = re.search(r"1069\d{6,}", text)
        if found_1069:
            bag["uplink_to"] = found_1069.group(0)
        if low in {"sms_content", "up_sms_content", "uplink_content", "uplink_sms_content", "message_content", "code_content", "sms_code_content"}:
            bag["uplink_content"] = text
        elif re.fullmatch(r"[A-Za-z]{2,8}", text) and "content" in low:
            bag["uplink_content"] = text
        elif low in {"dst", "dst_num", "dst_mobile", "sp_number", "sms_port", "port", "target_number", "up_sms_mobile", "send_to", "receive_number"}:
            bag["uplink_to"] = re.sub(r"\D", "", text) or text
        elif low in {"mask_mobile", "mobile_mask", "from_mobile"}:
            bag["uplink_from"] = text
        elif low == "mobile" and (text.startswith("106") or "*" in text):
            if text.startswith("106"):
                bag["uplink_to"] = re.sub(r"\D", "", text) or text
            else:
                bag["uplink_from"] = bag.get("uplink_from") or text
        elif low in {"need_code", "need_sms_code"} and value in (True, 1, "1", "true"):
            bag["need_code"] = True
        if isinstance(value, (dict, list)):
            _collect_verify_detail(value, bag, depth + 1)


def _ingest_packet(url: str, payload: Any, bag: dict[str, Any]):
    if not isinstance(payload, dict):
        return
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        data = payload
    token = data.get("token")
    if token:
        bag["token"] = str(token)
    jump_src = extract_jump_from_data(data)
    qrcode = data.get("qrcode")
    decoded = decode_qr_payload(str(qrcode or "")) if isinstance(qrcode, str) else ""
    jump_src = pick_best_jump(str(bag.get("app_jump_url") or ""), jump_src, decoded)
    if jump_src:
        bag["app_jump_url"] = jump_src
    qr_url = data.get("qrcode_url") or data.get("frontend_show_qrcode") or data.get("qr_url") or ""
    if isinstance(qr_url, str) and is_image_qr_src(qr_url):
        bag["qr_url"] = qr_url
    elif isinstance(qr_url, str) and qr_url.startswith("data:image"):
        bag["qr_url"] = qr_url
    qrcode = data.get("qrcode")
    if isinstance(qrcode, str) and len(qrcode) > 80:
        bag["qr_url"] = qrcode if qrcode.startswith("data:") else f"data:image/png;base64,{qrcode.split(',', 1)[-1]}"
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
    flow = data.get("account_flow") or data.get("flow")
    if isinstance(flow, str) and flow.strip():
        bag["account_flow"] = flow.strip()
    echo = data.get("description") or data.get("message") or data.get("msg") or data.get("toast") or data.get("error_message")
    if isinstance(echo, str) and echo.strip() and echo.strip().lower() not in {"success", "ok", "check pass"}:
        bag["echo"] = echo.strip()
        logger.info("抓包回传信息 %s", echo.strip()[:200])
    if "send_code" in (url or "").lower():
        if str(payload.get("message") or "").lower() == "success" or data.get("retry_time") or data.get("mobile"):
            bag["sms_sent"] = True
            if data.get("mobile"):
                bag["uplink_from"] = bag.get("uplink_from") or str(data.get("mobile"))
            logger.info("抓包短信已发送 mobile=%s retry=%s", data.get("mobile"), data.get("retry_time"))
    methods: list[dict[str, str]] = []
    _collect_methods_from_obj(payload, methods)
    if methods:
        bag["methods"] = _uniq_methods((bag.get("methods") or []) + methods)
        logger.info("抓包验证方式 %s", bag["methods"])
    _collect_verify_detail(payload, bag)


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
    merged = {
        "visible": False,
        "methods": [],
        "account": "",
        "needCode": False,
        "needPassword": False,
        "info": "",
        "sendTo": "",
        "smsContent": "",
        "fromMobile": "",
        "error": "",
        "step": "",
        "needGetCode": False,
    }
    for scope in _iter_scopes(page):
        try:
            part = scope.evaluate(VERIFY_SCAN_JS) or {}
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
        if part.get("info") and not merged["info"]:
            merged["info"] = str(part.get("info") or "")
        if part.get("sendTo") and not merged["sendTo"]:
            merged["sendTo"] = str(part.get("sendTo") or "")
        if part.get("smsContent") and not merged["smsContent"]:
            merged["smsContent"] = str(part.get("smsContent") or "")
        if part.get("fromMobile") and not merged["fromMobile"]:
            merged["fromMobile"] = str(part.get("fromMobile") or "")
        if part.get("error") and not merged["error"]:
            merged["error"] = str(part.get("error") or "")
        if part.get("step") and not merged["step"]:
            merged["step"] = str(part.get("step") or "")
        if part.get("needGetCode"):
            merged["needGetCode"] = True
        merged["methods"].extend(part.get("methods") or [])
    merged["methods"] = _uniq_methods(merged["methods"])
    return merged


def _click_verify_method(page, method_id: str, label: str = "") -> bool:
    ident = str(method_id or "").strip()
    zh = _human_verify_label(ident, label)
    keys = [k for k in (zh, label, "接收短信验证码", ident) if k]
    keys = list(dict.fromkeys(keys))
    click_js = """(keys) => {
      const nodes = [...document.querySelectorAll("button, [role=button], li, a, p, span, div")];
      const visible = (el) => {
        const r = el.getBoundingClientRect();
        if (r.width < 8 || r.height < 8) return false;
        const cs = getComputedStyle(el);
        return cs.display !== "none" && cs.visibility !== "hidden" && Number(cs.opacity || 1) > 0.05;
      };
      const isLeaf = (el, t) => {
        for (const child of el.children) {
          const ct = (child.innerText || "").replace(/\\s+/g, " ").trim();
          if (ct === t) return false;
        }
        return true;
      };
      for (const want of keys) {
        const target = String(want || "").replace(/\\s+/g, " ").trim();
        if (!target) continue;
        let best = null;
        let bestLen = 1e9;
        for (const el of nodes) {
          const t = (el.innerText || "").replace(/\\s+/g, " ").trim();
          if (!t || t.length > 22) continue;
          if (/发送短信验证|人脸验证|邮箱验证|密码验证|无法验证|选择其他|用户协议|隐私政策/.test(t) && t !== target) continue;
          const ok = t === target || t.startsWith(target + "（") || t.startsWith(target + "(");
          if (!ok || !visible(el) || !isLeaf(el, t)) continue;
          if (t.length < bestLen) {
            bestLen = t.length;
            best = el;
          }
        }
        if (best) {
          best.click();
          return (best.innerText || "").replace(/\\s+/g, " ").trim();
        }
      }
      return "";
    }"""
    for scope in _iter_scopes(page):
        try:
            clicked = scope.evaluate(click_js, keys)
            if clicked:
                logger.info("已点击验证方式 %s -> %s", ident, clicked)
                return True
        except Exception:
            continue
        for text in keys:
            try:
                loc = scope.get_by_text(text, exact=True)
                if loc.count() == 0:
                    continue
                loc.first.click(timeout=2000)
                logger.info("已精确点击验证方式 %s", text)
                return True
            except Exception:
                continue
        try:
            loc = scope.locator("div, section, [role=dialog]").filter(has_text="身份验证").get_by_text("接收短信验证码", exact=True)
            if loc.count():
                loc.first.click(timeout=2000)
                logger.info("已在身份验证卡片点击接收短信验证码")
                return True
        except Exception:
            continue
    logger.warning("没有点到验证方式 id=%s keys=%s", ident, keys)
    return False


def _click_get_sms_code(page) -> bool:
    labels = ["获取验证码", "发送验证码", "获取短信验证码"]
    click_js = """(labels) => {
      const nodes = [...document.querySelectorAll("button, [role=button], a, span, div, p")];
      const visible = (el) => {
        const r = el.getBoundingClientRect();
        if (r.width < 8 || r.height < 8) return false;
        const cs = getComputedStyle(el);
        return cs.display !== "none" && cs.visibility !== "hidden";
      };
      const isLeaf = (el, t) => {
        for (const child of el.children) {
          const ct = (child.innerText || "").replace(/\\s+/g, " ").trim();
          if (ct === t) return false;
        }
        return true;
      };
      const inLoginForm = (el) => {
        let n = el;
        for (let i = 0; i < 8 && n && n !== document.body; i++) {
          const ctx = (n.innerText || "").replace(/\\s+/g, " ").trim();
          if (ctx.length > 400) break;
          if (/验证码登录|密码登录/.test(ctx)) return true;
          n = n.parentElement;
        }
        return false;
      };
      const inVerify = (el) => {
        let n = el;
        for (let i = 0; i < 10 && n && n !== document.body; i++) {
          const ctx = (n.innerText || "").replace(/\\s+/g, " ").trim();
          if (ctx.length > 900) break;
          if (ctx.includes("身份验证") && /请输入验证码|短信已发送/.test(ctx)) return true;
          n = n.parentElement;
        }
        return false;
      };
      for (const want of labels) {
        let best = null;
        for (const el of nodes) {
          const t = (el.innerText || "").replace(/\\s+/g, " ").trim();
          if (t !== want || !visible(el) || !isLeaf(el, t) || inLoginForm(el) || !inVerify(el)) continue;
          best = el;
          break;
        }
        if (best) {
          best.click();
          return want;
        }
      }
      return "";
    }"""
    for scope in _iter_scopes(page):
        try:
            clicked = scope.evaluate(click_js, labels)
            if clicked:
                logger.info("已点击身份验证「获取验证码」 -> %s", clicked)
                return True
        except Exception:
            continue
        try:
            loc = scope.locator("div, section, [role=dialog]").filter(has_text="身份验证").get_by_text("获取验证码", exact=True)
            if loc.count():
                loc.first.click(timeout=2000)
                logger.info("已精确点击身份验证「获取验证码」")
                return True
        except Exception:
            continue
    logger.warning("没有点到身份验证里的「获取验证码」，短信不会发出")
    return False


def _click_exact_text(page, texts: list[str]) -> str:
    labels = [str(t).strip() for t in texts if str(t).strip()]
    click_js = """(labels) => {
      const nodes = [...document.querySelectorAll("button, [role=button], a, div, span, p, li")];
      for (const want of labels) {
        const hit = nodes.find((el) => {
          const t = (el.innerText || "").replace(/\\s+/g, " ").trim();
          if (t !== want) return false;
          if (/无法验证|选择其他|用户协议|隐私政策/.test(t)) return false;
          return true;
        });
        if (hit) {
          hit.click();
          return want;
        }
      }
      return "";
    }"""
    for scope in _iter_scopes(page):
        try:
            clicked = scope.evaluate(click_js, labels)
            if clicked:
                logger.info("已精确点击 %s", clicked)
                return str(clicked)
        except Exception:
            continue
        for text in labels:
            try:
                loc = scope.get_by_text(text, exact=True)
                if loc.count() == 0:
                    continue
                loc.first.click(timeout=2000)
                logger.info("已精确点击 %s", text)
                return text
            except Exception:
                continue
    logger.warning("没有精确点到 %s", labels)
    return ""


def _fill_verify_code(page, code: str, password: str = "") -> bool:
    fill_js = """(payload) => {
      const code = String(payload.code || "");
      const password = String(payload.password || "");
      const setValue = (el, value) => {
        const proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
        if (proto && proto.set) proto.set.call(el, value);
        else el.value = value;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        el.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true }));
      };
      const walk = (el, pred) => {
        let n = el;
        for (let i = 0; i < 10 && n && n !== document.body; i++) {
          const ctx = (n.innerText || "").replace(/\\s+/g, " ").trim();
          if (ctx.length > 900) break;
          if (pred(ctx)) return true;
          n = n.parentElement;
        }
        return false;
      };
      const inLogin = (el) => walk(el, (ctx) => /验证码登录|密码登录/.test(ctx));
      const inMfa = (el) => walk(el, (ctx) => /短信已发送至|身份验证/.test(ctx) && !/验证码登录/.test(ctx));
      const allInputs = [...document.querySelectorAll("input")];
      const mfaInputs = allInputs.filter((el) => inMfa(el) && !inLogin(el));
      const pool = mfaInputs.length ? mfaInputs : allInputs.filter((el) => !inLogin(el));
      let filled = false;
      if (password) {
        const pwd = pool.find((el) => (el.type || "") === "password");
        if (pwd) { setValue(pwd, password); filled = true; }
      }
      if (code) {
        const box = pool.find((el) => /验证码|校验码|code/i.test((el.placeholder || "") + (el.name || "")))
          || pool.find((el) => Number(el.maxLength) === 4 || Number(el.maxLength) === 6)
          || pool.find((el) => (el.type || "") === "tel" || (el.type || "") === "number" || (el.type || "") === "text");
        if (box) { setValue(box, code); filled = true; }
      }
      const btn = [...document.querySelectorAll("button, [role=button], div, span")].find((el) => {
        const t = (el.innerText || "").replace(/\\s+/g, " ").trim();
        return t === "验证" && inMfa(el) && !inLogin(el);
      }) || [...document.querySelectorAll("button, [role=button], div, span")].find((el) => {
        return (el.innerText || "").replace(/\\s+/g, " ").trim() === "验证";
      });
      if (btn) btn.click();
      return { filled, clicked: !!btn, inputCount: pool.length, mfaInputs: mfaInputs.length };
    }"""
    last = {}
    for scope in _iter_scopes(page):
        try:
            last = scope.evaluate(fill_js, {"code": code, "password": password}) or {}
        except Exception:
            continue
        logger.info("提交验证码结果 %s", last)
        if last.get("filled"):
            if not last.get("clicked"):
                _click_exact_text(page, ["验证"])
            return True
    logger.warning("没有把验证码填进抖音页面 last=%s", last)
    return False


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


MARK_LIVE_CARD_JS = (Path(__file__).resolve().parent / "mark_live_card.js").read_text(encoding="utf-8")


def _capture_live_card(page) -> bool:
    global _live_box
    best_info = None
    best_scope = None
    best_score = -1e9
    for scope in _iter_scopes(page):
        try:
            info = scope.evaluate(f"({MARK_LIVE_CARD_JS.strip()})()")
        except Exception:
            logger.debug("标记抖音卡片失败", exc_info=True)
            continue
        if not info or not info.get("ok"):
            continue
        score = float(info.get("score") or 0)
        if score > best_score:
            best_score = score
            best_info = info
            best_scope = scope
    if not best_scope or not best_info:
        return False
    loc = best_scope.locator("[data-sparkflow-live='1']").first
    try:
        raw = loc.screenshot(type="png")
        box = loc.bounding_box()
    except Exception:
        logger.debug("截取抖音卡片失败 info=%s", best_info, exc_info=True)
        return False
    if not raw:
        return False
    b64 = base64.b64encode(raw).decode("ascii")
    digest = str(len(b64)) + ":" + b64[:32]
    with _lock:
        if box:
            _live_box.update(
                {
                    "x": float(box.get("x") or 0),
                    "y": float(box.get("y") or 0),
                    "w": float(box.get("width") or best_info.get("w") or 0),
                    "h": float(box.get("height") or best_info.get("h") or 0),
                }
            )
        if (_state.get("live_hash") or "") == digest:
            return True
    _set(
        live_image=b64,
        live_html="",
        live_hash=digest,
        live_w=int(best_info.get("w") or 0),
        live_h=int(best_info.get("h") or 0),
    )
    if best_info.get("verify"):
        logger.info(
            "已截取身份验证卡片 %sx%s text=%s",
            best_info.get("w"),
            best_info.get("h"),
            best_info.get("text"),
        )
    return True

def _do_live_click(page, rx, ry) -> bool:
    with _lock:
        box = dict(_live_box)
    if float(box.get("w") or 0) <= 0 or float(box.get("h") or 0) <= 0:
        logger.warning("同步点击失败：还没有抖音卡片坐标")
        return False
    try:
        ratio_x = max(0.0, min(1.0, float(rx)))
        ratio_y = max(0.0, min(1.0, float(ry)))
    except (TypeError, ValueError):
        return False
    x = box["x"] + ratio_x * box["w"]
    y = box["y"] + ratio_y * box["h"]
    logger.info("同步点击抖音卡片 x=%.1f y=%.1f", x, y)
    try:
        page.mouse.click(x, y)
    except Exception:
        logger.exception("同步点击抖音卡片失败")
        return False
    return True


def _playwright_key(key: str) -> str:
    raw = str(key or "").strip()
    aliases = {" ": "Space"}
    raw = aliases.get(raw, raw)
    allowed = {
        "Backspace",
        "Enter",
        "Escape",
        "Delete",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "Home",
        "End",
        "Space",
        "Control+A",
        "Control+a",
    }
    return raw if raw in allowed else ""


def _handle_live_command(page, cmd: dict[str, Any]) -> None:
    action = str(cmd.get("type") or "")
    if action == "live_click":
        _do_live_click(page, cmd.get("x"), cmd.get("y"))
        time.sleep(0.22)
        _capture_live_card(page)
        return
    if action == "live_type":
        text = str(cmd.get("text") or "")[:120]
        if text:
            logger.info("同步输入抖音卡片 len=%s", len(text))
            try:
                page.keyboard.type(text, delay=18)
            except Exception:
                logger.exception("同步输入失败")
        time.sleep(0.12)
        _capture_live_card(page)
        return
    if action == "live_key":
        key = _playwright_key(str(cmd.get("key") or ""))
        if key:
            logger.info("同步按键 %s", key)
            try:
                page.keyboard.press(key)
            except Exception:
                logger.exception("同步按键失败")
        time.sleep(0.12)
        _capture_live_card(page)
        return
    if action == "live_fill":
        text = str(cmd.get("text") or "")[:120]
        logger.info("同步填写抖音卡片 len=%s", len(text))
        _do_live_fill(page, text)
        time.sleep(0.12)
        _capture_live_card(page)


def _do_live_fill(page, text: str) -> bool:
    fill_js = """(value) => {
      const setValue = (node, v) => {
        const proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")
          || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value");
        if (proto && proto.set) proto.set.call(node, v);
        else node.value = v;
        node.dispatchEvent(new Event("input", { bubbles: true }));
        node.dispatchEvent(new Event("change", { bubbles: true }));
      };
      const el = document.activeElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA") && el.type !== "hidden") {
        setValue(el, value);
        return true;
      }
      const box = [...document.querySelectorAll("input, textarea")].find((n) => {
        const type = (n.type || "").toLowerCase();
        return type !== "hidden" && type !== "checkbox" && type !== "radio";
      });
      if (box) {
        setValue(box, value);
        return true;
      }
      return false;
    }"""
    for scope in _iter_scopes(page):
        try:
            if scope.evaluate(fill_js, text):
                return True
        except Exception:
            continue
    try:
        page.keyboard.press("Control+A")
        page.keyboard.type(text, delay=12)
        return True
    except Exception:
        logger.exception("同步填写失败")
        return False


def _status() -> str:
    with _lock:
        return str(_state.get("status") or "idle")


def qr_busy() -> bool:
    return _status() in {"loading", "waiting", "scanned", "verify"}


def _publish_verify(ui: dict[str, Any], sniff: dict[str, Any]):
    account = _clean_verify_account(ui.get("account")) or _clean_verify_account(sniff.get("account")) or ""
    uplink_from = str(ui.get("fromMobile") or sniff.get("uplink_from") or _state.get("verify_uplink_from") or "")
    error = str(ui.get("error") or "")
    echo = str(sniff.get("echo") or "")
    if any(k in echo for k in ("频繁", "失败", "稍后再试", "不正确", "无匹配")):
        error = echo
    keep = str(_state.get("message") or "")
    info = str(ui.get("info") or "")
    sms_sent = bool(sniff.get("sms_sent")) or "短信已发送" in info or bool(re.search(r"\d+\s*s后重新发送", info))
    if any(k in keep for k in ("已提交", "已把验证码", "正在确认")):
        message = keep
    elif sms_sent:
        message = "验证码已发送，请填写后点确定"
    else:
        message = "请输入短信验证码"
    payload = {
        "status": "verify",
        "message": message,
        "qr_base64": "",
        "qr_url": "",
        "verify_need_code": True,
        "verify_need_password": False,
        "verify_info": "",
        "verify_methods": [],
        "verify_kind": "sms",
        "verify_uplink_from": uplink_from or account or str(_state.get("verify_uplink_from") or ""),
        "verify_uplink_to": "",
        "verify_uplink_content": "",
        "verify_error": error or str(_state.get("verify_error") or ""),
        "verify_account": account or str(_state.get("verify_account") or ""),
    }
    with _lock:
        same = all(_state.get(key) == value for key, value in payload.items())
    if same:
        return
    _set(**payload)


def is_display_unique_id(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "undefined", "0"}:
        return False
    if text.startswith("MS4wLjAB"):
        return False
    if re.fullmatch(r"[0-9a-fA-F]{16,64}", text):
        return False
    return True


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
        if (
            nick
            and not found["username"]
            and isinstance(nick, str)
            and nick not in ("douyin", "抖音")
            and not re.fullmatch(r"[0-9a-fA-F]{16,64}", nick)
        ):
            found["username"] = nick.strip()
        if uid and isinstance(uid, (str, int)) and is_display_unique_id(uid):
            text = str(uid).strip()
            if not found["unique_id"] or not is_display_unique_id(found["unique_id"]):
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


def extract_profile(page, context, allow_stop: bool = True) -> dict[str, str]:
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
        if got.get("username") and not found["username"] and not re.fullmatch(r"[0-9a-fA-F]{16,64}", str(got.get("username") or "")):
            found["username"] = got["username"]
        if got.get("unique_id") and is_display_unique_id(got.get("unique_id")) and not found["unique_id"]:
            found["unique_id"] = got["unique_id"]
        if found["username"] and found["unique_id"]:
            logger.info("已抓到账号资料 username=%s unique_id=%s", found["username"], found["unique_id"])
            break

    if not (found["username"] and found["unique_id"]):
        for url in (HOME + "/", HOME + "/user/self", HOME + "/chat"):
            if allow_stop and _stop.is_set():
                break
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                logger.debug("打开资料页 %s", page.url)
                time.sleep(1.2)
            except Exception:
                logger.exception("打开资料页失败 %s", url)
                continue
            got = _try_page_user(page)
            if got.get("username") and not found["username"] and not re.fullmatch(r"[0-9a-fA-F]{16,64}", str(got.get("username") or "")):
                found["username"] = got["username"]
            if got.get("unique_id") and is_display_unique_id(got.get("unique_id")) and not found["unique_id"]:
                found["unique_id"] = got["unique_id"]
            if found["username"] and found["unique_id"]:
                break

    cookies = _cookie_map(context)
    if not is_display_unique_id(found["unique_id"]):
        found["unique_id"] = ""
    if found["username"] and not is_display_unique_id(found["username"]) and re.fullmatch(r"[0-9a-fA-F]{16,64}", found["username"] or ""):
        found["username"] = ""
    if not found["username"]:
        found["username"] = found["unique_id"] or "抖音账号"
    logger.info("最终账号资料 username=%s unique_id=%s cookie_uid_tt=%s", found["username"], found["unique_id"], (cookies.get("uid_tt") or "")[:8])
    return found


def _iter_scopes(page):
    yield page
    try:
        for frame in page.frames:
            yield frame
    except Exception:
        return


EXTRACT_QR_JS = """() => {
  const toSrc = (img) => {
    if (!img) return "";
    const src = img.currentSrc || img.src || img.getAttribute("src") || "";
    const w = img.naturalWidth || img.width || 0;
    const h = img.naturalHeight || img.height || 0;
    if (w && (w < 80 || h < 80)) return "";
    return src || "";
  };
  const blobToData = (img) => {
    try {
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth || img.width || 0;
      canvas.height = img.naturalHeight || img.height || 0;
      if (canvas.width < 80 || canvas.height < 80) return "";
      canvas.getContext("2d").drawImage(img, 0, 0);
      return canvas.toDataURL("image/png");
    } catch (e) {
      return "";
    }
  };
  const normalize = (img) => {
    let src = toSrc(img);
    if (src.startsWith("blob:")) src = blobToData(img) || src;
    return src;
  };
  const selectors = [
    "#animate_qrcode_container img",
    "img[src*='qrcode']",
    "img[src*='qr']",
    "[class*='qrcode'] img",
    "[class*='Qrcode'] img",
    "[class*='qr-code'] img",
  ];
  for (const sel of selectors) {
    const src = normalize(document.querySelector(sel));
    if (src && !src.startsWith("blob:")) return { src, sel };
  }
  for (const img of document.querySelectorAll("img")) {
    const w = img.naturalWidth || img.width || 0;
    const h = img.naturalHeight || img.height || 0;
    if (w < 120 || h < 120 || w > 480 || Math.abs(w - h) > 40) continue;
    const src = normalize(img);
    if (src && !src.startsWith("blob:")) return { src, sel: "square-img" };
  }
  return { src: "", sel: "" };
}"""


def _extract_qr_url(page) -> str:
    for scope in _iter_scopes(page):
        try:
            data = scope.evaluate(EXTRACT_QR_JS) or {}
        except Exception:
            continue
        src = str(data.get("src") or "").strip()
        if src:
            logger.info("拿到二维码 url selector=%s prefix=%s", data.get("sel"), src[:120])
            return src
    logger.warning("页面上没有二维码 url=%s frames=%s", getattr(page, "url", ""), len(getattr(page, "frames", []) or []))
    return ""


def _wait_chat_qr(page) -> str:
    logger.info("打开抖音私信页 %s", CHAT)
    page.goto(CHAT, wait_until="domcontentloaded", timeout=60000)
    logger.info("私信页已打开 url=%s title=%s", page.url, page.title())
    try:
        page.wait_for_selector("text=扫码登录", timeout=12000)
        logger.info("已出现扫码登录弹窗")
        _capture_live_card(page)
    except Exception:
        logger.warning("12 秒内没等到「扫码登录」文案，继续取二维码地址")
    for attempt in range(12):
        if _stop.is_set():
            return ""
        _capture_live_card(page)
        qr_url = _extract_qr_url(page)
        if qr_url:
            return qr_url
        logger.info("第 %s 次未拿到二维码地址，继续等", attempt + 1)
        time.sleep(1)
    logger.error("没有拿到二维码地址")
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
    if not isinstance(data, dict):
        data = {}
    token = str(data.get("token") or "")
    qrcode = str(data.get("qrcode") or "")
    if qrcode.startswith("data:image"):
        qrcode = qrcode.split(",", 1)[-1]
    jump = pick_best_jump(extract_jump_from_data(data), decode_qr_payload(qrcode))
    logger.info(
        "get_qrcode token=%s qr_png=%s jump=%s jump_host=%s payload=%s",
        "yes" if token else "no",
        len(qrcode),
        "yes" if jump else "no",
        (jump_url_host(jump) or jump[:48]),
        _brief_payload(payload),
    )
    return token, qrcode, jump


def _fetch_qr_in_page(page, fp: str) -> tuple[str, str, str]:
    try:
        payload = page.evaluate(
            """async ({ url, params }) => {
              const u = new URL(url);
              Object.entries(params || {}).forEach(([k, v]) => u.searchParams.set(k, String(v)));
              const r = await fetch(u.toString(), { credentials: "include" });
              const text = await r.text();
              try { return JSON.parse(text); } catch (e) { return { _raw: String(text || "").slice(0, 120) }; }
            }""",
            {"url": SSO + "/get_qrcode/", "params": _sso_params(fp)},
        )
    except Exception:
        logger.debug("页面内 fetch get_qrcode 失败", exc_info=True)
        return "", "", ""
    if not isinstance(payload, dict) or payload.get("_raw"):
        logger.warning("页面内 get_qrcode 非 JSON %s", (payload or {}).get("_raw") if isinstance(payload, dict) else type(payload))
        return "", "", ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return "", "", ""
    token = str(data.get("token") or "")
    qrcode = str(data.get("qrcode") or "")
    if qrcode.startswith("data:image"):
        qrcode = qrcode.split(",", 1)[-1]
    jump = pick_best_jump(extract_jump_from_data(data), decode_qr_payload(qrcode))
    logger.info(
        "页面 fetch get_qrcode token=%s qr_png=%s jump=%s jump_host=%s",
        "yes" if token else "no",
        len(qrcode),
        "yes" if jump else "no",
        jump_url_host(jump) or jump[:48],
    )
    return token, qrcode, jump


def _check_qr(context, fp: str, token: str) -> dict[str, Any]:
    params = _sso_params(fp)
    params["token"] = token
    try:
        resp = _http_get(context, SSO + "/check_qrconnect/", params)
        status = int(getattr(resp, "status", 0) or 0)
        try:
            body = (resp.text() or "").strip()
        except Exception:
            body = ""
        if not body:
            logger.debug("check_qrconnect HTTP %s 空响应，改信页面抓包", status)
            return {}
        if body[0] not in "{[":
            logger.debug("check_qrconnect HTTP %s 非 JSON: %s", status, body[:120])
            return {}
        payload = json.loads(body)
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return {}
        logger.debug("check_qrconnect HTTP %s status=%s", status, data.get("status"))
        return data
    except Exception:
        logger.debug("check_qrconnect 解析失败", exc_info=True)
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
            app_jump_url="",
            app_scheme="",
            app_scheme_ios="",
            app_open_url="",
            app_open_url_android="",
            username="",
            unique_id="",
            cookies=[],
            replace_index=replace_index,
            started_at=time.time(),
            verify_methods=[],
            verify_account="",
            verify_need_code=False,
            verify_need_password=False,
            verify_info="",
            verify_kind="",
            verify_uplink_from="",
            verify_uplink_to="",
            verify_uplink_content="",
            live_html="",
            live_hash="",
            live_w=0,
            live_h=0,
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
        sniff: dict[str, Any] = {"methods": [], "token": "", "redirect": "", "account": "", "qr_status": "", "app_jump_url": ""}
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
            page.goto(HOME, wait_until="domcontentloaded", timeout=25000)
            logger.info("已打开抖音首页 url=%s", page.url)
        except Exception:
            logger.exception("打开抖音首页失败")

        token, api_png, api_jump = _request_qr(context, fp)
        if not token and not api_png and not api_jump:
            token, api_png, api_jump = _fetch_qr_in_page(page, fp)
        token = token or str(sniff.get("token") or "")
        app_jump = pick_best_jump(api_jump, str(sniff.get("app_jump_url") or ""), decode_qr_payload(api_png))
        qr_url = ""
        if api_png:
            qr_url = api_png if str(api_png).startswith("data:") else f"data:image/png;base64,{api_png}"
        sniff_img = str(sniff.get("qr_url") or "")
        if not qr_url and (is_image_qr_src(sniff_img) or sniff_img.startswith("data:image")):
            qr_url = sniff_img

        if not qr_url and not app_jump:
            logger.info("接口未拿到登录码，回退打开私信页")
            try:
                qr_url = _wait_chat_qr(page)
            except Exception:
                logger.exception("打开抖音私信页失败")
                qr_url = ""
            token = token or str(sniff.get("token") or "")
            app_jump = pick_best_jump(app_jump, str(sniff.get("app_jump_url") or ""))
            if sniff.get("qr_url") and not qr_url:
                maybe = str(sniff.get("qr_url") or "")
                if is_image_qr_src(maybe) or maybe.startswith("data:image"):
                    qr_url = maybe
                    logger.info("从页面抓包拿到二维码图片")
            if sniff.get("token") and not token:
                token = str(sniff.get("token") or "")
                logger.info("从页面抓包拿到扫码 token")

        if not qr_url:
            logger.error("获取二维码失败：没有二维码地址")
            _set(status="error", message="获取二维码失败，请稍后点「刷新二维码」再试", **_jump_fields(""))
            return

        jump_fields = _jump_fields(app_jump)
        logger.info(
            "登录码已就绪 jump=%s host=%s token=%s",
            "yes" if jump_fields.get("app_jump_url") else "no",
            jump_url_host(jump_fields.get("app_jump_url") or "") or (jump_fields.get("app_jump_url") or "")[:48],
            "yes" if token else "no",
        )
        _set(
            status="waiting",
            message="手机点「打开抖音 App」，应出现「登录抖音网页版」。确认后如需验证码，回到本页填写",
            qr_base64="",
            qr_url=qr_url,
            **jump_fields,
        )

        deadline = time.time() + 180
        last_shot = 0
        last_cookie_log = 0
        last_live = 0
        missing_qr = 0
        had_qr = bool(qr_url)
        verify_gone = 0
        sms_method_clicked = False
        sms_code_clicked = False
        sms_form_seen_at = 0.0
        last_sms_try = 0.0
        _capture_live_card(page)
        while time.time() < deadline and not _stop.is_set():
            while True:
                cmd = _pop_command()
                if not cmd:
                    break
                action = str(cmd.get("type") or "")
                if action.startswith("live_"):
                    _handle_live_command(page, cmd)
                    deadline = max(deadline, time.time() + 180)
                    last_live = time.time()
                elif action == "choose":
                    _click_verify_method(page, str(cmd.get("id") or ""), str(cmd.get("label") or ""))
                    time.sleep(1.6)
                    deadline = max(deadline, time.time() + 180)
                elif action == "code":
                    ok = _fill_verify_code(page, str(cmd.get("code") or ""), str(cmd.get("password") or ""))
                    if ok:
                        _set(message="已把验证码提交到抖音，正在确认…", verify_error="")
                    else:
                        _set(verify_error="没有把验证码填进抖音页面，请再点一次「验证」")
                    time.sleep(2.2)
                    deadline = max(deadline, time.time() + 120)
                elif action == "resend":
                    if not _click_exact_text(page, ["重新发送"]):
                        _click_get_sms_code(page)
                    _set(verify_resend_at=time.time() + 60, verify_error="")
                    time.sleep(0.8)
                elif action == "sent":
                    _click_exact_text(page, ["我已发送"])
                    time.sleep(1.2)
                elif action == "back":
                    _click_exact_text(page, ["选择其他验证方式"])
                    _set(verify_kind="", verify_need_code=False, verify_error="", verify_uplink_to="", verify_uplink_content="")
                    time.sleep(0.8)

            if sniff.get("token") and not token:
                token = str(sniff.get("token") or "")

            ui = _scan_verify_ui(page)
            in_verify = bool(ui.get("visible")) or str(sniff.get("account_flow") or "") == "verify"
            if in_verify:
                deadline = max(deadline, time.time() + 240)
                if _status() != "verify":
                    logger.info(
                        "已进入身份验证 step=%s methods=%s account=%s needGetCode=%s info=%s",
                        ui.get("step"),
                        ui.get("methods"),
                        ui.get("account"),
                        ui.get("needGetCode"),
                        (ui.get("info") or "")[:180],
                    )
                _publish_verify(ui, sniff)
                step = str(ui.get("step") or "")
                info = str(ui.get("info") or "")
                need_get = bool(ui.get("needGetCode"))
                now_sms = time.time()
                sms_sent = bool(sniff.get("sms_sent")) or "短信已发送" in info or bool(re.search(r"\d+\s*s后重新发送", info))
                still_choose = (not sms_sent) and step not in ("sms", "uplink") and (
                    step in ("", "choose")
                    or ("接收短信验证码" in info and "请输入验证码" not in info and "短信已发送" not in info)
                )
                if sms_sent and not sms_code_clicked:
                    sms_code_clicked = True
                    sms_method_clicked = True
                    logger.info("短信已发出，等待填写验证码")
                    _set(
                        verify_kind="sms",
                        verify_need_code=True,
                        verify_resend_at=time.time() + 60,
                        message="验证码已发送，请填写后点确定",
                    )
                if sms_code_clicked:
                    pass
                elif still_choose:
                    sms_method_clicked = False
                    if now_sms - last_sms_try >= 1.6:
                        last_sms_try = now_sms
                        logger.info("身份验证自动选择接收短信验证码 step=%s needGetCode=%s info=%s", step, need_get, info[:180])
                        if "人脸" in info and "接收短信验证码" not in info:
                            switched = _click_exact_text(page, ["选择其他验证方式", "其他验证方式", "更换验证方式"])
                            logger.info("已尝试离开人脸验证 switched=%s", bool(switched))
                            time.sleep(0.9)
                        if _click_verify_method(page, "mobile_sms_verify", "接收短信验证码"):
                            sms_method_clicked = True
                            time.sleep(1.1)
                elif need_get:
                    sms_method_clicked = True
                    sms_form_seen_at = 0.0
                    if now_sms - last_sms_try >= 1.2:
                        last_sms_try = now_sms
                        logger.info("短信页出现「获取验证码」，准备点击发码 info=%s", info[:160])
                        if _click_get_sms_code(page):
                            sms_code_clicked = True
                            _set(
                                verify_kind="sms",
                                verify_need_code=True,
                                verify_resend_at=time.time() + 60,
                                message="已点获取验证码，请查收短信后填写",
                            )
                elif step == "sms" or "请输入验证码" in info:
                    sms_method_clicked = True
                    if not sms_form_seen_at:
                        sms_form_seen_at = now_sms
                    elif now_sms - sms_form_seen_at >= 2.0:
                        sms_code_clicked = True
                        logger.info("短信输入页已无「获取验证码」，按已发出处理")
                        _set(
                            verify_kind="sms",
                            verify_need_code=True,
                            verify_resend_at=time.time() + 60,
                            message="请填写收到的验证码后点确定",
                        )
            elif _status() == "verify":
                _publish_verify(ui, sniff)
                in_verify = True

            sniff_status = str(sniff.get("qr_status") or "")
            if not in_verify:
                if sniff_status == "2" and _status() == "waiting":
                    _set(status="scanned", message=str(sniff.get("echo") or "已扫码，请在手机上确认登录"))
                elif sniff_status in ("3", "4") or sniff.get("redirect"):
                    redirect = str(sniff.pop("redirect", "") or "")
                    sniff["qr_status"] = ""
                    _set(status="scanned", message=str(sniff.get("echo") or "已确认，正在进入下一步…"))
                    _follow_login_redirect(page, context, redirect or None)

            if token and not in_verify and not sniff.get("qr_status") and str(sniff.get("account_flow") or "") != "verify":
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
            if in_verify and not ui.get("visible") and str(sniff.get("account_flow") or "") != "verify":
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
                fresh = _extract_qr_url(page)
                sniff_src = str(sniff.get("qr_url") or "")
                if (is_image_qr_src(sniff_src) or sniff_src.startswith("data:image")) and not fresh:
                    fresh = sniff_src
                if fresh:
                    had_qr = True
                    missing_qr = 0
                    patch = {"qr_url": fresh, "qr_base64": ""}
                    sniffed_jump = str(sniff.get("app_jump_url") or "")
                    if sniffed_jump:
                        with _lock:
                            current_jump = _state.get("app_jump_url") or ""
                        patch.update(_jump_fields(pick_best_jump(current_jump, sniffed_jump)))
                    _set(**patch)
                elif had_qr:
                    missing_qr += 1
                    logger.info("二维码地址已消失 %s 次", missing_qr)
                last_shot = now
            sniffed_jump = str(sniff.get("app_jump_url") or "")
            if sniffed_jump:
                with _lock:
                    current_jump = _state.get("app_jump_url") or ""
                chosen = pick_best_jump(current_jump, sniffed_jump)
                fields = _jump_fields(chosen)
                if fields.get("app_jump_url") and fields["app_jump_url"] != current_jump:
                    _set(**fields)
            if _status() != "verify" and now - last_live >= 0.7:
                _capture_live_card(page)
                last_live = now
            time.sleep(0.45)
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
            app_jump_url="",
            app_scheme="",
            app_scheme_ios="",
            app_open_url="",
            app_open_url_android="",
            live_html="",
            live_hash="",
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
    with _lock:
        _live_box.update({"x": 0, "y": 0, "w": 0, "h": 0})
    _set(
        status="loading",
        message="正在生成二维码…",
        qr_base64="",
        qr_url="",
        app_jump_url="",
        app_scheme="",
        app_scheme_ios="",
        app_open_url="",
        app_open_url_android="",
        username="",
        unique_id="",
        cookies=[],
        replace_index=replace_index,
        started_at=time.time(),
        verify_methods=[],
        verify_account="",
        verify_need_code=False,
        verify_need_password=False,
        verify_info="",
        verify_kind="",
        verify_uplink_from="",
        verify_uplink_to="",
        verify_uplink_content="",
        live_html="",
        live_hash="",
        live_w=0,
        live_h=0,
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
        app_jump_url="",
        app_scheme="",
        app_scheme_ios="",
        app_open_url="",
        app_open_url_android="",
        cookies=[],
        verify_methods=[],
        verify_account="",
        verify_need_code=False,
        verify_need_password=False,
        verify_info="",
        verify_kind="",
        verify_uplink_from="",
        verify_uplink_to="",
        verify_uplink_content="",
        live_html="",
        live_hash="",
        live_w=0,
        live_h=0,
    )
    return snapshot()


def live_page_action(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    kind = str(action or "").strip()
    if kind == "click":
        _push_command({"type": "live_click", "x": payload.get("x"), "y": payload.get("y")})
    elif kind == "type":
        text = str(payload.get("text") or "")[:120]
        if text:
            _push_command({"type": "live_type", "text": text})
    elif kind in {"key", "press"}:
        key = str(payload.get("key") or "")
        if key:
            _push_command({"type": "live_key", "key": key})
    elif kind == "fill":
        text = str(payload.get("text") or "")[:120]
        _push_command({"type": "live_fill", "text": text})
    else:
        return {"ok": False, "message": "未知操作"}
    return {"ok": True}


def choose_verify_method(method_id: str, label: str = "") -> dict[str, Any]:
    ident = str(method_id or "").strip()
    zh = _human_verify_label(ident, label)
    kind = _method_kind(ident + " " + zh)
    logger.info("用户选择身份验证方式 id=%s label=%s kind=%s", ident, zh, kind)
    extra = {
        "message": "身份验证",
        "verify_kind": kind,
        "status": "verify",
        "verify_uplink_from": _extract_mobile(zh) or _extract_mobile(ident),
    }
    if kind == "sms":
        extra["verify_need_code"] = True
        extra["message"] = "接收短信验证码"
        extra["verify_resend_at"] = time.time() + 60
    elif kind == "uplink":
        extra["verify_need_code"] = False
        extra["message"] = "发送短信验证"
    if ident or zh:
        _push_command({"type": "choose", "id": ident or zh, "label": zh})
        _set(**extra)
    return snapshot()


def submit_verify_code(code: str, password: str = "") -> dict[str, Any]:
    logger.info(
        "用户提交身份验证 code_len=%s has_password=%s",
        len(str(code or "").strip()),
        "yes" if str(password or "").strip() else "no",
    )
    _push_command({"type": "code", "code": str(code or "").strip(), "password": str(password or "")})
    _set(message="已提交验证码，正在确认…")
    return snapshot()


def verify_page_action(action: str) -> dict[str, Any]:
    action = str(action or "").strip()
    logger.info("身份验证页面动作 %s", action)
    if action == "back":
        _push_command({"type": "back"})
        _set(verify_kind="", verify_need_code=False, verify_error="", message="身份验证")
    elif action == "resend":
        _push_command({"type": "resend"})
        _set(message="正在重新发送验证码…", verify_resend_at=time.time() + 60, verify_error="")
    elif action == "sent":
        _push_command({"type": "sent"})
        _set(message="已点「我已发送」，正在确认…")
    return snapshot()
