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
WP_QR_TTL = 1800
WP_POLL_MIN = 10
WP_CREATE_QR = "https://wxpusher.zjiecode.com/api/fun/create/qrcode"
WP_SCAN_UID = "https://wxpusher.zjiecode.com/api/fun/scan-qrcode-uid"
WP_SEND = "https://wxpusher.zjiecode.com/api/send/message"

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
        "wxpusher": {
            "enabled": False,
            "app_token": "",
            "uids": "",
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
    wxpusher = dict(current.get("wxpusher") or {})
    incoming_wp = payload.get("wxpusher") if isinstance(payload.get("wxpusher"), dict) else {}
    if "enabled" in incoming_wp:
        wxpusher["enabled"] = bool(incoming_wp.get("enabled"))
    if "app_token" in incoming_wp:
        wxpusher["app_token"] = str(incoming_wp.get("app_token") or "").strip()
    if "uids" in incoming_wp:
        wxpusher["uids"] = str(incoming_wp.get("uids") or "").strip()
    data = {"wechat": wechat, "wxpusher": wxpusher, "events": events}
    NOTIFY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _parse_uids(raw) -> list[str]:
    if isinstance(raw, list):
        parts = raw
    else:
        text = str(raw or "").replace("，", ",").replace(";", ",").replace("\n", ",")
        parts = text.split(",")
    return [str(item).strip() for item in parts if str(item).strip()]


def public_notify(data: dict | None = None) -> dict:
    data = data or load_notify()
    wechat = dict(data.get("wechat") or {})
    secret = str(wechat.get("app_secret") or "")
    wechat["app_secret_set"] = bool(secret)
    wxpusher = dict(data.get("wxpusher") or {})
    wp_token = str(wxpusher.get("app_token") or "").strip()
    return {
        "wechat": wechat,
        "wxpusher": wxpusher,
        "events": dict(data.get("events") or {}),
        "bound": bool(wechat.get("admin_openid")),
        "ready": bool(wechat.get("enabled") and wechat.get("app_id") and secret and wechat.get("admin_openid")),
        "wxpusher_ready": bool(wxpusher.get("enabled") and wp_token),
    }


def mask_uid(uid: str) -> str:
    raw = str(uid or "").strip()
    if not raw:
        return ""
    if len(raw) <= 4:
        return "已绑定"
    return "••••" + raw[-4:]


def public_wxpusher(user: dict | None) -> dict:
    uid = str((user or {}).get("wxpusher_uid") or "").strip()
    return {
        "bound": bool(uid),
        "mask": mask_uid(uid) if uid else "",
        "bound_at": str((user or {}).get("wxpusher_bound_at") or ""),
    }


def user_wxpusher_uid(username: str) -> str:
    user = find_user(username)
    return str((user or {}).get("wxpusher_uid") or "").strip()


def set_user_wxpusher(username: str, uid: str) -> dict | None:
    name = str(username or "").strip()
    if not name:
        return None
    users = load_users()
    found = None
    for item in users:
        if item.get("username") != name:
            continue
        item["wxpusher_uid"] = str(uid or "").strip()
        item["wxpusher_bound_at"] = to_iso(now_utc()) if item["wxpusher_uid"] else ""
        found = item
        break
    if not found:
        return None
    save_users(users)
    return found


def _close_session(session: dict | None) -> None:
    if not session:
        return
    client = session.get("client")
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


def _app_token() -> str:
    cfg = load_notify().get("wxpusher") or {}
    if not cfg.get("enabled"):
        raise RuntimeError("还没打开 WxPusher，请管理员先在设置里启用并填写 appToken")
    token = str(cfg.get("app_token") or "").strip()
    if not token:
        raise RuntimeError("请管理员先在设置里填写 WxPusher appToken")
    return token


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


def send_wxpusher(title: str, body: str = "", uids=None) -> dict:
    token = _app_token()
    targets = _parse_uids(uids)
    if not targets:
        raise RuntimeError("还没扫码绑定微信")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "appToken": token,
        "content": f"{title}\n{body or '-'}\n{now}",
        "summary": (title or "SparkFlow")[:20],
        "contentType": 1,
        "uids": targets,
    }
    with httpx.Client(timeout=8.0) as client:
        resp = client.post(WP_SEND, json=payload)
        data = resp.json()
    if int(data.get("code") or 0) != 1000:
        raise RuntimeError(data.get("msg") or "WxPusher 发送失败")
    logger.info("WxPusher 推送成功 title=%s uids=%s", title, len(targets))
    return data


def notify_event(kind: str, title: str, body: str = "", usernames=None) -> None:
    cfg = load_notify()
    events = cfg.get("events") or {}
    if kind != "test" and not events.get(kind, True):
        return
    wechat = cfg.get("wechat") or {}
    wxpusher = cfg.get("wxpusher") or {}
    names = []
    seen = set()
    for name in usernames or []:
        item = str(name or "").strip()
        if item and item not in seen:
            seen.add(item)
            names.append(item)
    uids = []
    for name in names:
        uid = user_wxpusher_uid(name)
        if uid and uid not in uids:
            uids.append(uid)
    if not wechat.get("enabled") and not (wxpusher.get("enabled") and uids):
        if wxpusher.get("enabled") and names and not uids:
            logger.warning("WxPusher 跳过 kind=%s owners=%s 未绑定微信", kind, ",".join(names))
        return
    owners = ",".join(names) or "-"

    def _run():
        if wxpusher.get("enabled") and uids:
            try:
                send_wxpusher(title, body, uids=uids)
            except Exception as exc:
                logger.warning("WxPusher 未发出 kind=%s owners=%s: %s", kind, owners, exc)
        if wechat.get("enabled"):
            try:
                send_wechat(kind, title, body)
            except Exception as exc:
                logger.warning("公众号未发出 kind=%s: %s", kind, exc)

    threading.Thread(target=_run, daemon=True).start()


def start_wxpusher_qr(username: str, force: bool = False) -> dict:
    name = str(username or "").strip()
    if not name:
        raise RuntimeError("未登录")
    token = _app_token()
    bound = public_wxpusher(find_user(name))
    extra = name[:64]
    with _bind_lock:
        session = _bind_sessions.get(name)
        if not force and _session_alive(session) and (session.get("qr_bytes") or session.get("qr_url")):
            return {
                "ok": True,
                "waiting": True,
                "expire_in": max(0, int(session["expire"] - time.time())),
                "qr_url": session.get("qr_url") or "",
                "has_image": bool(session.get("qr_bytes")),
                **bound,
            }
        _close_session(session)
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.post(
                    WP_CREATE_QR,
                    json={"appToken": token, "extra": extra, "validTime": WP_QR_TTL},
                )
                data = resp.json()
                if int(data.get("code") or 0) != 1000:
                    raise RuntimeError(data.get("msg") or "生成 WxPusher 二维码失败")
                info = data.get("data") if isinstance(data.get("data"), dict) else {}
                code = str(info.get("code") or "").strip()
                qr_url = str(info.get("url") or info.get("shortUrl") or "").strip()
                if not code or not qr_url:
                    raise RuntimeError("WxPusher 没有返回二维码")
                qr_bytes = b""
                try:
                    img = client.get(qr_url)
                    if img.status_code == 200 and img.content:
                        qr_bytes = img.content
                except Exception:
                    logger.warning("拉取 WxPusher 二维码图片失败 user=%s", name)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("生成 WxPusher 二维码失败") from exc
        now = time.time()
        _bind_sessions[name] = {
            "code": code,
            "extra": extra,
            "qr_url": qr_url,
            "qr_bytes": qr_bytes,
            "created": now,
            "expire": now + WP_QR_TTL,
            "last_poll": 0,
        }
        logger.info("已生成 WxPusher 绑定二维码 user=%s ttl=%s", name, WP_QR_TTL)
        return {
            "ok": True,
            "waiting": True,
            "expire_in": WP_QR_TTL,
            "qr_url": qr_url,
            "has_image": bool(qr_bytes),
            **bound,
        }


def wxpusher_qr_image(username: str) -> bytes:
    name = str(username or "").strip()
    with _bind_lock:
        session = _bind_sessions.get(name)
        if not _session_alive(session):
            return b""
        return session.get("qr_bytes") or b""


def poll_wxpusher_qr(username: str) -> dict:
    name = str(username or "").strip()
    bound = public_wxpusher(find_user(name))
    with _bind_lock:
        session = _bind_sessions.get(name)
        if not _session_alive(session):
            if session:
                _close_session(_bind_sessions.pop(name, None))
            if bound.get("bound"):
                return {"ok": True, "waiting": False, "expired": False, **bound}
            return {"ok": True, "waiting": False, "expired": True, **bound}
        now = time.time()
        last = float(session.get("last_poll") or 0)
        if last and now - last < WP_POLL_MIN:
            left = max(0, int(float(session.get("expire") or 0) - now))
            return {"ok": True, "waiting": True, "expired": False, "expire_in": left, **bound}
        session["last_poll"] = now
        code = session.get("code") or ""
        extra = session.get("extra") or name
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(WP_SCAN_UID, params={"code": code})
                data = resp.json()
        except Exception as exc:
            logger.warning("查询 WxPusher 扫码状态失败 user=%s: %s", name, exc)
            left = max(0, int(float(session.get("expire") or 0) - now))
            return {"ok": True, "waiting": True, "expired": False, "expire_in": left, **bound}
        info = data.get("data") if isinstance(data.get("data"), dict) else {}
        uid = str((info or {}).get("uid") or "").strip()
        if not uid and isinstance(data.get("data"), str):
            uid = str(data.get("data") or "").strip()
        got_extra = str((info or {}).get("extra") or "").strip()
        if uid and got_extra and got_extra != extra:
            left = max(0, int(float(session.get("expire") or 0) - now))
            return {"ok": True, "waiting": True, "expired": False, "expire_in": left, **bound}
        if not uid or int(data.get("code") or 0) != 1000:
            left = max(0, int(float(session.get("expire") or 0) - now))
            return {"ok": True, "waiting": True, "expired": False, "expire_in": left, **bound}
        _close_session(_bind_sessions.pop(name, None))
    saved = set_user_wxpusher(name, uid)
    logger.info("WxPusher 扫码绑定成功 user=%s", name)
    return {"ok": True, "waiting": False, "bound": True, **public_wxpusher(saved)}


def cancel_wxpusher_qr(username: str) -> None:
    name = str(username or "").strip()
    with _bind_lock:
        _close_session(_bind_sessions.pop(name, None))


def apply_wxpusher_callback(payload: dict | None) -> dict:
    data = payload or {}
    info = data.get("data") if isinstance(data.get("data"), dict) else data
    uid = str((info or {}).get("uid") or "").strip()
    extra = str((info or {}).get("extra") or "").strip()
    if not uid or not extra:
        return {"ok": True, "bound": False}
    saved = set_user_wxpusher(extra, uid)
    if not saved:
        logger.warning("WxPusher 回调未匹配到用户 extra=%s", extra)
        return {"ok": True, "bound": False}
    with _bind_lock:
        _close_session(_bind_sessions.pop(extra, None))
    logger.info("WxPusher 回调绑定成功 user=%s", extra)
    return {"ok": True, "bound": True, **public_wxpusher(saved)}
