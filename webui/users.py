import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USERS_FILE = ROOT / "config" / "users.json"
SECRET = "dsf-user-v1"
TRIAL_HOURS = 24
USERNAME_RE = re.compile(r"^[\w.-]{2,24}$", re.UNICODE)


def _hash_password(username: str, password: str) -> str:
    return hashlib.sha256(f"{SECRET}|{username}|{password}".encode("utf-8")).hexdigest()


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


def make_user(username: str, password: str, role: str = "user", days: int | None = None) -> dict:
    created = now_utc()
    role = "admin" if role == "admin" else "user"
    row = {
        "username": username.strip(),
        "password_hash": _hash_password(username.strip(), password),
        "role": role,
        "created_at": to_iso(created),
        "permanent": role == "admin",
        "expires_at": None if role == "admin" else to_iso(created + timedelta(hours=TRIAL_HOURS if days is None else max(1, int(days)) * 24)),
    }
    if role == "admin":
        row["expires_at"] = None
        row["permanent"] = True
    return row


def normalize_user(user: dict) -> tuple[dict, bool]:
    changed = False
    if not user.get("created_at"):
        user["created_at"] = to_iso(now_utc())
        changed = True
    if user.get("role") == "admin":
        if not user.get("permanent") or user.get("expires_at"):
            user["permanent"] = True
            user["expires_at"] = None
            changed = True
    elif not user.get("expires_at"):
        created = parse_iso(user.get("created_at")) or now_utc()
        user["expires_at"] = to_iso(created + timedelta(hours=TRIAL_HOURS))
        user["permanent"] = False
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


def verify_user(username: str, password: str):
    user = find_user(username)
    if not user:
        return None
    if user.get("password_hash") != _hash_password(username.strip(), password):
        return None
    return user


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
    }


def make_token(username: str) -> str:
    raw = hashlib.sha256(f"{SECRET}|token|{username}".encode("utf-8")).hexdigest()
    return f"{raw}:{username}"


def parse_token(token: str):
    if not token or ":" not in token:
        return None
    sig, username = token.split(":", 1)
    expected = hashlib.sha256(f"{SECRET}|token|{username}".encode("utf-8")).hexdigest()
    if sig != expected:
        return None
    return find_user(username)


def admin_count(users: list | None = None) -> int:
    users = users if users is not None else load_users()
    return sum(1 for u in users if u.get("role") == "admin")


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.match((username or "").strip()))


def extend_user(user: dict, days: int) -> dict:
    days = max(1, int(days))
    start = now_utc()
    current_exp = parse_iso(user.get("expires_at"))
    if current_exp and current_exp > start:
        start = current_exp
    user["expires_at"] = to_iso(start + timedelta(days=days))
    user["permanent"] = False
    return user
