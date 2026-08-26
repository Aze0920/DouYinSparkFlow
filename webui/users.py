import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USERS_FILE = ROOT / "config" / "users.json"
AUTH_SECRET_FILE = ROOT / "config" / "auth_secret"
LEGACY_SECRET = "dsf-user-v1"
USERNAME_RE = re.compile(r"^[\w.-]{2,24}$", re.UNICODE)
PROTECTED_USERNAMES = {"admin"}
_secret_holder = {"value": ""}


def _digest_eq(left: str, right: str) -> bool:
    a = str(left or "")
    b = str(right or "")
    if not a or not b or len(a) != len(b):
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _get_secret() -> str:
    if _secret_holder["value"]:
        return _secret_holder["value"]
    env_secret = str(os.getenv("AUTH_SECRET") or "").strip()
    if len(env_secret) >= 16:
        _secret_holder["value"] = env_secret
        return env_secret
    AUTH_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if AUTH_SECRET_FILE.is_file():
        text = AUTH_SECRET_FILE.read_text(encoding="utf-8").strip().splitlines()
        stored = (text[0] if text else "").strip()
        if len(stored) >= 16:
            _secret_holder["value"] = stored
            return stored
    secret = secrets.token_hex(32)
    AUTH_SECRET_FILE.write_text(secret + "\n", encoding="utf-8")
    try:
        os.chmod(AUTH_SECRET_FILE, 0o600)
    except OSError:
        pass
    _secret_holder["value"] = secret
    return secret


def _hmac_hex(payload: str) -> str:
    return hmac.new(_get_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _hash_password(username: str, password: str) -> str:
    return _hmac_hex(f"{username}|{password}")


def _legacy_hash_password(username: str, password: str) -> str:
    return hashlib.sha256(f"{LEGACY_SECRET}|{username}|{password}".encode("utf-8")).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_days(value, default: int = 1) -> int:
    if value in (None, ""):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < 0:
        n = 0
    return min(n, 3650)


def parse_max_accounts(value, default: int = 1) -> int:
    if value in (None, ""):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < 0:
        n = 0
    return min(n, 100)


def is_protected_username(username: str) -> bool:
    return str(username or "").strip().lower() in PROTECTED_USERNAMES


def is_permanent(user: dict | None) -> bool:
    if not user:
        return False
    if user.get("role") == "admin":
        return True
    return bool(user.get("permanent"))


def is_expired(user: dict | None) -> bool:
    if not user:
        return True
    if is_permanent(user):
        return False
    exp = parse_iso(user.get("expires_at"))
    if not exp:
        return True
    return now_utc() >= exp


def user_can_spark(user: dict | None) -> bool:
    return bool(user) and not is_expired(user)


def account_limit(user: dict | None) -> int:
    if not user:
        return 1
    if user.get("role") == "admin":
        return 0
    try:
        n = int(user.get("max_accounts") if user.get("max_accounts") not in (None, "") else 1)
    except (TypeError, ValueError):
        n = 1
    if n <= 0:
        return 0
    return min(n, 100)


def account_limit_label(user: dict | None) -> str:
    n = account_limit(user)
    return "账号不限" if n == 0 else f"{n} 个账号"


def remaining_label(user: dict | None) -> str:
    if is_permanent(user):
        return "永久"
    exp = parse_iso((user or {}).get("expires_at"))
    if not exp:
        return "已过期"
    left = int((exp - now_utc()).total_seconds())
    if left <= 0:
        return "已过期"
    hours, rem = divmod(left, 3600)
    minutes = rem // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"剩余 {days} 天 {hours} 小时"
    if hours > 0:
        return f"剩余 {hours} 小时 {minutes} 分"
    return f"剩余 {max(minutes, 1)} 分"


def make_user(
    username: str,
    password: str,
    role: str = "user",
    days: int | None = None,
    max_accounts: int | None = None,
    card_code: str = "",
) -> dict:
    created = now_utc()
    role = "admin" if role == "admin" else "user"
    days_n = parse_days(days, default=1)
    accounts_n = parse_max_accounts(max_accounts, default=1)
    permanent = role == "admin" or days_n == 0
    row = {
        "username": username.strip(),
        "password_hash": _hash_password(username.strip(), password),
        "role": role,
        "created_at": to_iso(created),
        "permanent": permanent,
        "expires_at": None if permanent else to_iso(created + timedelta(days=days_n)),
        "max_accounts": 0 if role == "admin" else accounts_n,
        "card_code": str(card_code or "").strip(),
    }
    return row


def normalize_user(user: dict) -> tuple[dict, bool]:
    changed = False
    if not user.get("created_at"):
        user["created_at"] = to_iso(now_utc())
        changed = True
    if user.get("role") == "admin":
        if not user.get("permanent") or user.get("expires_at") or user.get("max_accounts") not in (0, "0"):
            user["permanent"] = True
            user["expires_at"] = None
            user["max_accounts"] = 0
            changed = True
    else:
        permanent = bool(user.get("permanent"))
        if user.get("permanent") != permanent:
            user["permanent"] = permanent
            changed = True
        if permanent and user.get("expires_at"):
            user["expires_at"] = None
            changed = True
        if user.get("max_accounts") in (None, ""):
            user["max_accounts"] = 1
            changed = True
        else:
            n = parse_max_accounts(user.get("max_accounts"), default=1)
            if user.get("max_accounts") != n:
                user["max_accounts"] = n
                changed = True
    return user, changed


def _default_users() -> dict:
    return {"users": [make_user("admin", "admin", role="admin")]}


def load_users() -> list:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.is_file():
        data = _default_users()
        USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data["users"]
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        users = data.get("users") or []
        if not users:
            users = _default_users()["users"]
            save_users(users)
            return users
    except Exception:
        users = _default_users()["users"]
        save_users(users)
        return users
    changed = False
    out = []
    for item in users:
        row, ch = normalize_user(dict(item))
        out.append(row)
        changed = changed or ch
    if changed:
        save_users(out)
    return out


def save_users(users: list) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=2), encoding="utf-8")


def find_user(username: str):
    name = (username or "").strip()
    for user in load_users():
        if user.get("username") == name:
            return user
    return None


def username_taken(username: str) -> bool:
    name = (username or "").strip().lower()
    if not name:
        return False
    return any(str(user.get("username") or "").strip().lower() == name for user in load_users())


def verify_user(username: str, password: str):
    user = find_user(username)
    if not user:
        return None
    name = username.strip()
    stored = str(user.get("password_hash") or "")
    if _digest_eq(stored, _hash_password(name, password)):
        return user
    if _digest_eq(stored, _legacy_hash_password(name, password)):
        upgraded = _hash_password(name, password)
        users = load_users()
        for item in users:
            if item.get("username") == name:
                item["password_hash"] = upgraded
                break
        save_users(users)
        user["password_hash"] = upgraded
        return user
    return None


def _mask_tail(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 4:
        return "已绑定"
    return "••••" + raw[-4:]


def public_user(user: dict) -> dict:
    created = parse_iso(user.get("created_at"))
    exp = parse_iso(user.get("expires_at"))
    return {
        "username": user.get("username"),
        "role": user.get("role") or "user",
        "permanent": is_permanent(user),
        "expired": is_expired(user),
        "can_spark": user_can_spark(user),
        "created_at": user.get("created_at") or "",
        "expires_at": user.get("expires_at") or "",
        "created_label": created.astimezone().strftime("%Y-%m-%d %H:%M") if created else "-",
        "expires_label": "永久" if is_permanent(user) else (exp.astimezone().strftime("%Y-%m-%d %H:%M") if exp else "-"),
        "remain_label": remaining_label(user),
        "max_accounts": account_limit(user),
        "account_limit_label": account_limit_label(user),
        "card_code": user.get("card_code") or "",
        "invite_pending": bool(user.get("invite_pending") and not user.get("invite_rewarded")),
        "wxpusher_bound": bool(str(user.get("wxpusher_uid") or "").strip()),
        "wxpusher_mask": _mask_tail(user.get("wxpusher_uid")),
        "wxpusher_bound_at": user.get("wxpusher_bound_at") or "",
        "protected": is_protected_username(user.get("username") or ""),
    }


def make_token(username: str) -> str:
    name = str(username or "").strip()
    return f"{_hmac_hex(f'token|{name}')}:{name}"


def parse_token(token: str):
    if not token or ":" not in token:
        return None
    sig, username = token.split(":", 1)
    name = str(username or "").strip()
    if not name or not valid_username(name):
        return None
    if not _digest_eq(sig, _hmac_hex(f"token|{name}")):
        return None
    return find_user(name)


def admin_count(users: list | None = None) -> int:
    users = users if users is not None else load_users()
    return sum(1 for u in users if u.get("role") == "admin")


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.match((username or "").strip()))


def extend_user(user: dict, days: int) -> dict:
    days_n = parse_days(days, default=0)
    if days_n <= 0:
        user["permanent"] = True
        user["expires_at"] = None
        return user
    if is_permanent(user):
        return user
    start = now_utc()
    current_exp = parse_iso(user.get("expires_at"))
    if current_exp and current_exp > start:
        start = current_exp
    user["expires_at"] = to_iso(start + timedelta(days=days_n))
    user["permanent"] = False
    return user


def apply_card_benefits(user: dict, card: dict | None) -> dict:
    card = card or {}
    days_n = parse_days(card.get("days"), default=1)
    extend_user(user, days_n)
    if user.get("role") == "admin":
        return user
    acc = parse_max_accounts(card.get("max_accounts"), default=1)
    cur = account_limit(user)
    if acc == 0:
        user["max_accounts"] = 0
    elif cur != 0:
        user["max_accounts"] = max(cur, acc)
    return user
