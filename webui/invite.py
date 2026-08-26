import json
import secrets
import threading
from pathlib import Path

from webui.users import (
    extend_user,
    find_user,
    is_expired,
    is_permanent,
    load_users,
    make_user,
    now_utc,
    parse_days,
    parse_iso,
    remaining_label,
    save_users,
    to_iso,
)

ROOT = Path(__file__).resolve().parent.parent
INVITE_FILE = ROOT / "config" / "invite.json"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_lock = threading.Lock()


def default_invite_data() -> dict:
    return {
        "settings": {
            "enabled": False,
            "inviter_days": 1,
            "invitee_days": 1,
        },
        "codes": {},
        "records": [],
    }


def load_invite() -> dict:
    INVITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = default_invite_data()
    if not INVITE_FILE.is_file():
        return data
    try:
        raw = json.loads(INVITE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return data
    if not isinstance(raw, dict):
        return data
    settings = dict(data["settings"])
    incoming = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    settings["enabled"] = bool(incoming.get("enabled", settings["enabled"]))
    settings["inviter_days"] = parse_days(incoming.get("inviter_days"), default=1)
    settings["invitee_days"] = parse_days(incoming.get("invitee_days"), default=1)
    codes = raw.get("codes") if isinstance(raw.get("codes"), dict) else {}
    records = raw.get("records") if isinstance(raw.get("records"), list) else []
    return {"settings": settings, "codes": codes, "records": records}


def save_invite(data: dict) -> dict:
    INVITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "settings": dict((data or {}).get("settings") or default_invite_data()["settings"]),
        "codes": dict((data or {}).get("codes") or {}),
        "records": list((data or {}).get("records") or []),
    }
    INVITE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def public_settings(data: dict | None = None) -> dict:
    settings = dict((data or load_invite()).get("settings") or {})
    inviter_days = parse_days(settings.get("inviter_days"), default=1)
    invitee_days = parse_days(settings.get("invitee_days"), default=1)
    return {
        "enabled": bool(settings.get("enabled")),
        "inviter_days": inviter_days,
        "invitee_days": invitee_days,
        "inviter_days_label": "永久" if inviter_days == 0 else f"{inviter_days} 天",
        "invitee_days_label": "永久" if invitee_days == 0 else f"{invitee_days} 天",
    }


def award_days_label(days, skipped: bool = False) -> str:
    if skipped:
        return "未加时长"
    if days in (None, ""):
        return "-"
    n = parse_days(days, default=0)
    return "永久" if n == 0 else f"{n} 天"


def can_invite(user: dict | None, data: dict | None = None) -> bool:
    if not user:
        return False
    settings = public_settings(data)
    if not settings["enabled"]:
        return False
    return not is_expired(user)


def _new_code(existing: set[str]) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if code not in existing:
            return code
    return secrets.token_hex(5).upper()


def _code_for_user(data: dict, username: str) -> str:
    name = str(username or "").strip()
    for code, row in (data.get("codes") or {}).items():
        if str((row or {}).get("username") or "").strip() == name:
            return str(code)
    return ""


def ensure_invite_code(username: str, rotate: bool = False) -> dict:
    name = str(username or "").strip()
    user = find_user(name)
    if not user:
        raise ValueError("用户不存在")
    with _lock:
        data = load_invite()
        if not can_invite(user, data):
            if is_expired(user):
                raise ValueError("会员已过期，续期后才能邀请")
            raise ValueError("管理员还没有开启邀请")
        codes = dict(data.get("codes") or {})
        current = _code_for_user(data, name)
        if current and not rotate:
            return {"code": current, **public_settings(data)}
        if current:
            codes.pop(current, None)
        existing = {str(k).upper() for k in codes}
        code = _new_code(existing)
        codes[code] = {"username": name, "created_at": to_iso(now_utc())}
        data["codes"] = codes
        save_invite(data)
        return {"code": code, **public_settings(data)}


def resolve_invite(code: str) -> tuple[dict, dict]:
    key = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    if not key:
        raise ValueError("邀请码无效")
    data = load_invite()
    settings = public_settings(data)
    if not settings["enabled"]:
        raise ValueError("邀请未开启")
    row = (data.get("codes") or {}).get(key) or (data.get("codes") or {}).get(key.lower())
    if not row:
        # case-insensitive scan
        row = next((item for ck, item in (data.get("codes") or {}).items() if str(ck).upper() == key), None)
    if not row:
        raise ValueError("邀请码无效或已失效")
    inviter = find_user(str(row.get("username") or ""))
    if not inviter:
        raise ValueError("邀请已过期")
    if is_expired(inviter):
        raise ValueError("邀请已过期")
    return inviter, settings


def preview_invite(code: str) -> dict:
    try:
        inviter, settings = resolve_invite(code)
    except ValueError as exc:
        return {"ok": True, "valid": False, "reason": str(exc), **public_settings()}
    return {
        "ok": True,
        "valid": True,
        "reason": "",
        "inviter": inviter.get("username"),
        **settings,
    }


def record_invite(
    inviter: str,
    invitee: str,
    inviter_days,
    invitee_days: int,
    inviter_already_permanent: bool = False,
) -> None:
    with _lock:
        data = load_invite()
        records = list(data.get("records") or [])
        records.insert(
            0,
            {
                "inviter": str(inviter or "").strip(),
                "invitee": str(invitee or "").strip(),
                "inviter_days": None if inviter_already_permanent else inviter_days,
                "invitee_days": invitee_days,
                "inviter_already_permanent": bool(inviter_already_permanent),
                "created_at": to_iso(now_utc()),
            },
        )
        data["records"] = records[:2000]
        save_invite(data)


def apply_invite_register(username: str, password: str, code: str) -> dict:
    inviter, settings = resolve_invite(code)
    inviter_name = str(inviter.get("username") or "")
    if inviter_name.lower() == str(username or "").strip().lower():
        raise ValueError("不能邀请自己")
    invitee_days = settings["invitee_days"]
    inviter_days = settings["inviter_days"]
    users = load_users()
    if any(str(item.get("username") or "").strip().lower() == str(username).strip().lower() for item in users):
        raise ValueError("用户名已存在")
    user = make_user(
        username,
        password,
        role="user",
        days=invitee_days,
        max_accounts=1,
        card_code="",
    )
    user["invited_by"] = inviter_name
    inviter_already_permanent = False
    awarded_inviter_days = inviter_days
    for item in users:
        if item.get("username") != inviter_name:
            continue
        if is_permanent(item):
            inviter_already_permanent = True
            awarded_inviter_days = None
        else:
            extend_user(item, inviter_days)
            awarded_inviter_days = inviter_days
        break
    users.append(user)
    save_users(users)
    record_invite(
        inviter_name,
        username.strip(),
        awarded_inviter_days,
        invitee_days,
        inviter_already_permanent=inviter_already_permanent,
    )
    return user


def my_invite_payload(user: dict, origin: str = "") -> dict:
    data = load_invite()
    settings = public_settings(data)
    name = str((user or {}).get("username") or "")
    code = _code_for_user(data, name)
    allowed = can_invite(user, data)
    expired = is_expired(user)
    if allowed and not code:
        try:
            ensured = ensure_invite_code(name, rotate=False)
            code = ensured.get("code") or ""
            settings = public_settings()
        except ValueError:
            code = ""
    origin = str(origin or "").rstrip("/")
    link = f"{origin}/?invite={code}" if code and origin else (f"/?invite={code}" if code else "")
    records = []
    for item in data.get("records") or []:
        if str(item.get("inviter") or "") != name:
            continue
        created = parse_iso(item.get("created_at"))
        skipped = bool(item.get("inviter_already_permanent"))
        records.append(
            {
                "invitee": item.get("invitee") or "",
                "inviter_days": item.get("inviter_days"),
                "invitee_days": item.get("invitee_days"),
                "inviter_already_permanent": skipped,
                "inviter_days_label": award_days_label(item.get("inviter_days"), skipped=skipped),
                "invitee_days_label": award_days_label(item.get("invitee_days")),
                "created_at": item.get("created_at") or "",
                "created_label": created.astimezone().strftime("%Y-%m-%d %H:%M") if created else "-",
            }
        )
        if len(records) >= 100:
            break
    permanent = is_permanent(user)
    if not settings["enabled"]:
        reward_hint = ""
    elif not allowed:
        reward_hint = (
            f"续期后可继续邀请。对方将获得 {settings['invitee_days_label']}。"
            f"已邀请 {len(records)} 人。"
        )
    elif permanent:
        reward_hint = (
            f"你已是永久会员，邀请成功不会再增加天数。对方获得 {settings['invitee_days_label']}。"
            f"已邀请 {len(records)} 人。"
        )
    elif settings["inviter_days"] == 0:
        reward_hint = (
            f"邀请成功：你将获得永久会员，对方获得 {settings['invitee_days_label']}。"
            f"已邀请 {len(records)} 人。"
        )
    else:
        reward_hint = (
            f"邀请成功：你获得 {settings['inviter_days_label']}，对方获得 {settings['invitee_days_label']}。"
            f"已邀请 {len(records)} 人。"
        )
    return {
        **settings,
        "can_invite": allowed,
        "expired": expired,
        "permanent": permanent,
        "remain_label": remaining_label(user),
        "code": code if allowed else "",
        "link": link if allowed else "",
        "link_expired": bool(code) and not allowed,
        "reward_hint": reward_hint,
        "records": records,
        "record_count": len(records),
    }


def save_invite_settings(payload: dict | None) -> dict:
    payload = payload or {}
    with _lock:
        data = load_invite()
        settings = dict(data.get("settings") or {})
        if "enabled" in payload:
            settings["enabled"] = bool(payload.get("enabled"))
        if "inviter_days" in payload:
            settings["inviter_days"] = parse_days(payload.get("inviter_days"), default=1)
        if "invitee_days" in payload:
            settings["invitee_days"] = parse_days(payload.get("invitee_days"), default=1)
        data["settings"] = settings
        save_invite(data)
        return public_settings(data)
