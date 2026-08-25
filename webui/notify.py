import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from utils.logger import setup_logger
from webui.users import find_user, load_users, now_utc, save_users, to_iso

ROOT = Path(__file__).resolve().parent.parent
NOTIFY_FILE = ROOT / "config" / "notify.json"
logger = setup_logger("notify")

EVENT_KEYS = ("task_done", "task_fail", "cookie_offline")
PP_QR_TTL = 90
PP_API = "https://www.pushplus.plus/api"
PP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.pushplus.plus/login.html",
    "Origin": "https://www.pushplus.plus",
}

_token_cache = {"token": "", "expire": 0}
_bind_lock = threading.Lock()
_bind_sessions: dict[str, dict] = {}


def default_notify() -> dict:
    return {
        "wechat": {
            "enabled": False,
            "app_id": "",
            "app_secret": "",
            "token": "",
            "aes_key": "",
            "admin_openid": "",
            "tpl_task_done": "",
            "tpl_task_fail": "",
            "tpl_cookie_offline": "",
            "tpl_style": "thing",
        },
        "events": {
            "task_done": True,
            "task_fail": True,
            "cookie_offline": True,
        },
    }


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_notify() -> dict:
    NOTIFY_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = default_notify()
    if NOTIFY_FILE.is_file():
        try:
            raw = json.loads(NOTIFY_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = _deep_merge(data, raw)
        except Exception:
            logger.exception("读取推送配置失败")
    return data


def save_notify(payload: dict) -> dict:
    current = load_notify()
    wechat = dict(current.get("wechat") or {})
    incoming = payload.get("wechat") if isinstance(payload.get("wechat"), dict) else payload
    for key in (
        "enabled",
        "app_id",
        "app_secret",
        "token",
        "aes_key",
        "admin_openid",
        "tpl_task_done",
        "tpl_task_fail",
        "tpl_cookie_offline",
        "tpl_style",
    ):
        if key in incoming:
            wechat[key] = incoming[key] if key == "enabled" else str(incoming.get(key) or "").strip()
    if "enabled" in incoming:
        wechat["enabled"] = bool(incoming.get("enabled"))
    wechat["tpl_style"] = "keyword" if str(wechat.get("tpl_style") or "") == "keyword" else "thing"
    events = dict(current.get("events") or {})
    incoming_events = payload.get("events") if isinstance(payload.get("events"), dict) else {}
    for key in EVENT_KEYS:
        if key in incoming_events:
            events[key] = bool(incoming_events.get(key))
    data = {"wechat": wechat, "events": events}
    NOTIFY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def public_notify(data: dict | None = None) -> dict:
    data = data or load_notify()
    wechat = dict(data.get("wechat") or {})
    secret = str(wechat.get("app_secret") or "")
    wechat["app_secret_set"] = bool(secret)
    return {
        "wechat": wechat,
        "events": dict(data.get("events") or {}),
        "bound": bool(wechat.get("admin_openid")),
        "ready": bool(wechat.get("enabled") and wechat.get("app_id") and secret and wechat.get("admin_openid")),
    }


def mask_token(token: str) -> str:
    raw = str(token or "").strip()
    if len(raw) <= 4:
        return "已绑定" if raw else ""
    return "••••" + raw[-4:]


def public_pushplus(user: dict | None) -> dict:
    token = str((user or {}).get("pushplus_token") or "").strip()
    return {
        "bound": bool(token),
        "mask": mask_token(token) if token else "",
        "bound_at": str((user or {}).get("pushplus_bound_at") or ""),
    }


def user_pushplus_token(username: str) -> str:
    user = find_user(username)
    return str((user or {}).get("pushplus_token") or "").strip()


def set_user_pushplus(username: str, token: str) -> dict | None:
    name = str(username or "").strip()
    users = load_users()
    found = None
    for item in users:
        if item.get("username") != name:
            continue
        item["pushplus_token"] = str(token or "").strip()
        item["pushplus_bound_at"] = to_iso(now_utc()) if item["pushplus_token"] else ""
        found = item
        break
    if not found:
        return None
    save_users(users)
    return found


def migrate_legacy_pushplus() -> None:
    cfg = load_notify()
    legacy = str((cfg.get("pushplus") or {}).get("token") or "").strip()
    if not legacy:
        return
    users = load_users()
    changed = False
    for item in users:
        if item.get("role") != "admin":
            continue
        if str(item.get("pushplus_token") or "").strip():
            continue
        item["pushplus_token"] = legacy
        item["pushplus_bound_at"] = to_iso(now_utc())
        changed = True
        logger.info("已把旧的全局 PushPlus token 迁到管理员 %s", item.get("username"))
        break
    if changed:
        save_users(users)
    leftover = dict(cfg)
    leftover.pop("pushplus", None)
    leftover.pop("wxpusher", None)
    leftover.pop("serverchan", None)
    NOTIFY_FILE.write_text(json.dumps(leftover, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_wechat_signature(signature: str, timestamp: str, nonce: str) -> bool:
    token = str((load_notify().get("wechat") or {}).get("token") or "")
    if not token or not signature:
        return False
    parts = sorted([token, str(timestamp or ""), str(nonce or "")])
    digest = hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()
    return digest == str(signature).lower()


def _access_token(app_id: str, app_secret: str) -> str:
    now = time.time()
    if _token_cache["token"] and _token_cache["expire"] > now + 60:
        return _token_cache["token"]
    url = "https://api.weixin.qq.com/cgi-bin/token"
    with httpx.Client(timeout=8.0) as client:
        resp = client.get(url, params={"grant_type": "client_credential", "appid": app_id, "secret": app_secret})
        data = resp.json()
    token = str(data.get("access_token") or "")
    if not token:
        raise RuntimeError(data.get("errmsg") or "获取微信 access_token 失败")
    _token_cache["token"] = token
    _token_cache["expire"] = now + int(data.get("expires_in") or 7200)
    return token


def _template_id(wechat: dict, kind: str) -> str:
    mapping = {
        "task_done": wechat.get("tpl_task_done"),
        "task_fail": wechat.get("tpl_task_fail"),
        "cookie_offline": wechat.get("tpl_cookie_offline"),
        "test": wechat.get("tpl_task_done") or wechat.get("tpl_task_fail") or wechat.get("tpl_cookie_offline"),
    }
    return str(mapping.get(kind) or "").strip()


def _template_data(title: str, body: str, style: str = "thing") -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = (title or "SparkFlow")[:20]
    body = (body or "-")[:20]
    if style == "keyword":
        return {
            "first": {"value": title},
            "keyword1": {"value": body},
            "keyword2": {"value": now},
            "remark": {"value": "来自 SparkFlow"},
        }
    return {
        "thing1": {"value": title},
        "time2": {"value": now},
        "thing3": {"value": body},
    }


def send_wechat(kind: str, title: str, body: str = "") -> dict:
    cfg = load_notify()
    wechat = cfg.get("wechat") or {}
    if not wechat.get("enabled"):
        raise RuntimeError("还没打开公众号推送")
    app_id = str(wechat.get("app_id") or "").strip()
    app_secret = str(wechat.get("app_secret") or "").strip()
    openid = str(wechat.get("admin_openid") or "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("请先填写公众号 AppID 和 AppSecret")
    if not openid:
        raise RuntimeError("请先填写管理员 OpenID")
    tpl = _template_id(wechat, kind)
    if not tpl:
        raise RuntimeError("请先填写对应事件的模板消息 ID")
    token = _access_token(app_id, app_secret)
    payload = {
        "touser": openid,
        "template_id": tpl,
        "data": _template_data(title, body, str(wechat.get("tpl_style") or "thing")),
    }
    url = "https://api.weixin.qq.com/cgi-bin/message/template/send"
    with httpx.Client(timeout=8.0) as client:
        resp = client.post(url, params={"access_token": token}, json=payload)
        data = resp.json()
    if int(data.get("errcode") or 0) != 0:
        raise RuntimeError(data.get("errmsg") or "微信推送失败")
    logger.info("公众号推送成功 kind=%s msgid=%s", kind, data.get("msgid"))
    return data


def send_pushplus(title: str, body: str = "", token: str = "") -> dict:
    token = str(token or "").strip()
    if not token:
        raise RuntimeError("还没绑定 PushPlus")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "token": token,
        "title": title or "SparkFlow",
        "content": f"{body or '-'}\n{now}",
        "template": "txt",
    }
    with httpx.Client(timeout=8.0) as client:
        resp = client.post("https://www.pushplus.plus/send", json=payload)
        data = resp.json()
    if int(data.get("code") or 0) != 200:
        raise RuntimeError(data.get("msg") or "PushPlus 发送失败")
    logger.info("PushPlus 推送成功 title=%s", title)
    return data


def notify_event(kind: str, title: str, body: str = "", usernames=None) -> None:
    cfg = load_notify()
    events = cfg.get("events") or {}
    if kind != "test" and not events.get(kind, True):
        return
    names = []
    seen = set()
    for name in usernames or []:
        item = str(name or "").strip()
        if item and item not in seen:
            seen.add(item)
            names.append(item)
    wechat_on = bool((cfg.get("wechat") or {}).get("enabled"))
    targets = []
    for name in names:
        token = user_pushplus_token(name)
        if token:
            targets.append((name, token))
    if not wechat_on and not targets:
        return

    def _run():
        for name, token in targets:
            try:
                send_pushplus(title, body, token=token)
            except Exception as exc:
                logger.warning("PushPlus 未发出 user=%s kind=%s: %s", name, kind, exc)
        if wechat_on:
            try:
                send_wechat(kind, title, body)
            except Exception as exc:
                logger.warning("公众号未发出 kind=%s: %s", kind, exc)

    threading.Thread(target=_run, daemon=True).start()


def _close_session(session: dict | None) -> None:
    client = (session or {}).get("client")
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass


def _session_alive(session: dict | None) -> bool:
    if not session:
        return False
    return float(session.get("expire") or 0) > time.time()


def _extract_token(payload) -> str:
    if isinstance(payload, str):
        text = payload.strip()
        if text and text not in ("尚未登录", "二维码过期", "已过期"):
            return text
        return ""
    if isinstance(payload, dict):
        for key in ("token", "pushToken", "userToken"):
            text = str(payload.get(key) or "").strip()
            if text:
                return text
    return ""


def start_pushplus_qr(username: str, force: bool = False) -> dict:
    name = str(username or "").strip()
    if not name:
        raise RuntimeError("未登录")
    with _bind_lock:
        session = _bind_sessions.get(name)
        if not force and _session_alive(session) and (session.get("qr_bytes") or session.get("qr_url")):
            return {
                "ok": True,
                "waiting": True,
                "bound": False,
                "expire_in": max(0, int(session["expire"] - time.time())),
                "qr_url": session.get("qr_url") or "",
                "has_image": bool(session.get("qr_bytes")),
            }
        _close_session(session)
        client = httpx.Client(timeout=10.0, headers=PP_HEADERS, follow_redirects=True)
        try:
            resp = client.get(f"{PP_API}/common/wechat/getQrcode")
            data = resp.json()
        except Exception as exc:
            client.close()
            raise RuntimeError("获取 PushPlus 二维码失败") from exc
        if int(data.get("code") or 0) != 200:
            client.close()
            raise RuntimeError(data.get("msg") or "获取 PushPlus 二维码失败")
        info = data.get("data") if isinstance(data.get("data"), dict) else {}
        key = str(info.get("qrCode") or "").strip()
        qr_url = str(info.get("qrCodeUrl") or "").strip()
        if not key or not qr_url:
            client.close()
            raise RuntimeError("PushPlus 没有返回二维码")
        qr_bytes = b""
        try:
            img = client.get(qr_url, headers={"Referer": "https://mp.weixin.qq.com/"})
            if img.status_code == 200 and img.content:
                qr_bytes = img.content
        except Exception:
            logger.warning("拉取 PushPlus 二维码图片失败 user=%s", name)
        now = time.time()
        _bind_sessions[name] = {
            "client": client,
            "key": key,
            "qr_url": qr_url,
            "qr_bytes": qr_bytes,
            "created": now,
            "expire": now + PP_QR_TTL,
        }
        logger.info("已生成 PushPlus 绑定二维码 user=%s ttl=%s", name, PP_QR_TTL)
        return {
            "ok": True,
            "waiting": True,
            "bound": False,
            "expire_in": PP_QR_TTL,
            "qr_url": qr_url,
            "has_image": bool(qr_bytes),
        }


def pushplus_qr_image(username: str) -> bytes:
    name = str(username or "").strip()
    with _bind_lock:
        session = _bind_sessions.get(name)
        if not _session_alive(session):
            return b""
        return session.get("qr_bytes") or b""


def poll_pushplus_qr(username: str) -> dict:
    name = str(username or "").strip()
    user = find_user(name)
    bound = public_pushplus(user)
    with _bind_lock:
        session = _bind_sessions.get(name)
        if bound.get("bound") and not session:
            return {"ok": True, "waiting": False, **bound}
        if not _session_alive(session):
            if session:
                _close_session(_bind_sessions.pop(name, None))
            payload = {"ok": True, "waiting": False, "expired": True, **bound}
            return payload
        client = session["client"]
        key = session["key"]
        try:
            resp = client.get(f"{PP_API}/common/wechat/confirmLogin", params={"key": key, "code": 1001})
            data = resp.json()
        except Exception as exc:
            logger.warning("查询 PushPlus 扫码状态失败 user=%s: %s", name, exc)
            return {"ok": True, "waiting": True, "expired": False, **bound}
        code = int(data.get("code") or 0)
        raw = data.get("data")
        msg = str(data.get("msg") or "")
        token = _extract_token(raw)
        waiting_hints = ("尚未登录", "未登录", "等待", "未扫码")
        if not token and (code != 200 or any(item in str(raw) for item in waiting_hints) or any(item in msg for item in waiting_hints)):
            left = max(0, int(float(session.get("expire") or 0) - time.time()))
            return {"ok": True, "waiting": True, "expired": left <= 0, "expire_in": left, **bound}
        if not token:
            return {"ok": True, "waiting": True, "expired": False, "message": msg or "还在等待扫码", **bound}
    saved = set_user_pushplus(name, token)
    with _bind_lock:
        _close_session(_bind_sessions.pop(name, None))
    logger.info("PushPlus 扫码绑定成功 user=%s", name)
    return {"ok": True, "waiting": False, "bound": True, **public_pushplus(saved)}


def cancel_pushplus_qr(username: str) -> None:
    name = str(username or "").strip()
    with _bind_lock:
        _close_session(_bind_sessions.pop(name, None))
