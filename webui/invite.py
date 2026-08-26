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


def _row_enabled(row) -> bool:
    if not isinstance(row, dict):
        return False
    if "enabled" not in row:
        return True
    return bool(row.get("enabled"))


def _lookup_row(data: dict, code: str):
    key = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    if not key:
        return "", None
    codes = data.get("codes") or {}
    row = codes.get(key) or codes.get(key.lower())
    if row:
        return key, row
    for ck, item in codes.items():
        if str(ck).upper() == key:
            return str(ck), item
    return key, None


def _ensure_code_locked(data: dict, name: str) -> tuple[str, bool]:
    current = _code_for_user(data, name)
    if current:
        return current, False
    codes = dict(data.get("codes") or {})
    code = _new_code({str(k).upper() for k in codes})
    codes[code] = {
        "username": name,
        "created_at": to_iso(now_utc()),
        "enabled": False,
    }
    data["codes"] = codes
    return code, True


def _user_enabled(data: dict, username: str) -> bool:
    code = _code_for_user(data, username)
    if not code:
        return False
    return _row_enabled((data.get("codes") or {}).get(code))


def can_invite(user: dict | None, data: dict | None = None) -> bool:
    if not user:
        return False
    if is_expired(user):
        return False
    return _user_enabled(data or load_invite(), str(user.get("username") or ""))


def ensure_invite_code(username: str) -> dict:
    name = str(username or "").strip()
    user = find_user(name)
    if not user:
        raise ValueError("用户不存在")
    with _lock:
        data = load_invite()
        code, created = _ensure_code_locked(data, name)
        if created:
            save_invite(data)
        return {"code": code, "user_enabled": _user_enabled(data, name), **public_settings(data)}


def set_user_invite_enabled(username: str, enabled: bool) -> dict:
    name = str(username or "").strip()
    user = find_user(name)
    if not user:
        raise ValueError("用户不存在")
    if enabled and is_expired(user):
        raise ValueError("会员已过期，续期后才能邀请")
    with _lock:
        data = load_invite()
        code, _created = _ensure_code_locked(data, name)
        row = dict((data.get("codes") or {}).get(code) or {})
        row["username"] = name
        if not row.get("created_at"):
            row["created_at"] = to_iso(now_utc())
        row["enabled"] = bool(enabled)
        codes = dict(data.get("codes") or {})
        codes[code] = row
        data["codes"] = codes
        save_invite(data)
        return {"code": code, "user_enabled": bool(enabled), **public_settings(data)}


def resolve_invite(code: str) -> tuple[dict, dict]:
    data = load_invite()
    settings = public_settings(data)
    _key, row = _lookup_row(data, code)
    if not row:
        raise ValueError("邀请码无效或已失效")
    if not _row_enabled(row):
        raise ValueError("邀请未开启")
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
    status: str = "pending",
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
                "status": status,
                "created_at": to_iso(now_utc()),
            },
        )
        data["records"] = records[:2000]
        save_invite(data)


def _mark_invite_rewarded(
    inviter: str,
    invitee: str,
    inviter_days,
    invitee_days: int,
    inviter_already_permanent: bool = False,
) -> None:
    inviter_name = str(inviter or "").strip()
    invitee_name = str(invitee or "").strip()
    with _lock:
        data = load_invite()
        records = list(data.get("records") or [])
        updated = False
        for item in records:
            if str(item.get("inviter") or "") != inviter_name:
                continue
            if str(item.get("invitee") or "") != invitee_name:
                continue
            if str(item.get("status") or "rewarded") not in {"pending", ""}:
                continue
            item["status"] = "rewarded"
            item["inviter_days"] = None if inviter_already_permanent else inviter_days
            item["invitee_days"] = invitee_days
            item["inviter_already_permanent"] = bool(inviter_already_permanent)
            item["rewarded_at"] = to_iso(now_utc())
            updated = True
            break
        if not updated:
            records.insert(
                0,
                {
                    "inviter": inviter_name,
                    "invitee": invitee_name,
                    "inviter_days": None if inviter_already_permanent else inviter_days,
                    "invitee_days": invitee_days,
                    "inviter_already_permanent": bool(inviter_already_permanent),
                    "status": "rewarded",
                    "created_at": to_iso(now_utc()),
                    "rewarded_at": to_iso(now_utc()),
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
        days=1,
        max_accounts=1,
        card_code="",
    )
    user["permanent"] = False
    user["expires_at"] = to_iso(now_utc())
    user["invited_by"] = inviter_name
    user["invite_pending"] = True
    user["invite_rewarded"] = False
    user["invitee_reward_days"] = invitee_days
    user["inviter_reward_days"] = inviter_days
    users.append(user)
    save_users(users)
    record_invite(
        inviter_name,
        username.strip(),
        inviter_days,
        invitee_days,
        inviter_already_permanent=is_permanent(inviter),
        status="pending",
    )
    return user


def complete_invite_on_bind(username: str) -> dict | None:
    name = str(username or "").strip()
    if not name:
        return None
    users = load_users()
    invitee = next((item for item in users if item.get("username") == name), None)
    if not invitee:
        return None
    if not invitee.get("invite_pending") or invitee.get("invite_rewarded"):
        return invitee
    invitee_days = parse_days(invitee.get("invitee_reward_days"), default=1)
    inviter_days = parse_days(invitee.get("inviter_reward_days"), default=1)
    inviter_name = str(invitee.get("invited_by") or "")
    extend_user(invitee, invitee_days)
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
    invitee["invite_pending"] = False
    invitee["invite_rewarded"] = True
    invitee["invite_rewarded_at"] = to_iso(now_utc())
    save_users(users)
    _mark_invite_rewarded(
        inviter_name,
        name,
        awarded_inviter_days,
        invitee_days,
        inviter_already_permanent=inviter_already_permanent,
    )
    return invitee


def my_invite_payload(user: dict, origin: str = "") -> dict:
    name = str((user or {}).get("username") or "")
    with _lock:
        data = load_invite()
        code = ""
        if name:
            code, created = _ensure_code_locked(data, name)
            if created:
                save_invite(data)
        settings = public_settings(data)
        user_enabled = _user_enabled(data, name) if name else False
        records_src = list(data.get("records") or [])
    allowed = bool(user) and (not is_expired(user)) and user_enabled
    expired = is_expired(user)
    origin = str(origin or "").rstrip("/")
    link = f"{origin}/?invite={code}" if code and origin else (f"/?invite={code}" if code else "")
    records = []
    for item in records_src:
        if str(item.get("inviter") or "") != name:
            continue
        created = parse_iso(item.get("created_at"))
        skipped = bool(item.get("inviter_already_permanent"))
        status = str(item.get("status") or "rewarded")
        pending = status == "pending"
        records.append(
            {
                "invitee": item.get("invitee") or "",
                "inviter_days": item.get("inviter_days"),
                "invitee_days": item.get("invitee_days"),
                "inviter_already_permanent": skipped,
                "status": status,
                "inviter_days_label": "待绑定微信" if pending else award_days_label(item.get("inviter_days"), skipped=skipped),
                "invitee_days_label": "待绑定微信" if pending else award_days_label(item.get("invitee_days")),
                "created_at": item.get("created_at") or "",
                "created_label": created.astimezone().strftime("%Y-%m-%d %H:%M") if created else "-",
            }
        )
        if len(records) >= 100:
            break
    permanent = is_permanent(user)
    if expired:
        reward_hint = (
            f"会员已过期，链接暂时无效。续期后不用换链接。对方将获得 {settings['invitee_days_label']}。"
            f"已邀请 {len(records)} 人。"
        )
    elif not user_enabled:
        reward_hint = (
            f"打开上方开关后，把固定链接发给朋友即可。对方绑定微信后才发放天数，将获得 {settings['invitee_days_label']}。"
            f"已邀请 {len(records)} 人。"
        )
    elif permanent:
        reward_hint = (
            f"你已是永久会员，邀请成功不会再增加天数。对方绑定微信后才发放奖励，对方获得 {settings['invitee_days_label']}。"
            f"已邀请 {len(records)} 人。"
        )
    elif settings["inviter_days"] == 0:
        reward_hint = (
            f"对方绑定微信后才算邀请成功：你将获得永久会员，对方获得 {settings['invitee_days_label']}。"
            f"已邀请 {len(records)} 人。"
        )
    else:
        reward_hint = (
            f"对方绑定微信后才算邀请成功：你获得 {settings['inviter_days_label']}，对方获得 {settings['invitee_days_label']}。"
            f"已邀请 {len(records)} 人。"
        )
    return {
        **settings,
        "user_enabled": user_enabled,
        "can_invite": allowed,
        "expired": expired,
        "permanent": permanent,
        "remain_label": remaining_label(user),
        "code": code,
        "link": link,
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
        if "inviter_days" in payload:
            settings["inviter_days"] = parse_days(payload.get("inviter_days"), default=1)
        if "invitee_days" in payload:
            settings["invitee_days"] = parse_days(payload.get("invitee_days"), default=1)
        data["settings"] = settings
        save_invite(data)
        return public_settings(data)
