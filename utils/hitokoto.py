import random
import time

import requests

from utils.config import get_config

hitokotoApi = "https://v1.hitokoto.cn/"

allHitokotoTypes = {
    "动画": "a",
    "漫画": "b",
    "游戏": "c",
    "文学": "d",
    "原创": "e",
    "来自网络": "f",
    "其他": "g",
    "影视": "h",
    "诗词": "i",
    "哲学": "k",
    "抖机灵": "l",
}

LOCAL_QUOTES = {
    "文学": (
        "生活不可能像你想象得那么好，但也不会像你想象得那么糟。—— 莫泊桑",
        "人是会思想的苇草。—— 帕斯卡尔",
        "我们听过无数的道理，却仍过不好这一生。—— 韩寒",
    ),
    "影视": (
        "生活就像一盒巧克力，你永远不知道下一颗是什么。—— 阿甘正传",
        "今天的风儿有些喧嚣。—— 千与千寻",
        "不要温和地走进那个良夜。—— 星际穿越",
    ),
    "诗词": (
        "海内存知己，天涯若比邻。—— 王勃",
        "会当凌绝顶，一览众山小。—— 杜甫",
        "山重水复疑无路，柳暗花明又一村。—— 陆游",
    ),
    "哲学": (
        "认识你自己。—— 苏格拉底",
        "我思故我在。—— 笛卡尔",
        "未经审视的人生不值得过。—— 苏格拉底",
    ),
    "动画": (
        "只要心里还存着那么一丝希望，就一定能找到拯救自己的道路。—— 千与千寻",
        "比起没有意义的长生，我更想要闪耀的瞬间。—— 进击的巨人",
    ),
    "漫画": (
        "我要成为海贼王！—— 海贼王",
        "人们的梦想是不会结束的。—— 海贼王",
    ),
    "游戏": (
        "愿风神护佑你。—— 原神",
        "战争从未改变。—— 辐射",
    ),
    "原创": (
        "今日火花，轻轻续上。",
        "把今天的温度留给明天的自己。",
    ),
    "来自网络": (
        "慢慢来，也比较快。",
        "一切都会好的，若不够好，说明还没到结局。",
    ),
    "其他": (
        "愿你今晚好梦，明日有光。",
        "把想说的话，说给在意的人听。",
    ),
    "抖机灵": (
        "火花续上了，烦恼先放一放。",
        "今天也要元气满满地划水。",
    ),
}

_REMOTE_CACHE = {"text": "", "ts": 0.0}
_REMOTE_TTL = 600


def _selected_types():
    config = get_config()
    raw = config.get("hitokotoTypes") or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]
    return [str(item) for item in raw if str(item) in allHitokotoTypes]


def _format_hitokoto(data: dict) -> str:
    text = str(data.get("hitokoto") or "").strip()
    if not text:
        return ""
    the_from = str(data.get("from") or "").strip() or "未知来源"
    the_from_who = str(data.get("from_who") or "").strip() or "未知作者"
    return f"{text} —— {the_from} ({the_from_who})"


def _fetch_hitokoto() -> str:
    api_url = hitokotoApi
    for name in _selected_types():
        code = allHitokotoTypes[name]
        api_url += ("&" if "?" in api_url else "?") + f"c={code}"
    try:
        response = requests.get(api_url, timeout=2.5)
        response.raise_for_status()
        return _format_hitokoto(response.json())
    except Exception:
        return ""


def _fetch_jinrishici() -> str:
    try:
        response = requests.get("https://v1.jinrishici.com/all.json", timeout=2.5)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    content = str(data.get("content") or "").strip()
    if not content:
        return ""
    origin = data.get("origin") if isinstance(data.get("origin"), dict) else {}
    title = str(origin.get("title") or data.get("origin") or "诗词").strip() or "诗词"
    author = str(origin.get("author") or data.get("author") or "").strip() or "未知作者"
    return f"{content} —— {title} ({author})"


def _local_fallback() -> str:
    types = _selected_types()
    pool = []
    for name in types:
        pool.extend(LOCAL_QUOTES.get(name, ()))
    if not pool:
        pool = [quote for quotes in LOCAL_QUOTES.values() for quote in quotes]
    return random.choice(pool)


def request_hitokoto():
    """拿一句一言。外网 API 不通时用本地句子，绝不把 [error] 发进聊天。"""
    now = time.time()
    cached = str(_REMOTE_CACHE.get("text") or "").strip()
    if cached and now - float(_REMOTE_CACHE.get("ts") or 0) < _REMOTE_TTL:
        return cached

    text = _fetch_hitokoto() or _fetch_jinrishici()
    if text:
        _REMOTE_CACHE["text"] = text
        _REMOTE_CACHE["ts"] = now
        return text
    return _local_fallback()
