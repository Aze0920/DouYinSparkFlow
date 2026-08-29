import hashlib
import hmac
import html
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from utils.logger import setup_logger
from webui import safe_io
from webui.users import (
    find_user,
    is_permanent,
    load_users,
    now_utc,
    parse_days,
    parse_iso,
    parse_max_accounts,
    remaining_label,
    save_users,
    to_iso,
)

ROOT = Path(__file__).resolve().parent.parent
NOTIFY_FILE = ROOT / "config" / "notify.json"
logger = setup_logger("notify")

EVENT_KEYS = (
    "task_done",
    "task_fail",
    "cookie_offline",
    "expire_soon",
    "recharge",
    "invite_reward",
)
USER_EVENT_KEYS = ("expire_soon", "recharge", "invite_reward")
WP_UID_BATCH = 100
WP_QR_TTL = 1800
WP_POLL_MIN = 10
WP_CREATE_QR = "https://wxpusher.zjiecode.com/api/fun/create/qrcode"
WP_SCAN_UID = "https://wxpusher.zjiecode.com/api/fun/scan-qrcode-uid"
WP_SEND = "https://wxpusher.zjiecode.com/api/send/message"
WP_USER_LIST = "https://wxpusher.zjiecode.com/api/fun/wxuser/v2"
WP_REMOVE = "https://wxpusher.zjiecode.com/api/fun/remove"
NOTIFYX_SEND = "https://www.notifyx.cn/api/v1/send/{key}"

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
            "expire_soon": True,
            "recharge": True,
            "invite_reward": True,
        },
        "wxpusher": {
            "enabled": False,
            "app_token": "",
            "uids": "",
            "allow_self_unbind": True,
        },
        "notifyx": {
            "enabled": False,
            "api_key": "",
            "team": "",
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
    events = dict(data.get("events") or {})
    for key in EVENT_KEYS:
        events.setdefault(key, True)
    data["events"] = events
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
    if "allow_self_unbind" in incoming_wp:
        wxpusher["allow_self_unbind"] = bool(incoming_wp.get("allow_self_unbind"))
    if "allow_self_unbind" not in wxpusher:
        wxpusher["allow_self_unbind"] = True
    notifyx = dict(current.get("notifyx") or {})
    incoming_nx = payload.get("notifyx") if isinstance(payload.get("notifyx"), dict) else {}
    if "enabled" in incoming_nx:
        notifyx["enabled"] = bool(incoming_nx.get("enabled"))
    if "api_key" in incoming_nx:
        notifyx["api_key"] = str(incoming_nx.get("api_key") or "").strip()
    if "team" in incoming_nx:
        notifyx["team"] = str(incoming_nx.get("team") or "").strip()
    data = {"wechat": wechat, "wxpusher": wxpusher, "notifyx": notifyx, "events": events}
    safe_io.write_json(NOTIFY_FILE, data)
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
    if "allow_self_unbind" not in wxpusher:
        wxpusher["allow_self_unbind"] = True
    else:
        wxpusher["allow_self_unbind"] = bool(wxpusher.get("allow_self_unbind"))
    notifyx = dict(data.get("notifyx") or {})
    nx_key = str(notifyx.get("api_key") or "").strip()
    return {
        "wechat": wechat,
        "wxpusher": wxpusher,
        "notifyx": notifyx,
        "events": dict(data.get("events") or {}),
        "bound": bool(wechat.get("admin_openid")),
        "ready": bool(wechat.get("enabled") and wechat.get("app_id") and secret and wechat.get("admin_openid")),
        "wxpusher_ready": bool(wxpusher.get("enabled") and wp_token),
        "notifyx_ready": bool(notifyx.get("enabled") and nx_key),
    }


def mask_uid(uid: str) -> str:
    raw = str(uid or "").strip()
    if not raw:
        return ""
    if len(raw) <= 4:
        return "已绑定"
    return "••••" + raw[-4:]


def allow_self_unbind() -> bool:
    raw = (load_notify().get("wxpusher") or {}).get("allow_self_unbind")
    if raw is None:
        return True
    return bool(raw)


def find_wxpusher_owner(uid: str, except_username: str = "") -> dict | None:
    key = str(uid or "").strip()
    if not key:
        return None
    skip = str(except_username or "").strip()
    for item in load_users():
        if str(item.get("wxpusher_uid") or "").strip() != key:
            continue
        if skip and str(item.get("username") or "").strip() == skip:
            continue
        return item
    return None


def public_wxpusher(user: dict | None) -> dict:
    uid = str((user or {}).get("wxpusher_uid") or "").strip()
    return {
        "bound": bool(uid),
        "mask": mask_uid(uid) if uid else "",
        "bound_at": str((user or {}).get("wxpusher_bound_at") or ""),
        "allow_self_unbind": allow_self_unbind(),
    }


def user_wxpusher_uid(username: str) -> str:
    user = find_user(username)
    return str((user or {}).get("wxpusher_uid") or "").strip()


def set_user_wxpusher(username: str, uid: str) -> dict | None:
    name = str(username or "").strip()
    uid = str(uid or "").strip()
    if not name:
        return None
    if uid:
        owner = find_wxpusher_owner(uid, except_username=name)
        if owner:
            raise ValueError("该微信已绑定过")
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


def bind_user_wxpusher(username: str, uid: str) -> dict:
    name = str(username or "").strip()
    uid = str(uid or "").strip()
    if not uid:
        raise ValueError("未获取到微信")
    saved = set_user_wxpusher(name, uid)
    if not saved:
        raise ValueError("用户不存在")
    try:
        from webui.invite import complete_invite_on_bind

        complete_invite_on_bind(name)
        saved = find_user(name) or saved
    except Exception:
        logger.exception("发放邀请奖励失败 user=%s", name)
    return saved


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


def _wxpusher_token() -> str:
    token = str((load_notify().get("wxpusher") or {}).get("app_token") or "").strip()
    if not token:
        raise RuntimeError("请管理员先在设置里填写 WxPusher appToken")
    return token


def remove_wxpusher_uid(uid: str) -> int:
    uid = str(uid or "").strip()
    if not uid:
        return 0
    token = _wxpusher_token()
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(
            WP_USER_LIST,
            params={"appToken": token, "page": 1, "pageSize": 100, "uid": uid},
        )
        data = resp.json()
        if int(data.get("code") or 0) != 1000:
            raise RuntimeError(data.get("msg") or "查询 WxPusher 用户失败")
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        records = payload.get("records") or []
        ids = []
        for item in records:
            if not isinstance(item, dict):
                continue
            if str(item.get("uid") or "").strip() != uid:
                continue
            rec_id = item.get("id")
            if rec_id in (None, ""):
                continue
            ids.append(rec_id)
        if not ids:
            logger.info("WxPusher 后台没有可删除的关注 uid=%s", uid)
            return 0
        removed = 0
        for rec_id in ids:
            resp = client.delete(WP_REMOVE, params={"appToken": token, "id": rec_id})
            result = resp.json()
            if int(result.get("code") or 0) != 1000:
                raise RuntimeError(result.get("msg") or "WxPusher 删除用户失败")
            removed += 1
            logger.info("已从 WxPusher 删除关注 id=%s uid=%s", rec_id, uid)
    return removed


def unbind_user_wxpusher(username: str) -> dict | None:
    name = str(username or "").strip()
    uid = user_wxpusher_uid(name)
    if uid:
        remove_wxpusher_uid(uid)
    cancel_wxpusher_qr(name)
    return set_user_wxpusher(name, "")


def verify_wechat_signature(signature: str, timestamp: str, nonce: str) -> bool:
    token = str((load_notify().get("wechat") or {}).get("token") or "")
    if not token or not signature:
        return False
    parts = sorted([token, str(timestamp or ""), str(nonce or "")])
    digest = hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()
    got = str(signature).lower().strip()
    if len(digest) != len(got):
        return False
    return hmac.compare_digest(digest, got)


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


def _notifyx_markdown(title: str = "", body: str = "", rows=None, footer: str = "") -> str:
    lines = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        lines.append(f"**{label}**：{value}" if label else value)
    if not lines:
        text = str(body or "").strip()
        if text:
            lines.append(text)
    note = str(footer or "").strip()
    if note:
        if lines:
            lines.append("")
        lines.append(note)
    content = "\n\n".join(lines).strip()
    return (content or str(title or "SparkFlow").strip() or "-")[:2000]


def send_notifyx(title: str, content: str = "", description: str = "") -> dict:
    cfg = load_notify().get("notifyx") or {}
    if not cfg.get("enabled"):
        raise RuntimeError("还没打开 NotifyX，请管理员先在设置里启用并填写 API Key")
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("请管理员先在设置里填写 NotifyX API Key")
    team = str(cfg.get("team") or "").strip()
    payload = {
        "title": (str(title or "SparkFlow").strip() or "SparkFlow")[:100],
        "content": (str(content or "-").strip() or "-")[:2000],
    }
    desc = str(description or "").strip()
    if desc:
        payload["description"] = desc[:500]
    if team:
        payload["team"] = team[:32]
    url = NOTIFYX_SEND.format(key=key)
    with httpx.Client(timeout=8.0) as client:
        resp = client.post(url, json=payload)
        try:
            data = resp.json()
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    if resp.status_code >= 400 or data.get("success") is False or data.get("error"):
        raise RuntimeError(
            str(data.get("error") or data.get("message") or f"NotifyX 发送失败 HTTP {resp.status_code}")
        )
    logger.info("NotifyX 推送成功 title=%s", title)
    return data


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def _attr(text) -> str:
    return html.escape(str(text or ""), quote=True)


def _html_text(text) -> str:
    return _esc(text).replace("\n", "<br/>")


def _kind_theme(kind: str) -> dict:
    key = str(kind or "").strip()
    if key in ("task_fail", "cookie_offline"):
        return {"accent": "#ff5d7a", "tag": "需要处理"}
    if key == "expire_soon":
        return {"accent": "#e8a317", "tag": "到期提醒"}
    if key in ("recharge", "invite_reward", "task_done"):
        return {"accent": "#ff7a3a", "tag": "已完成"}
    if key == "password_code":
        return {"accent": "#ff7a3a", "tag": "安全验证"}
    if key == "broadcast":
        return {"accent": "#ff7a3a", "tag": "系统通知"}
    if key == "test":
        return {"accent": "#ff7a3a", "tag": "通道测试"}
    return {"accent": "#ff7a3a", "tag": "SparkFlow"}


def render_wxpusher_html(
    title: str,
    body: str = "",
    kind: str = "",
    rows=None,
    footer: str = "",
    copy_text: str = "",
) -> str:
    theme = _kind_theme(kind)
    accent = theme["accent"]
    items = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        items.append((label, value))
    if not items:
        text = str(body or "").strip()
        if text:
            items.append(("", text))
    row_html = []
    for index, (label, value) in enumerate(items):
        line = "border-top:1px solid #f0e0d2;" if index else "border-top:none;"
        label_html = (
            f'<p style="margin:0;font-size:12px;color:#8a7466;letter-spacing:.04em;" '
            f'data-darkmode-color="#b9a394">{_esc(label)}</p>'
            if label
            else ""
        )
        value_margin = "margin:4px 0 0;" if label else "margin:0;"
        row_html.append(
            "<tr>"
            f'<td style="padding:12px 0;{line}">'
            f"{label_html}"
            f'<p style="{value_margin}font-size:15px;line-height:1.65;color:#24180f;" '
            f'data-darkmode-color="#f4efe8">{_html_text(value)}</p>'
            "</td>"
            "</tr>"
        )
    code = str(copy_text or "").strip()
    copy_html = ""
    if code:
        copy_html = (
            '<section style="padding:4px 18px 14px;" data-darkmode-bgcolor="#1a1410">'
            f'<copy data-clipboard-text="{_attr(code)}" style="display:block;text-align:center;'
            "padding:14px 12px;border-radius:12px;background:#ff7a3a;color:#fff;"
            'font-size:28px;font-weight:700;letter-spacing:8px;">'
            f"{_esc(code)}</copy>"
            '<p style="margin:8px 0 0;text-align:center;font-size:12px;color:#8a7466;" '
            'data-darkmode-color="#b9a394">点击验证码即可复制</p>'
            "</section>"
        )
    footer_html = ""
    note = str(footer or "").strip()
    if note:
        footer_html = (
            '<section style="padding:12px 18px 16px;background:#fff3ea;" '
            'data-darkmode-bgcolor="#24180f" data-darkmode-color="#d7c4b6">'
            f'<p style="margin:0;font-size:12px;line-height:1.6;color:#8a7466;" '
            f'data-darkmode-color="#d7c4b6">{_html_text(note)}</p>'
            "</section>"
        )
    rows_wrap = ""
    if row_html:
        rows_wrap = (
            '<section style="padding:2px 18px 10px;" data-darkmode-bgcolor="#1a1410">'
            '<table style="width:100%;border-collapse:collapse;">'
            f"{''.join(row_html)}</table></section>"
        )
    return (
        '<section style="margin:0;padding:2px 0 6px;font-family:-apple-system,BlinkMacSystemFont,'
        "'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;\" "
        'data-darkmode-bgcolor="transparent" data-darkmode-color="#f4efe8">'
        '<section style="border:1px solid #f0d9c8;border-radius:16px;overflow:hidden;'
        'background:#fffaf6;" data-darkmode-bgcolor="#1a1410" data-darkmode-color="#f4efe8">'
        f'<section style="height:4px;background:{accent};font-size:0;line-height:0;">&nbsp;</section>'
        '<section style="padding:16px 18px 8px;" data-darkmode-bgcolor="#1a1410">'
        f'<p style="margin:0;font-size:11px;color:{accent};letter-spacing:.06em;" '
        f'data-darkmode-color="#ffb07a">{_esc(theme["tag"]).upper()}</p>'
        "</section>"
        f"{copy_html}{rows_wrap}{footer_html}"
        "</section></section>"
    )


def send_wxpusher(
    title: str,
    body: str = "",
    uids=None,
    kind: str = "",
    rows=None,
    footer: str = "",
    copy_text: str = "",
) -> dict:
    token = _app_token()
    targets = _parse_uids(uids)
    if not targets:
        raise RuntimeError("还没扫码绑定微信")
    last = {}
    payload_content = render_wxpusher_html(
        title, body, kind=kind, rows=rows, footer=footer, copy_text=copy_text
    )
    summary = (str(title or "").strip() or "SparkFlow")[:20]
    with httpx.Client(timeout=8.0) as client:
        for i in range(0, len(targets), WP_UID_BATCH):
            chunk = targets[i : i + WP_UID_BATCH]
            payload = {
                "appToken": token,
                "content": payload_content,
                "summary": summary,
                "contentType": 2,
                "uids": chunk,
            }
            resp = client.post(WP_SEND, json=payload)
            data = resp.json()
            if int(data.get("code") or 0) != 1000:
                raise RuntimeError(data.get("msg") or "WxPusher 发送失败")
            last = data
    logger.info("WxPusher 推送成功 title=%s uids=%s", title, len(targets))
    return last


def list_bound_wxpusher_uids() -> list[str]:
    uids = []
    seen = set()
    for item in load_users():
        uid = str(item.get("wxpusher_uid") or "").strip()
        if uid and uid not in seen:
            seen.add(uid)
            uids.append(uid)
    return uids


def broadcast_to_bound(title: str, body: str) -> dict:
    title = str(title or "").strip() or "SparkFlow 通知"
    body = str(body or "").strip()
    if not body:
        raise RuntimeError("请填写通知内容")
    uids = list_bound_wxpusher_uids()
    nx = load_notify().get("notifyx") or {}
    can_nx = bool(nx.get("enabled") and str(nx.get("api_key") or "").strip())
    if not uids and not can_nx:
        raise RuntimeError("还没有用户绑定微信，也未配置 NotifyX")
    sent = 0
    if uids:
        send_wxpusher(
            title,
            body,
            uids=uids,
            kind="broadcast",
            rows=[{"label": "内容", "value": body}],
            footer="此消息由管理员发给所有已绑定微信的用户",
        )
        sent = len(uids)
    notifyx_sent = False
    if can_nx:
        send_notifyx(
            title,
            _notifyx_markdown(title, body, rows=[{"label": "内容", "value": body}], footer="管理员群发通知"),
            description=title,
        )
        notifyx_sent = True
    logger.info("已群发通知 title=%s users=%s notifyx=%s", title, sent, notifyx_sent)
    return {"sent": sent, "notifyx": notifyx_sent}


def _days_text(days, default: int = 1) -> str:
    n = parse_days(days, default=default)
    return "永久" if n == 0 else f"{n} 天"


def notify_event(
    kind: str,
    title: str,
    body: str = "",
    usernames=None,
    wechat_admin=None,
    rows=None,
    footer: str = "",
    copy_text: str = "",
) -> None:
    cfg = load_notify()
    events = cfg.get("events") or {}
    if kind != "test" and not events.get(kind, True):
        return
    if wechat_admin is None:
        wechat_admin = kind not in USER_EVENT_KEYS
    wechat = cfg.get("wechat") or {}
    wxpusher = cfg.get("wxpusher") or {}
    notifyx = cfg.get("notifyx") or {}
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
    can_wp = bool(wxpusher.get("enabled") and uids)
    can_wx = bool(wechat_admin and wechat.get("enabled"))
    can_nx = bool(notifyx.get("enabled") and str(notifyx.get("api_key") or "").strip())
    if not can_wp and not can_wx and not can_nx:
        if wxpusher.get("enabled") and names and not uids:
            logger.warning("WxPusher 跳过 kind=%s owners=%s 未绑定微信", kind, ",".join(names))
        return
    owners = ",".join(names) or "-"

    def _run():
        if can_wp:
            try:
                send_wxpusher(
                    title,
                    body,
                    uids=uids,
                    kind=kind,
                    rows=rows,
                    footer=footer,
                    copy_text=copy_text,
                )
            except Exception as exc:
                logger.warning("WxPusher 未发出 kind=%s owners=%s: %s", kind, owners, exc)
        if can_wx:
            try:
                send_wechat(kind, title, body)
            except Exception as exc:
                logger.warning("公众号未发出 kind=%s: %s", kind, exc)
        if can_nx:
            try:
                send_notifyx(
                    title,
                    _notifyx_markdown(title, body, rows=rows, footer=footer),
                    description=str(title or "")[:500],
                )
            except Exception as exc:
                logger.warning("NotifyX 未发出 kind=%s: %s", kind, exc)

    threading.Thread(target=_run, daemon=True).start()


def notify_recharge_success(username: str, card: dict | None = None, user: dict | None = None) -> None:
    name = str(username or "").strip()
    if not name:
        return
    card = card or {}
    days_txt = _days_text(card.get("days"), default=1)
    acc = parse_max_accounts(card.get("max_accounts"), default=1)
    acc_txt = "账号不限" if acc == 0 else f"{acc} 个账号"
    remain = remaining_label(user) if user else ""
    body = f"卡密充值成功，到账 {days_txt}，额度 {acc_txt}"
    if remain:
        body += f"。当前{remain}"
    rows = [
        {"label": "到账时长", "value": days_txt},
        {"label": "账号额度", "value": acc_txt},
    ]
    if remain:
        rows.append({"label": "当前有效期", "value": remain})
    notify_event(
        "recharge",
        "充值成功",
        body,
        usernames=[name],
        rows=rows,
        footer="可在控制台继续添加账号、续火花",
    )


def notify_invite_rewards(
    invitee_name: str,
    inviter_name: str = "",
    invitee_days=None,
    awarded_inviter_days=None,
    inviter_already_permanent: bool = False,
    invitee: dict | None = None,
    inviter: dict | None = None,
) -> None:
    guest = str(invitee_name or "").strip()
    host = str(inviter_name or "").strip()
    if guest:
        days_txt = _days_text(invitee_days, default=1)
        remain = remaining_label(invitee) if invitee else ""
        body = f"已绑定微信，邀请成功。获得 {days_txt}"
        if remain:
            body += f"，当前{remain}"
        rows = [
            {"label": "奖励", "value": days_txt},
            {"label": "状态", "value": "已绑定微信，邀请成功"},
        ]
        if remain:
            rows.append({"label": "当前有效期", "value": remain})
        notify_event(
            "invite_reward",
            "邀请奖励已到账",
            body,
            usernames=[guest],
            rows=rows,
            footer="邀请奖励需绑定微信后才会发放",
        )
    if not host:
        return
    if inviter_already_permanent:
        host_body = f"好友 {guest} 已绑定微信。你是永久会员，未再加时长"
        host_rows = [
            {"label": "好友", "value": guest or "-"},
            {"label": "状态", "value": "已绑定微信"},
            {"label": "你的奖励", "value": "永久会员，未再加时长"},
        ]
    else:
        host_days = _days_text(awarded_inviter_days, default=1)
        host_body = f"好友 {guest} 已绑定微信。你获得 {host_days}"
        remain = remaining_label(inviter) if inviter else ""
        if remain:
            host_body += f"，当前{remain}"
        host_rows = [
            {"label": "好友", "value": guest or "-"},
            {"label": "状态", "value": "已绑定微信"},
            {"label": "你的奖励", "value": host_days},
        ]
        if remain:
            host_rows.append({"label": "当前有效期", "value": remain})
    notify_event(
        "invite_reward",
        "邀请成功",
        host_body,
        usernames=[host],
        rows=host_rows,
        footer="邀请成功以好友绑定微信为准",
    )


def tick_expire_reminders() -> dict:
    cfg = load_notify()
    events = cfg.get("events") or {}
    if not events.get("expire_soon", True):
        return {"ok": True, "sent": 0, "skipped": "disabled"}
    wxpusher = cfg.get("wxpusher") or {}
    notifyx = cfg.get("notifyx") or {}
    can_nx = bool(notifyx.get("enabled") and str(notifyx.get("api_key") or "").strip())
    if not wxpusher.get("enabled") and not can_nx:
        return {"ok": True, "sent": 0, "skipped": "channel"}
    now = now_utc()
    users = load_users()
    sent = 0
    dirty = False
    for user in users:
        name = str(user.get("username") or "").strip()
        if not name or is_permanent(user):
            continue
        exp = parse_iso(user.get("expires_at"))
        if not exp:
            continue
        seconds = (exp - now).total_seconds()
        if seconds <= 0:
            continue
        exp_key = str(user.get("expires_at") or "")
        if str(user.get("expire_notice_for") or "") != exp_key:
            user["expire_notice_for"] = exp_key
            user["expire_notice_24h"] = False
            user["expire_notice_12h"] = False
            dirty = True
        has_uid = bool(str(user.get("wxpusher_uid") or "").strip())
        if not has_uid and not can_nx:
            continue
        hours = seconds / 3600.0
        remain = remaining_label(user)
        if 12 < hours <= 24 and not user.get("expire_notice_24h"):
            notify_event(
                "expire_soon",
                "会员即将到期",
                f"你的会员将在 24 小时内到期。当前{remain}。到期后可在「我的」用卡密续费。",
                usernames=[name],
                rows=[
                    {"label": "提醒节点", "value": "到期前 24 小时"},
                    {"label": "当前剩余", "value": remain},
                ],
                footer="到期后可在「我的」用卡密续费",
            )
            user["expire_notice_24h"] = True
            dirty = True
            sent += 1
        if 0 < hours <= 12 and not user.get("expire_notice_12h"):
            notify_event(
                "expire_soon",
                "会员即将到期",
                f"你的会员将在 12 小时内到期。当前{remain}。到期后可在「我的」用卡密续费。",
                usernames=[name],
                rows=[
                    {"label": "提醒节点", "value": "到期前 12 小时"},
                    {"label": "当前剩余", "value": remain},
                ],
                footer="到期后可在「我的」用卡密续费",
            )
            user["expire_notice_12h"] = True
            dirty = True
            sent += 1
    if dirty:
        save_users(users)
    if sent:
        logger.info("到期提醒已排队 sent=%s", sent)
    return {"ok": True, "sent": sent}


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
    try:
        saved = bind_user_wxpusher(name, uid)
    except ValueError as exc:
        left = 0
        with _bind_lock:
            session = _bind_sessions.get(name)
            if _session_alive(session):
                left = max(0, int(float(session.get("expire") or 0) - time.time()))
        return {
            "ok": False,
            "waiting": True,
            "expired": False,
            "expire_in": left,
            "error": str(exc),
            **public_wxpusher(find_user(name)),
        }
    cancel_wxpusher_qr(name)
    logger.info("WxPusher 扫码绑定成功 user=%s", name)
    return {"ok": True, "waiting": False, "bound": True, **public_wxpusher(saved)}


def cancel_wxpusher_qr(username: str) -> None:
    name = str(username or "").strip()
    with _bind_lock:
        _close_session(_bind_sessions.pop(name, None))


def apply_wxpusher_callback(payload: dict | None) -> dict:
    data = payload or {}
    info = data.get("data") if isinstance(data.get("data"), dict) else data
    extra = str((info or {}).get("extra") or "").strip()
    if not extra or not find_user(extra):
        return {"ok": True, "bound": False}
    with _bind_lock:
        session = _bind_sessions.get(extra)
        if not _session_alive(session):
            logger.warning("拒绝无进行中扫码会话的 WxPusher 回调 extra=%s", extra)
            return {"ok": True, "bound": False}
        code = str(session.get("code") or "").strip()
        expect_extra = str(session.get("extra") or extra).strip()
    if not code:
        return {"ok": True, "bound": False}
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(WP_SCAN_UID, params={"code": code})
            result = resp.json()
    except Exception as exc:
        logger.warning("WxPusher 回调核验扫码失败 extra=%s: %s", extra, exc)
        return {"ok": True, "bound": False}
    payload_data = result.get("data") if isinstance(result.get("data"), dict) else {}
    uid = str((payload_data or {}).get("uid") or "").strip()
    if not uid and isinstance(result.get("data"), str):
        uid = str(result.get("data") or "").strip()
    got_extra = str((payload_data or {}).get("extra") or "").strip()
    if got_extra and got_extra != expect_extra:
        logger.warning("WxPusher 回调 extra 不匹配 expect=%s got=%s", expect_extra, got_extra)
        return {"ok": True, "bound": False}
    if not uid or int(result.get("code") or 0) != 1000:
        return {"ok": True, "bound": False}
    try:
        saved = bind_user_wxpusher(extra, uid)
    except ValueError as exc:
        logger.warning("WxPusher 回调绑定拒绝 extra=%s: %s", extra, exc)
        return {"ok": True, "bound": False, "error": str(exc)}
    with _bind_lock:
        _close_session(_bind_sessions.pop(extra, None))
    logger.info("WxPusher 回调绑定成功 user=%s", extra)
    return {"ok": True, "bound": True, **public_wxpusher(saved)}
