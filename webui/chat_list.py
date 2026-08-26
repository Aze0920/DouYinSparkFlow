"""读取抖音网页私信里的好友 / 群聊，并尽量解析火花天数。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from core.browser import get_browser
from utils.logger import setup_logger
from webui.cookie_probe import _probe_lock
from webui.qr_login import CHAT

logger = setup_logger("app", "DEBUG")

EXTRACT_JS = """() => {
  const itemSels = [
    '[data-e2e="conversation-item"]',
    '.conversationConversationItemwrapper',
    '[class*="conversationItemwrapper"]',
  ];
  let items = [];
  for (const sel of itemSels) {
    items = Array.from(document.querySelectorAll(sel));
    if (items.length) break;
  }
  items = items.filter((el) => items.every((other) => other === el || !other.contains(el)));
  const isSpark = (n) => {
    n = Number(n);
    return n >= 1 && n <= 3660 && !(n >= 1900 && n <= 2099);
  };
  const nodeText = (n) => String((n && (n.innerText || n.textContent)) || '').replace(/[\\s\\u200b\\u00a0]+/g, ' ').trim();
  const titleWrap = (el) => el.querySelector(
    '.conversationConversationItemtitleWrapper, [class*="ItemtitleWrapper"], [class*="titleWrapper"], [class*="TitleWrapper"]'
  );
  const titleOf = (el) => {
    const exact = el.querySelector('.conversationConversationItemtitle');
    if (exact) return nodeText(exact);
    const wrap = titleWrap(el);
    const nodes = Array.from((wrap || el).querySelectorAll('[class*="Itemtitle"], [class*="item-title"]'));
    const hit = nodes.find((n) => !/wrapper/i.test(String(n.className || '')));
    return nodeText(hit);
  };
  const isTimeNode = (n) => !!(n && n.closest && n.closest('[class*="timeStr"], [class*="TimeStr"], [class*="time-str"]'));
  const cleanNum = (raw) => {
    const t = String(raw || '').replace(/[\\s\\u200b\\u00a0]+/g, ' ').trim();
    if (!t || /点燃中/.test(t) || /\\d+\\s*\\/\\s*\\d+/.test(t)) return null;
    if (/^\\d{1,4}$/.test(t) && isSpark(Number(t))) return Number(t);
    return null;
  };
  const stripTime = (line) => String(line || '')
    .replace(/\\d{1,2}:\\d{2}/g, ' ')
    .replace(/\\d+\\s*(秒前|分钟前|小时前|天前)/g, ' ')
    .replace(/昨天|前天/g, ' ')
    .replace(/\\s+/g, ' ')
    .trim();
  const sparkAfterName = (name, line) => {
    if (!name || !line) return null;
    const idx = line.indexOf(name);
    if (idx < 0) return null;
    const rest = line.slice(idx + name.length).trim();
    const m = rest.match(/^(\\d{1,4})\\b/);
    if (m && isSpark(Number(m[1]))) return Number(m[1]);
    return null;
  };
  const looksFlame = (node) => {
    if (!node) return false;
    const src = String(node.currentSrc || node.src || node.getAttribute('src') || '');
    const cls = String(node.className || '');
    const style = String(node.getAttribute('style') || '');
    let bg = '';
    try { bg = (node.nodeType === 1 && getComputedStyle(node).backgroundImage) || ''; } catch (e) { bg = ''; }
    return /flame_icon|flame|streak|huohua|spark|fire/i.test(src + ' ' + cls + ' ' + style + ' ' + bg);
  };
  const fromFlame = (root) => {
    const icons = Array.from(root.querySelectorAll('img, svg, [class*="Streakicon"], [class*="streak"]'));
    for (const icon of icons) {
      if (!looksFlame(icon) && icon.tagName !== 'IMG') continue;
      if (!looksFlame(icon) && icon.tagName === 'IMG') {
        if (!/flame_icon|flame|streak|huohua|spark|fire/i.test(String(icon.currentSrc || icon.src || ''))) continue;
      }
      let sib = icon.nextElementSibling;
      while (sib) {
        if (!isTimeNode(sib)) {
          const got = cleanNum(nodeText(sib));
          if (got) return got;
        }
        sib = sib.nextElementSibling;
      }
      const parent = icon.parentElement;
      if (parent && !isTimeNode(parent)) {
        const got = cleanNum(nodeText(parent));
        if (got) return got;
        const line = stripTime(nodeText(parent));
        const m = line.match(/(?:^|\\s)(\\d{1,4})(?:\\s|$)/);
        if (m && isSpark(Number(m[1]))) return Number(m[1]);
      }
    }
    return null;
  };
  const fromColoredNum = (root) => {
    const nodes = Array.from(root.querySelectorAll('*'));
    for (const n of nodes) {
      if (isTimeNode(n)) continue;
      const got = cleanNum(nodeText(n));
      if (!got) continue;
      let color = '';
      try { color = getComputedStyle(n).color || ''; } catch (e) { color = ''; }
      const m = color.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
      if (!m) continue;
      const r = Number(m[1]), g = Number(m[2]), b = Number(m[3]);
      if (r >= 180 && g <= 190 && b <= 140) return got;
    }
    return null;
  };
  const fromTextNodes = (root) => {
    if (!root) return null;
    const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = it.nextNode())) {
      if (isTimeNode(node.parentElement)) continue;
      const got = cleanNum(node.nodeValue);
      if (got) return got;
    }
    return null;
  };
  const readStreak = (el, name) => {
    const wrap = titleWrap(el) || el;
    if (/点燃中/.test(nodeText(wrap))) return null;
    for (const n of wrap.querySelectorAll('.commonStreaknormalText, [class*="StreaknormalText"], [class*="StreakText"], [class*="streakText"], [class*="Streak"]')) {
      if (isTimeNode(n)) continue;
      const got = cleanNum(nodeText(n));
      if (got) return got;
    }
    for (const n of wrap.querySelectorAll('.commonStreakstreakContainer, [class*="streakContainer"], [class*="Streak"]')) {
      if (n.querySelector('[class*="timeStr"], [class*="TimeStr"]')) continue;
      const got = cleanNum(nodeText(n));
      if (got) return got;
    }
    const flame = fromFlame(wrap);
    if (flame) return flame;
    for (const n of wrap.querySelectorAll('[class*="TagNextToTitleleft"]')) {
      if (n.querySelector('[class*="timeStr"], [class*="TimeStr"]')) continue;
      const got = cleanNum(nodeText(n));
      if (got) return got;
    }
    const colored = fromColoredNum(wrap);
    if (colored) return colored;
    const walked = fromTextNodes(wrap);
    if (walked) return walked;
    return sparkAfterName(name, stripTime(nodeText(wrap)));
  };
  return items.map((el) => {
    const name = titleOf(el).split('\\n').map((x) => x.trim()).filter(Boolean)[0] || '';
    const wrap = titleWrap(el);
    const title_row = wrap ? nodeText(wrap) : '';
    const spark = readStreak(el, name);
    const img = el.querySelector('.commonIMAvataravatarContainer img, .semi-avatar img, img[src*="aweme-avatar"], img[src*="douyinpic.com"]');
    const avatar = img ? String(img.currentSrc || img.src || img.getAttribute('src') || '').trim() : '';
    const isGroup = /群/.test(name);
    return { name, kind: isGroup ? 'group' : 'friend', spark_days: spark, avatar, title_row };
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
  const el = document.querySelector('.conversationConversationListwrapper, [class*="conversationListwrapper"], [class*="ConversationListwrapper"]');
  let box = el;
  if (!box) {
    const item = document.querySelector('[class*="conversationItemwrapper"], [class*="ConversationItemwrapper"]');
    box = item && item.parentElement;
  }
  if (!box) return false;
  const before = box.scrollTop;
  box.scrollTop += 720;
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
    if name and text.strip():
        return spark_from_title_row(name, text)
    nearby = re.search(
        r"flame_icon[\s\S]{0,400}?>\s*(?:</img>)?\s*<[^>]*>\s*(\d{1,4})\s*<",
        raw,
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
            title_row = str((item or {}).get("title_row") or "")
            spark = (item or {}).get("spark_days")
            try:
                spark = int(spark) if spark not in (None, "", 0, "0") else None
            except (TypeError, ValueError):
                spark = None
            if not is_plausible_spark(spark):
                spark = spark_from_title_row(name, title_row)
            if not is_plausible_spark(spark):
                spark = None
            kind = _row_kind(name, title_row, str((item or {}).get("kind") or ""))
            rows.append({
                "name": name,
                "kind": kind,
                "spark_days": spark,
                "avatar": clean_avatar_url((item or {}).get("avatar") or ""),
            })
    return rows


def list_conversations(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    if not _probe_lock.acquire(blocking=False):
        return {"ok": False, "items": [], "message": "正在检测另一个账号，请稍后再试"}
    playwright = None
    browser = None
    context = None
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

        playwright, browser = get_browser()
        context = make_context(browser)
        try:
            context.add_cookies(cookies)
        except Exception:
            logger.exception("写入 Cookie 失败")
            return {"ok": False, "items": [], "message": "Cookie 格式浏览器不接受，请重新登录"}

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
        page.goto(CHAT, wait_until="domcontentloaded", timeout=25000)
        time.sleep(0.8)
        if _looks_like_login(page):
            _dump_chat_debug(page, "picker")
            return {"ok": False, "items": [], "message": "Cookie 已失效，请重新登录后再选好友"}

        item_loc, scope, item_sel = _wait_locator(page, CONVERSATION_ITEM_SELECTORS, timeout_ms=15000)
        list_loc, _, list_sel = _find_locator(page, CONVERSATION_LIST_SELECTORS)
        logger.info("选择好友等待会话列表 item=%s list=%s", item_sel or "无", list_sel or "无")
        try:
            page.wait_for_selector(
                ".commonStreaknormalText, img.commonStreakicon, img[src*='flame_icon'], [class*='titleWrapper']",
                timeout=8000,
            )
        except Exception:
            pass
        time.sleep(1.2)

        items: list[dict] = []
        for _ in range(14):
            api_names = [
                {"name": row.get("name"), "kind": row.get("kind") or "friend", "spark_days": None, "avatar": ""}
                for row in api_rows
                if row.get("name")
            ]
            items = merge_conversations(items, api_names, _collect_dom(page))
            moved = False
            try:
                moved = bool(_scroll_list(scope, list_loc, item_loc))
            except Exception:
                try:
                    moved = bool(page.evaluate(SCROLL_JS))
                except Exception:
                    moved = False
            item_loc, scope, _ = _find_locator(page, CONVERSATION_ITEM_SELECTORS)
            list_loc, _, _ = _find_locator(page, CONVERSATION_LIST_SELECTORS)
            if items and not moved:
                break
            time.sleep(0.55)

        items = merge_conversations(
            items,
            [
                {"name": row.get("name"), "kind": row.get("kind") or "friend", "spark_days": None, "avatar": ""}
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
            return {
                "ok": False,
                "items": [],
                "self_avatar": self_avatar,
                "message": "私信页没出现好友列表。Cookie 检测过首页不等于网页私信能打开，请确认这个号能打开抖音网页私信",
            }
        return {"ok": True, "items": items, "self_avatar": self_avatar, "message": f"已读取 {len(items)} 个会话"}
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
        _probe_lock.release()
