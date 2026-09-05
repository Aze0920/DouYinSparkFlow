"""读取抖音网页私信里的好友 / 群聊，并尽量解析火花天数。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from core.browser import get_browser
from utils.logger import setup_logger
from webui.cookie_probe import _probe_lock
from webui.qr_login import CHAT_URLS, wait_chat_access
from webui.session_store import (
    clear_browser_state,
    load_chats,
    load_state_path,
    save_chats,
    save_state,
)

logger = setup_logger("app", "DEBUG")

EXTRACT_JS = """() => {
  const queryAllDeep = (sel, root = document) => {
    const out = [];
    const walk = (node) => {
      if (!node) return;
      try { node.querySelectorAll && node.querySelectorAll(sel).forEach((el) => out.push(el)); } catch (e) {}
      let all = [];
      try { all = node.querySelectorAll ? node.querySelectorAll('*') : []; } catch (e) { return; }
      for (const el of all) {
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(root);
    return out;
  };
  const itemSels = [
    '[data-e2e="conversation-item"]',
    '[data-e2e="session-item"]',
    '.conversationConversationItemwrapper',
    '[class*="conversationItemwrapper"]',
    '[class*="sessionItem"]',
    '[class*="chatListItem"]',
  ];
  let items = [];
  for (const sel of itemSels) {
    items = queryAllDeep(sel);
    if (items.length) break;
  }
  items = items.filter((el) => items.every((other) => other === el || !other.contains(el)));
  const isSpark = (n) => {
    n = Number(n);
    return n >= 1 && n <= 3660 && !(n >= 1900 && n <= 2099);
  };
  const nodeText = (n) => String((n && (n.innerText || n.textContent)) || '').replace(/[\\s\\u200b\\u00a0]+/g, ' ').trim();
  const inPreview = (n) => !!(n && n.closest && n.closest('[class*="Desc"], [class*="Hint"], [class*="hintWrapper"], pre'));
  const titleWrap = (el) => el.querySelector(
    '.conversationConversationItemtitleWrapper, [class*="ItemtitleWrapper"], [class*="titleWrapper"]'
  );
  const streakScope = (el) => {
    const area = el.querySelector('[class*="rowArea2"]') || el;
    return area;
  };
  const titleOf = (el) => {
    const exact = el.querySelector('.conversationConversationItemtitle');
    if (exact) return nodeText(exact);
    const wrap = titleWrap(el);
    const nodes = Array.from((wrap || el).querySelectorAll('[class*="Itemtitle"]'));
    const hit = nodes.find((n) => !/wrapper/i.test(String(n.className || '')));
    return nodeText(hit);
  };
  const looksTime = (raw) => /\\d{1,2}:\\d{2}|\\d+\\s*(秒前|分钟前|小时前|天前)|昨天|前天/.test(String(raw || ''));
  const cleanNum = (raw) => {
    const t = String(raw || '').replace(/[\\s\\u200b\\u00a0]+/g, ' ').trim();
    if (!t || /点燃中/.test(t) || /\\d+\\s*\\/\\s*\\d+/.test(t) || looksTime(t)) return null;
    if (/^\\d{1,4}$/.test(t) && isSpark(Number(t))) return Number(t);
    const m = t.match(/^火花\\s*[xX×]?\\s*(\\d{1,4})\\s*天?$/) || t.match(/^(\\d{1,4})\\s*天$/);
    if (m && isSpark(Number(m[1]))) return Number(m[1]);
    return null;
  };
  const isFlameNode = (node) => {
    if (!node || inPreview(node)) return false;
    const src = String(node.currentSrc || node.src || node.getAttribute('src') || '');
    const cls = String(node.className || '');
    let bg = '';
    try { bg = (node.nodeType === 1 && getComputedStyle(node).backgroundImage) || ''; } catch (e) { bg = ''; }
    return /flame_icon|commonStreakicon|Streakicon|huohua/i.test(src + ' ' + cls + ' ' + bg);
  };
  const fromStreakText = (root) => {
    if (!root) return null;
    const sels = [
      '.commonStreaknormalText',
      '[class*="StreaknormalText"]',
      '[class*="commonStreak"] [class*="Text"]',
      '[class*="streakContainer"]',
      '[class*="StreakstreakContainer"]',
    ];
    for (const sel of sels) {
      for (const n of root.querySelectorAll(sel)) {
        if (inPreview(n) || looksTime(nodeText(n))) continue;
        const got = cleanNum(nodeText(n));
        if (got) return got;
      }
    }
    return null;
  };
  const numBeside = (flame) => {
    let sib = flame.nextElementSibling;
    while (sib) {
      if (!inPreview(sib)) {
        const got = cleanNum(nodeText(sib));
        if (got) return got;
      }
      sib = sib.nextElementSibling;
    }
    const box = flame.closest('.commonStreakstreakContainer, [class*="streakContainer"], [class*="commonStreak"], [class*="TagNextToTitleleft"]') || flame.parentElement;
    if (!box || inPreview(box)) return null;
    const exact = fromStreakText(box);
    if (exact) return exact;
    const self = cleanNum(nodeText(box));
    if (self) return self;
    for (const n of box.querySelectorAll('span, div, em, b, strong')) {
      if (inPreview(n) || looksTime(nodeText(n))) continue;
      const got = cleanNum(nodeText(n));
      if (got) return got;
    }
    return null;
  };
  const fromFlame = (root) => {
    const flames = Array.from(root.querySelectorAll('img, svg, [class*="Streakicon"], [class*="streak"], [class*="Streak"]')).filter(isFlameNode);
    for (const flame of flames) {
      const got = numBeside(flame);
      if (got) return got;
    }
    return null;
  };
  const fromTitleLeft = (root) => {
    for (const n of root.querySelectorAll('[class*="TagNextToTitleleft"], [class*="TagNextToTitlewrapper"]')) {
      if (inPreview(n)) continue;
      const parts = [];
      const walk = (node) => {
        if (!node) return;
        if (node.nodeType === 1) {
          const cls = String(node.className || '');
          if (/timeStr|TimeStr|Desc|Hint|Itemtitle/i.test(cls)) return;
          if (inPreview(node)) return;
        }
        if (node.nodeType === 3) {
          const t = String(node.textContent || '').trim();
          if (t && !looksTime(t)) parts.push(t);
          return;
        }
        for (const c of node.childNodes || []) walk(c);
      };
      walk(n);
      for (const p of parts) {
        const got = cleanNum(p);
        if (got) return got;
      }
    }
    return null;
  };
  const readStreak = (el) => {
    const root = streakScope(el);
    if (/点燃中/.test(nodeText(titleWrap(el) || root))) return null;
    const fromText = fromStreakText(root);
    if (fromText) return fromText;
    const flame = fromFlame(root);
    if (flame) return flame;
    return fromTitleLeft(root);
  };
  const hasSparkWidget = (el) => {
    const root = streakScope(el);
    if (fromStreakText(root)) return true;
    if (root.querySelector('img.commonStreakicon, img[src*="flame_icon"], [class*="commonStreakicon"], [class*="Streakicon"], [class*="streakContainer"]')) return true;
    return Array.from(root.querySelectorAll('img, svg, *[style]')).some(isFlameNode);
  };
  return items.map((el) => {
    const name = titleOf(el).split('\\n').map((x) => x.trim()).filter(Boolean)[0] || '';
    const spark = readStreak(el);
    const img = el.querySelector('.commonIMAvataravatarContainer img, .semi-avatar img, img[src*="aweme-avatar"], img[src*="douyinpic.com"]');
    const avatar = img ? String(img.currentSrc || img.src || img.getAttribute('src') || '').trim() : '';
    const isGroup = /群/.test(name);
    return { name, kind: isGroup ? 'group' : 'friend', spark_days: spark, avatar, has_flame: hasSparkWidget(el) };
  }).filter((row) => row.name);
}"""

EXTRACT_SELF_AVATAR_JS = """() => {
  const imgs = Array.from(document.querySelectorAll('img[src*="aweme-avatar"], img[src*="douyinpic.com/img/"]'));
  for (const img of imgs) {
    if (img.closest('[data-e2e="conversation-item"], [class*="conversationItemwrapper"]')) continue;
    const src = String(img.currentSrc || img.src || img.getAttribute('src') || '').trim();
    if (/^https?:\\/\\//i.test(src) && !/^data:/i.test(src)) return src;
  }
  return '';
}"""

SCROLL_JS = """() => {
  const queryDeep = (sel) => {
    const walk = (node) => {
      if (!node) return null;
      try { const hit = node.querySelector && node.querySelector(sel); if (hit) return hit; } catch (e) {}
      let all = [];
      try { all = node.querySelectorAll ? node.querySelectorAll('*') : []; } catch (e) { return null; }
      for (const el of all) {
        if (el.shadowRoot) {
          const inner = walk(el.shadowRoot);
          if (inner) return inner;
        }
      }
      return null;
    };
    return walk(document);
  };
  const item = queryDeep('[data-e2e="conversation-item"], [class*="conversationItemwrapper"], [class*="sessionItem"]');
  let box = queryDeep('.conversationConversationListwrapper, [class*="conversationListwrapper"], [class*="ConversationListwrapper"], [class*="session-list"]');
  if (!box && item) {
    let p = item.parentElement;
    while (p) {
      const s = getComputedStyle(p);
      if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && p.scrollHeight > p.clientHeight + 8) {
        box = p;
        break;
      }
      p = p.parentElement;
    }
  }
  if (!box) return false;
  const before = box.scrollTop;
  box.scrollTop += 360;
  return box.scrollTop !== before;
}"""

SPARK_KEY_RE = re.compile(
    r"^(streak|spark_days|spark_count|streak_count|huohua)$",
    re.I,
)
SKIP_SPARK_KEY_RE = re.compile(r"unread|red_dot|mention|badge_count", re.I)
SPARK_TEXT_RES = [
    re.compile(r"火花\s*[xX×]?\s*(\d{1,4})"),
    re.compile(r"连续(?:互发|互相关心|关心)?\s*(\d{1,4})\s*天"),
    re.compile(r"(\d{1,4})\s*天(?:火花|连续)"),
]
DATE_RE = re.compile(
    r"(?:19|20)\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?"
    r"|(?:19|20)\d{2}年"
)
YEAR_TOKEN_RE = re.compile(r"(?:(?<=\s)|(?<=^))(?:19|20)\d{2}(?=\s|$)")
TIME_RE = re.compile(
    r"\d{1,2}:\d{2}|\d+\s*(?:秒前|分钟前|小时前|天前)|昨天|前天|周一|周二|周三|周四|周五|周六|周日"
)
IGNITE_RE = re.compile(r"点燃中\s*\d+\s*/\s*\d+")


def is_plausible_spark(n: Any) -> bool:
    try:
        days = int(n)
    except (TypeError, ValueError):
        return False
    if days < 1 or days > 3660:
        return False
    if 1900 <= days <= 2099:
        return False
    return True


def clean_avatar_url(url: str) -> str:
    raw = str(url or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    if not re.match(r"^https?://", raw, re.I):
        return ""
    if re.search(r"(javascript:|data:|blob:)", raw, re.I):
        return ""
    return raw[:800]


def parse_spark_days(text: str) -> int | None:
    raw = re.sub(r"\d+\s*天前", "", str(text or ""))
    raw = re.sub(r"\d+\s*小时前", "", raw)
    raw = IGNITE_RE.sub(" ", raw)
    raw = DATE_RE.sub(" ", raw)
    for pat in SPARK_TEXT_RES:
        found = pat.search(raw)
        if found:
            try:
                n = int(found.group(1))
            except (TypeError, ValueError):
                continue
            if is_plausible_spark(n):
                return n
    return None


def parse_spark_near_name(name: str, text: str) -> int | None:
    found = parse_spark_days(text)
    if found:
        return found
    raw = IGNITE_RE.sub(" ", str(text or ""))
    raw = DATE_RE.sub(" ", raw)
    raw = YEAR_TOKEN_RE.sub(" ", raw)
    raw = TIME_RE.sub(" ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    label = str(name or "").strip()
    if label and raw.startswith(label):
        rest = raw[len(label) :].strip()
        matched = re.match(r"(\d{1,4})\b", rest)
        if matched:
            n = int(matched.group(1))
            if is_plausible_spark(n):
                return n
    parts = [p for p in re.split(r"[\s]+", raw) if p]
    if label and label in parts:
        index = parts.index(label)
        nxt = parts[index + 1] if index + 1 < len(parts) else ""
        if re.fullmatch(r"\d{1,4}", nxt or ""):
            n = int(nxt)
            if is_plausible_spark(n):
                return n
    return None


def strip_spark_suffix(name: str, spark: int | None) -> str:
    cleaned = str(name or "").strip()
    if not spark or not cleaned:
        return cleaned
    suffix = str(spark)
    if cleaned != suffix and cleaned.endswith(suffix):
        cleaned = cleaned[: -len(suffix)].strip()
    return cleaned or str(name or "").strip()


def spark_from_streak_text(text: str) -> int | None:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw or "点燃中" in raw or re.search(r"\d+\s*/\s*\d+", raw):
        return None
    if re.fullmatch(r"\d{1,4}", raw) and is_plausible_spark(int(raw)):
        return int(raw)
    return None


def spark_from_title_row(name: str, text: str) -> int | None:
    """只解析名字旁边那一行（昵称 + 火焰天数 + 时间），不要用整行消息。"""
    return parse_spark_near_name(name, text)


def _title_wrapper_text(html: str) -> str:
    raw = str(html or "")
    start = re.search(r'class="[^"]*titleWrapper[^"]*"', raw, re.I)
    if not start:
        return ""
    begin = raw.find(">", start.end())
    if begin < 0:
        return ""
    tail = raw[begin + 1 :]
    desc = re.search(r'class="[^"]*Desc', tail, re.I)
    blob = tail[: desc.start()] if desc else tail
    return re.sub(r"<[^>]+>", " ", blob)


def spark_from_streak_html(html: str) -> int | None:
    raw = str(html or "")
    if "点燃中" in raw:
        return None
    if not re.search(r"flame_icon|commonStreak|Streakicon|streakContainer", raw, re.I):
        return None
    found = re.search(
        r"(?:commonStreaknormalText|StreaknormalText)[^>]*>\s*(\d{1,4})\s*<",
        raw,
        re.I,
    )
    if found:
        n = int(found.group(1))
        if is_plausible_spark(n):
            return n
    title_m = re.search(
        r'class="[^"]*conversationConversationItemtitle"[^>]*>([^<]+)',
        raw,
    )
    name = title_m.group(1).strip() if title_m else ""
    text = _title_wrapper_text(raw)
    desc = re.search(r'class="[^"]*Desc', raw, re.I)
    title_only = raw[: desc.start()] if desc else raw
    if name and text.strip():
        got = spark_from_title_row(name, text)
        if got:
            return got
    nearby = re.search(
        r"flame_icon[\s\S]{0,400}?>\s*(?:</img>)?\s*<[^>]*>\s*(\d{1,4})\s*<",
        title_only,
        re.I,
    )
    if nearby:
        n = int(nearby.group(1))
        if is_plausible_spark(n):
            return n
    return None


def _direct_spark(payload: dict) -> int | None:
    for key, value in payload.items():
        if SPARK_KEY_RE.search(str(key or "")):
            try:
                n = int(value)
            except (TypeError, ValueError):
                n = None
            if is_plausible_spark(n):
                return n
        if str(key or "") in {"streak_info", "spark_info", "ext"}:
            if isinstance(value, dict):
                nested = _direct_spark(value)
                if nested:
                    return nested
            if isinstance(value, str) and value.startswith("{") and len(value) < 8000:
                try:
                    nested = _direct_spark(json.loads(value))
                except Exception:
                    nested = None
                if nested:
                    return nested
    return None


NAME_KEYS = ("remark_name", "nick_name", "nickname", "display_name", "name")
NOISE_NAMES = {"私信", "消息", "搜索", "发起聊天", "没有消息", "陌生人消息"}


def _pick_name(payload: dict) -> str:
    for key in NAME_KEYS:
        val = str(payload.get(key) or "").strip()
        if val and val not in {"null", "undefined"}:
            return val
    return ""


def _nested_name(payload: dict) -> str:
    name = _pick_name(payload)
    if name:
        return name
    for key in ("user_info", "user", "participant", "conversation_core_info", "core_info", "owner"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            name = _pick_name(nested)
            if name:
                return name
    for key in ("participants", "users", "members"):
        people = payload.get(key)
        if isinstance(people, list) and people and isinstance(people[0], dict):
            name = _pick_name(people[0])
            if name:
                return name
    return ""


def harvest_api_conversations(payload: Any, out: list[dict] | None = None) -> list[dict]:
    rows = out if out is not None else []
    if isinstance(payload, dict):
        name = _nested_name(payload)
        ctype = payload.get("conversation_type")
        if ctype is None:
            ctype = payload.get("type")
        looks_conv = any(
            key in payload
            for key in ("conversation_short_id", "conversation_id", "conversation_type", "conversation_core_info")
        )
        looks_user = bool(_pick_name(payload)) and any(key in payload for key in ("sec_uid", "short_id"))
        spark = _direct_spark(payload) if looks_conv else None
        if name and name not in NOISE_NAMES and (looks_conv or looks_user):
            kind = "group" if str(ctype) in {"2", "3", "10"} else "friend"
            if "群" in name:
                kind = "group"
            rows.append({"name": name, "kind": kind, "spark_days": spark})
        for value in payload.values():
            harvest_api_conversations(value, rows)
    elif isinstance(payload, list):
        for item in payload:
            harvest_api_conversations(item, rows)
    return rows


def merge_conversations(*groups: list[dict]) -> list[dict]:
    by_name: dict[str, dict] = {}
    for group in groups:
        for item in group or []:
            name = str((item or {}).get("name") or "").strip()
            if not name:
                continue
            kind = "group" if (item or {}).get("kind") == "group" else "friend"
            spark = (item or {}).get("spark_days")
            try:
                spark = int(spark) if spark not in (None, "") else None
            except (TypeError, ValueError):
                spark = parse_spark_days(str(spark or ""))
            if not is_plausible_spark(spark):
                spark = None
            row = by_name.get(name) or {"name": name, "kind": kind, "spark_days": None, "avatar": ""}
            if kind == "group":
                row["kind"] = "group"
            avatar = clean_avatar_url((item or {}).get("avatar") or "")
            if avatar:
                row["avatar"] = avatar
            if spark:
                current = int(row.get("spark_days") or 0)
                if not is_plausible_spark(current) or spark > current:
                    row["spark_days"] = spark
            by_name[name] = row
    friends = [row for row in by_name.values() if row["kind"] != "group"]
    groups_out = [row for row in by_name.values() if row["kind"] == "group"]

    def rank(row: dict) -> tuple:
        spark = int(row.get("spark_days") or 0)
        return (0 if spark else 1, -spark, str(row.get("name") or ""))

    friends.sort(key=rank)
    groups_out.sort(key=rank)
    return friends + groups_out


def _is_im_url(url: str) -> bool:
    raw = str(url or "").lower()
    return any(token in raw for token in ("/im/", "imapi", "conversation", "im/user"))


def _row_kind(name: str, text: str, flag: str = "") -> str:
    if str(flag or "") == "group":
        return "group"
    blob = f"{name} {text}"
    if "群聊" in blob or "群" in name or re.search(r"\d+\s*人", text or ""):
        return "group"
    return "friend"


def _collect_dom(page) -> list[dict]:
    rows: list[dict] = []
    scopes = [page]
    try:
        scopes.extend(page.frames)
    except Exception:
        pass
    for scope in scopes:
        try:
            part = scope.evaluate(EXTRACT_JS) or []
        except Exception:
            continue
        for item in part:
            name = str((item or {}).get("name") or "").strip()
            if not name or name in NOISE_NAMES:
                continue
            spark = (item or {}).get("spark_days")
            try:
                spark = int(spark) if spark not in (None, "", 0, "0") else None
            except (TypeError, ValueError):
                spark = None
            if not is_plausible_spark(spark):
                spark = None
            kind = _row_kind(name, "", str((item or {}).get("kind") or ""))
            rows.append({
                "name": name,
                "kind": kind,
                "spark_days": spark,
                "avatar": clean_avatar_url((item or {}).get("avatar") or ""),
            })
    return rows


def update_spark_snapshot(account_id: str, rows: list[dict]) -> int:
    """把这次扫到的火花天数写回会话快照，让续火花任务顺手刷新天数。

    这里是新读数直接覆盖旧值，不能像 merge_conversations 那样取较大的：
    火花断了会从头数起，取最大值的话旧天数就永远赖着不走了。
    """
    account_id = str(account_id or "").strip()
    if not account_id:
        return 0
    fresh: dict[str, dict] = {}
    for row in rows or []:
        name = str((row or {}).get("name") or "").strip()
        days = (row or {}).get("spark_days")
        if name and is_plausible_spark(days):
            fresh[name] = {"days": int(days), "kind": (row or {}).get("kind") or "friend"}
    if not fresh:
        return 0
    cached = load_chats(account_id) or {}
    items = [row for row in (cached.get("items") or []) if isinstance(row, dict)]
    hit = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        got = fresh.get(name)
        if got:
            item["spark_days"] = got["days"]
            hit.add(name)
    for name, got in fresh.items():
        if name not in hit:
            items.append({"name": name, "kind": got["kind"], "spark_days": got["days"], "avatar": ""})
    save_chats(account_id, items, str(cached.get("self_avatar") or ""))
    return len(fresh)


def fresh_spark_days(account: dict) -> dict:
    """账号上存的天数停在选好友那天，续火花每跑一次会把新天数写进快照，所以以快照为准。

    这样账号列表一打开就是最新天数，不用先点一次「选择好友和群聊」。
    """
    saved = account.get("target_sparks") if isinstance(account.get("target_sparks"), dict) else {}
    merged = dict(saved or {})
    account_id = str(account.get("unique_id") or "").strip()
    if not account_id:
        return merged
    try:
        cached = load_chats(account_id) or {}
    except Exception:
        return merged
    for row in cached.get("items") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name and is_plausible_spark(row.get("spark_days")):
            merged[name] = int(row["spark_days"])
    return merged


def list_conversations(
    cookies: list[dict[str, Any]], unique_id: str = "", force: bool = False, region: str = ""
) -> dict[str, Any]:
    account_id = str(unique_id or "").strip()
    if account_id and not force:
        cached = load_chats(account_id)
        if cached and cached.get("items"):
            items = cached.get("items") or []
            logger.info("使用账号会话快照 unique_id=%s count=%s", account_id, len(items))
            return {
                "ok": True,
                "items": items,
                "self_avatar": str(cached.get("self_avatar") or ""),
                "from_snapshot": True,
                "message": f"已打开账号快照，{len(items)} 个会话",
            }
    if not _probe_lock.acquire(blocking=False):
        return {"ok": False, "items": [], "message": "正在检测另一个账号，请稍后再试"}
    playwright = None
    browser = None
    context = None
    lease = None
    api_rows: list[dict] = []
    try:
        from core.browser import make_context
        from core.tasks import (
            CONVERSATION_ITEM_SELECTORS,
            CONVERSATION_LIST_SELECTORS,
            _dump_chat_debug,
            _find_locator,
            _looks_like_login,
            _scroll_list,
            _wait_locator,
        )

        if str(region or "").strip():
            try:
                from webui.proxy import lease_proxy, proxy_enabled

                # 总开关关掉就当没设地区：直连读列表，不取 IP、不探活
                if proxy_enabled():
                    lease = lease_proxy(region)
            except Exception:
                logger.exception("读会话列表时提取代理失败，改走直连")
        proxy = lease.server if lease else None
        playwright, browser = get_browser()
        state = load_state_path(account_id)
        try:
            context = make_context(browser, storage_state=state, cookies=cookies, proxy=proxy)
        except Exception:
            if not proxy:
                raise
            logger.exception("用代理建上下文失败，改走直连")
            context = make_context(browser, storage_state=state, cookies=cookies)

        def on_response(response):
            url = str(getattr(response, "url", "") or "")
            if not _is_im_url(url):
                return
            try:
                data = response.json()
            except Exception:
                return
            harvest_api_conversations(data, api_rows)

        page = context.new_page()
        page.on("response", on_response)
        # 只等响应头。等 domcontentloaded 的话，私信页这么重的应用走代理常常直接超时，
        # 而超时会把整个「选好友」流程掀掉；真正要等的会话列表由 wait_chat_access 盯着。
        chat_state = "empty"
        for url in CHAT_URLS:
            try:
                page.goto(url, wait_until="commit", timeout=30000 if proxy else 20000)
            except Exception as exc:
                logger.warning("打开私信页超时（%s）url=%s", type(exc).__name__, url)
                continue
            chat_state = wait_chat_access(page, timeout_s=35 if proxy else 20)
            if chat_state in {"chat", "login", "challenge"}:
                break
        if chat_state == "login" or _looks_like_login(page):
            if account_id:
                clear_browser_state(account_id)
            _dump_chat_debug(page, "picker")
            cached = load_chats(account_id) if account_id else None
            if cached and cached.get("items"):
                items = cached.get("items") or []
                return {
                    "ok": True,
                    "items": items,
                    "self_avatar": str(cached.get("self_avatar") or ""),
                    "from_snapshot": True,
                    "message": f"网页私信要扫码，已改用上次快照（{len(items)} 个会话）。请重新扫码后再检测火花",
                }
            return {
                "ok": False,
                "items": [],
                "message": "网页私信在要求扫码。请重新扫码登录这个号",
            }
        if chat_state == "challenge":
            cached = load_chats(account_id) if account_id else None
            if cached and cached.get("items"):
                items = cached.get("items") or []
                return {
                    "ok": True,
                    "items": items,
                    "self_avatar": str(cached.get("self_avatar") or ""),
                    "from_snapshot": True,
                    "message": f"私信页在做安全验证，已改用上次快照（{len(items)} 个会话）",
                }
            return {
                "ok": False,
                "items": [],
                "message": "私信页在做安全验证，暂时读不到好友列表。账号没掉线，稍后或换条线路再试",
            }

        item_loc, scope, item_sel = _wait_locator(page, CONVERSATION_ITEM_SELECTORS, timeout_ms=8000)
        list_loc, _, list_sel = _find_locator(page, CONVERSATION_LIST_SELECTORS)
        logger.info("选择好友等待会话列表 item=%s list=%s chat=%s", item_sel or "无", list_sel or "无", chat_state)
        try:
            page.evaluate(
                """() => {
                  const el = document.querySelector('.conversationConversationListwrapper, [class*="conversationListwrapper"]');
                  if (el) el.scrollTop = 0;
                }"""
            )
        except Exception:
            pass
        try:
            page.wait_for_selector(
                ".commonStreaknormalText, img.commonStreakicon, img[src*='flame_icon']",
                timeout=5000,
            )
        except Exception:
            pass
        time.sleep(1.2)

        prev_items = []
        if account_id:
            prev = load_chats(account_id)
            prev_items = (prev or {}).get("items") or []
        # 只拿当前视口的 DOM，不要先合并旧快照。否则名单早已齐全，
        # 滚两下天数不变就会停，下面那些有火的人永远扫不到。
        seen_dom = _collect_dom(page)
        stagnant = 0
        for _ in range(28):
            # 代理到点就别再滚了，已经扫到的照常返回
            if lease and lease.expired():
                logger.warning("代理已用满 %s 分钟，会话列表滚到这儿为止", lease.minutes)
                break
            before = {(row.get("name"), row.get("spark_days")) for row in seen_dom}
            moved = False
            try:
                moved = bool(page.evaluate(SCROLL_JS))
            except Exception:
                moved = False
            if not moved:
                try:
                    moved = bool(_scroll_list(scope, list_loc, item_loc))
                except Exception:
                    moved = False
            if not moved:
                try:
                    target = None
                    if list_loc is not None:
                        target = list_loc.first
                    elif item_loc is not None:
                        target = item_loc.first
                    box = target.bounding_box() if target is not None else None
                    if box:
                        page.mouse.move(
                            box["x"] + max(48, box["width"] * 0.4),
                            box["y"] + min(220, max(40, box["height"] * 0.4)),
                        )
                        page.mouse.wheel(0, 420)
                        moved = True
                except Exception:
                    moved = False
            time.sleep(1.05)
            try:
                page.wait_for_selector(
                    ".commonStreaknormalText, img.commonStreakicon, img[src*='flame_icon']",
                    timeout=1200,
                )
            except Exception:
                pass
            item_loc, scope, _ = _find_locator(page, CONVERSATION_ITEM_SELECTORS)
            list_loc, _, _ = _find_locator(page, CONVERSATION_LIST_SELECTORS)
            seen_dom = merge_conversations(seen_dom, _collect_dom(page))
            after = {(row.get("name"), row.get("spark_days")) for row in seen_dom}
            if after == before:
                stagnant += 1
            else:
                stagnant = 0
            if stagnant >= 4:
                break
            if _looks_like_login(page):
                break

        items = merge_conversations(
            seen_dom,
            prev_items,
            [
                {
                    "name": row.get("name"),
                    "kind": row.get("kind") or "friend",
                    "spark_days": row.get("spark_days"),
                    "avatar": "",
                }
                for row in api_rows
                if row.get("name")
            ],
            _collect_dom(page),
        )
        spark_n = sum(1 for row in items if is_plausible_spark(row.get("spark_days")))
        logger.info(
            "读取会话列表 count=%s sparks=%s api=%s url=%s",
            len(items),
            spark_n,
            len(api_rows),
            getattr(page, "url", ""),
        )
        self_avatar = ""
        try:
            self_avatar = clean_avatar_url(page.evaluate(EXTRACT_SELF_AVATAR_JS) or "")
        except Exception:
            self_avatar = ""
        if not items:
            _dump_chat_debug(page, "picker")
            cached = load_chats(account_id) if account_id else None
            if cached and cached.get("items"):
                return {
                    "ok": True,
                    "items": cached.get("items") or [],
                    "self_avatar": str(cached.get("self_avatar") or self_avatar),
                    "from_snapshot": True,
                    "message": f"这次没扫到列表，已打开上次快照（{len(cached.get('items') or [])} 个会话）",
                }
            return {
                "ok": False,
                "items": [],
                "self_avatar": self_avatar,
                "message": "私信页没出现好友列表。请重新扫码登录这个号后再选好友",
            }
        if account_id:
            save_state(context, account_id)
            save_chats(account_id, items, self_avatar)
        return {"ok": True, "items": items, "self_avatar": self_avatar, "from_snapshot": False, "message": f"已读取 {len(items)} 个会话"}
    except Exception as exc:
        logger.exception("读取会话列表失败")
        return {"ok": False, "items": [], "message": f"读取失败：{exc}"}
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if playwright:
                playwright.stop()
        except Exception:
            pass
        # 浏览器全关掉才算真的不再用这条 IP
        if lease:
            lease.release(f"选好友 {account_id or '-'}")
        _probe_lock.release()
