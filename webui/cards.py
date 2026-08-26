import json
import secrets
from pathlib import Path

from webui.users import now_utc, parse_days, parse_iso, parse_max_accounts, to_iso

ROOT = Path(__file__).resolve().parent.parent
CARDS_FILE = ROOT / "config" / "cards.json"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def normalize_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum() or ch == "-")


def _new_code() -> str:
    parts = ["".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3)]
    return "DSF-" + "-".join(parts)


def load_cards() -> list:
    CARDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CARDS_FILE.is_file():
        save_cards([])
        return []
    try:
        data = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
        cards = data.get("cards") if isinstance(data, dict) else data
        return list(cards or [])
    except Exception:
        return []


def save_cards(cards: list) -> None:
    CARDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CARDS_FILE.write_text(json.dumps({"cards": cards}, ensure_ascii=False, indent=2), encoding="utf-8")


def _clamp_days(value) -> int:
    return parse_days(value, default=1)


def _clamp_accounts(value) -> int:
    return parse_max_accounts(value, default=1)


def _clamp_count(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, 50))


def public_card(card: dict) -> dict:
    created = parse_iso(card.get("created_at"))
    used = parse_iso(card.get("used_at"))
    used_by = str(card.get("used_by") or "").strip()
    days_n = _clamp_days(card.get("days"))
    accounts_n = _clamp_accounts(card.get("max_accounts"))
    return {
        "code": card.get("code") or "",
        "days": days_n,
        "max_accounts": accounts_n,
        "days_label": "不限" if days_n == 0 else f"{days_n} 天",
        "max_accounts_label": "账号不限" if accounts_n == 0 else f"{accounts_n} 个账号",
        "note": str(card.get("note") or "").strip(),
        "created_at": card.get("created_at") or "",
        "used_at": card.get("used_at") or "",
        "used_by": used_by,
        "used": bool(used_by),
        "created_label": created.astimezone().strftime("%Y-%m-%d %H:%M") if created else "-",
        "used_label": used.astimezone().strftime("%Y-%m-%d %H:%M") if used else "",
        "status_label": f"已用 · {used_by}" if used_by else "未使用",
    }


def create_cards(days=1, max_accounts=1, count=1, note: str = "") -> list:
    days_n = _clamp_days(days)
    accounts_n = _clamp_accounts(max_accounts)
    count_n = _clamp_count(count)
    note_text = str(note or "").strip()[:80]
    cards = load_cards()
    existing = {normalize_code(item.get("code")) for item in cards}
    created = []
    for _ in range(count_n):
        code = _new_code()
        while normalize_code(code) in existing:
            code = _new_code()
        existing.add(normalize_code(code))
        row = {
            "code": code,
            "days": days_n,
            "max_accounts": accounts_n,
            "note": note_text,
            "created_at": to_iso(now_utc()),
            "used_at": "",
            "used_by": "",
        }
        cards.append(row)
        created.append(row)
    save_cards(cards)
    return created


def consume_card(code: str, username: str) -> dict:
    key = normalize_code(code)
    if not key:
        raise ValueError("请输入卡密")
    username = str(username or "").strip()
    if not username:
        raise ValueError("缺少用户名")
    cards = load_cards()
    for item in cards:
        if normalize_code(item.get("code")) != key:
            continue
        if str(item.get("used_by") or "").strip():
            raise ValueError("这张卡密已经被使用")
        item["used_at"] = to_iso(now_utc())
        item["used_by"] = username
        save_cards(cards)
        return item
    raise ValueError("卡密无效")


def delete_card(code: str) -> dict:
    key = normalize_code(code)
    cards = load_cards()
    target = next((item for item in cards if normalize_code(item.get("code")) == key), None)
    if not target:
        raise ValueError("卡密不存在")
    if str(target.get("used_by") or "").strip():
        raise ValueError("已使用的卡密不能删除")
    cards = [item for item in cards if normalize_code(item.get("code")) != key]
    save_cards(cards)
    return target


def list_public_cards() -> list:
    cards = [public_card(item) for item in load_cards()]
    unused = sorted(
        [item for item in cards if not item["used"]],
        key=lambda item: item["created_at"],
        reverse=True,
    )
    used = sorted(
        [item for item in cards if item["used"]],
        key=lambda item: item["used_at"] or item["created_at"],
        reverse=True,
    )
    return unused + used
