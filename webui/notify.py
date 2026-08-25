import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from utils.logger import setup_logger

ROOT = Path(__file__).resolve().parent.parent
NOTIFY_FILE = ROOT / "config" / "notify.json"
logger = setup_logger("notify")

EVENT_KEYS = ("task_done", "task_fail", "cookie_offline")
_token_cache = {"token": "", "expire": 0}


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
        "pushplus": {
            "enabled": False,
            "token": "",
        },
        "serverchan": {
            "enabled": False,
            "sendkey": "",
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
    wxpusher = dict(current.get("wxpusher") or {})
    incoming_wp = payload.get("wxpusher") if isinstance(payload.get("wxpusher"), dict) else {}
    if "enabled" in incoming_wp:
        wxpusher["enabled"] = bool(incoming_wp.get("enabled"))
    if "app_token" in incoming_wp:
        wxpusher["app_token"] = str(incoming_wp.get("app_token") or "").strip()
    if "uids" in incoming_wp:
        wxpusher["uids"] = str(incoming_wp.get("uids") or "").strip()
    pushplus = dict(current.get("pushplus") or {})
    incoming_pp = payload.get("pushplus") if isinstance(payload.get("pushplus"), dict) else {}
    if "enabled" in incoming_pp:
        pushplus["enabled"] = bool(incoming_pp.get("enabled"))
    if "token" in incoming_pp:
        pushplus["token"] = str(incoming_pp.get("token") or "").strip()
    serverchan = dict(current.get("serverchan") or {})
    incoming_sc = payload.get("serverchan") if isinstance(payload.get("serverchan"), dict) else {}
    if "enabled" in incoming_sc:
        serverchan["enabled"] = bool(incoming_sc.get("enabled"))
    if "sendkey" in incoming_sc:
        serverchan["sendkey"] = str(incoming_sc.get("sendkey") or "").strip()
    events = dict(current.get("events") or {})
    incoming_events = payload.get("events") if isinstance(payload.get("events"), dict) else {}
    for key in EVENT_KEYS:
        if key in incoming_events:
            events[key] = bool(incoming_events.get(key))
    data = {"wechat": wechat, "wxpusher": wxpusher, "pushplus": pushplus, "serverchan": serverchan, "events": events}
    NOTIFY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def public_notify(data: dict | None = None) -> dict:
    data = data or load_notify()
    wechat = dict(data.get("wechat") or {})
    secret = str(wechat.get("app_secret") or "")
    wechat["app_secret_set"] = bool(secret)
    wxpusher = dict(data.get("wxpusher") or {})
    wp_token = str(wxpusher.get("app_token") or "").strip()
    wp_uids = str(wxpusher.get("uids") or "").strip()
    pushplus = dict(data.get("pushplus") or {})
    serverchan = dict(data.get("serverchan") or {})
    return {
        "wechat": wechat,
        "wxpusher": wxpusher,
        "pushplus": pushplus,
        "serverchan": serverchan,
        "events": dict(data.get("events") or {}),
        "bound": bool(wechat.get("admin_openid")),
        "ready": bool(wechat.get("enabled") and wechat.get("app_id") and secret and wechat.get("admin_openid")),
        "wxpusher_ready": bool(wxpusher.get("enabled") and wp_token and wp_uids),
        "pushplus_ready": bool(pushplus.get("enabled") and str(pushplus.get("token") or "").strip()),
        "serverchan_ready": bool(serverchan.get("enabled") and str(serverchan.get("sendkey") or "").strip()),
    }


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
        raise RuntimeError("请先填写管理员 OpenID，后续会改成扫码绑定")
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


def _parse_uids(raw) -> list[str]:
    if isinstance(raw, list):
        parts = raw
    else:
        text = str(raw or "").replace("，", ",").replace(";", ",").replace("\n", ",")
        parts = text.split(",")
    return [str(item).strip() for item in parts if str(item).strip()]


def send_wxpusher(title: str, body: str = "") -> dict:
    cfg = load_notify().get("wxpusher") or {}
    if not cfg.get("enabled"):
        raise RuntimeError("还没打开 WxPusher")
    token = str(cfg.get("app_token") or "").strip()
    uids = _parse_uids(cfg.get("uids"))
    if not token:
        raise RuntimeError("请填写 WxPusher appToken")
    if not uids:
        raise RuntimeError("请填写 WxPusher UID")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "appToken": token,
        "content": f"{title}\n{body or '-'}\n{now}",
        "summary": (title or "SparkFlow")[:20],
        "contentType": 1,
        "uids": uids,
    }
    with httpx.Client(timeout=8.0) as client:
        resp = client.post("https://wxpusher.zjiecode.com/api/send/message", json=payload)
        data = resp.json()
    if int(data.get("code") or 0) != 1000:
        raise RuntimeError(data.get("msg") or "WxPusher 发送失败")
    logger.info("WxPusher 推送成功 title=%s uids=%s", title, len(uids))
    return data


def send_pushplus(title: str, body: str = "") -> dict:
    cfg = load_notify().get("pushplus") or {}
    if not cfg.get("enabled"):
        raise RuntimeError("还没打开 PushPlus")
    token = str(cfg.get("token") or "").strip()
    if not token:
        raise RuntimeError("请填写 PushPlus token")
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


def _serverchan_sendkey(raw: str) -> str:
    key = str(raw or "").strip()
    for prefix in ("https://sctapi.ftqq.com/", "http://sctapi.ftqq.com/", "https://sc.ftqq.com/", "http://sc.ftqq.com/"):
        if prefix in key:
            key = key.split(prefix, 1)[1]
    return key.replace(".send", "").strip("/")


def send_serverchan(title: str, body: str = "") -> dict:
    cfg = load_notify().get("serverchan") or {}
    if not cfg.get("enabled"):
        raise RuntimeError("还没打开 Server酱")
    sendkey = _serverchan_sendkey(cfg.get("sendkey"))
    if not sendkey:
        raise RuntimeError("请填写 Server酱 SendKey")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {"title": title or "SparkFlow", "desp": f"{body or '-'}\n\n{now}"}
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    if not sendkey.upper().startswith("SCT"):
        url = f"https://sc.ftqq.com/{sendkey}.send"
    with httpx.Client(timeout=8.0) as client:
        resp = client.post(url, data=payload)
        data = resp.json()
    if int(data.get("code") or 0) != 0:
        raise RuntimeError(data.get("message") or data.get("msg") or "Server酱 发送失败")
    logger.info("Server酱 推送成功 title=%s", title)
    return data


def notify_event(kind: str, title: str, body: str = "") -> None:
    cfg = load_notify()
    events = cfg.get("events") or {}
    if kind != "test" and not events.get(kind, True):
        return
    channels = [
        ("wxpusher", (cfg.get("wxpusher") or {}).get("enabled"), lambda: send_wxpusher(title, body)),
        ("pushplus", (cfg.get("pushplus") or {}).get("enabled"), lambda: send_pushplus(title, body)),
        ("serverchan", (cfg.get("serverchan") or {}).get("enabled"), lambda: send_serverchan(title, body)),
        ("wechat", (cfg.get("wechat") or {}).get("enabled"), lambda: send_wechat(kind, title, body)),
    ]
    if not any(item[1] for item in channels):
        return

    def _run():
        for name, enabled, sender in channels:
            if not enabled:
                continue
            try:
                sender()
            except Exception as exc:
                logger.warning("%s 未发出 kind=%s: %s", name, kind, exc)

    threading.Thread(target=_run, daemon=True).start()
