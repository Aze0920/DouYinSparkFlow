import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USERS_FILE = ROOT / "config" / "users.json"
SECRET = "dsf-user-v1"


def _hash_password(username: str, password: str) -> str:
    return hashlib.sha256(f"{SECRET}|{username}|{password}".encode("utf-8")).hexdigest()


def _default_users() -> dict:
    return {
        "users": [
            {
                "username": "admin",
                "password_hash": _hash_password("admin", "admin"),
                "role": "admin",
            }
        ]
    }


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
    return {"username": user.get("username"), "role": user.get("role") or "user"}


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
