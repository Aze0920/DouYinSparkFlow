"""按账号地区提取住宅代理 IP。

提取接口本身必须直连（不能走代理），拿到的 ip:port 才交给 Playwright。
地区码为空时一律返回 None：宁可用直连，也不能给账号配一个异地 IP —— 
登录态 IP 归属地突变在抖音风控里比机房 IP 更敏感，可能直接作废 sessionid。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from utils.logger import setup_logger
from webui.regions import area_label, normalize_area

ROOT = Path(__file__).resolve().parent.parent
PROXY_FILE = ROOT / "config" / "proxy.json"
logger = setup_logger("app", "DEBUG")

IP_PORT = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})\b")
PROTOCOLS = ("http", "socks5")
MAX_RETRIES = 5
FETCH_TIMEOUT = 30


def default_proxy() -> dict:
    return {
        "enabled": False,
        "api_url": "",
        "protocol": "http",
        "minute": 10,
        "retries": 3,
    }


def _clamp_int(value, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def load_proxy() -> dict:
    data = default_proxy()
    if PROXY_FILE.is_file():
        try:
            raw = json.loads(PROXY_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update({k: v for k, v in raw.items() if k in data})
        except Exception:
            logger.exception("读取代理配置失败")
    data["enabled"] = bool(data.get("enabled"))
    data["api_url"] = str(data.get("api_url") or "").strip()
    data["protocol"] = str(data.get("protocol") or "http").strip().lower()
    if data["protocol"] not in PROTOCOLS:
        data["protocol"] = "http"
    data["minute"] = _clamp_int(data.get("minute"), 1, 120, 10)
    data["retries"] = _clamp_int(data.get("retries"), 1, MAX_RETRIES, 3)
    return data


def save_proxy(payload: dict) -> dict:
    data = load_proxy()
    payload = payload or {}
    if "enabled" in payload:
        data["enabled"] = bool(payload.get("enabled"))
    # 前端拿到的是打码链接，留空表示不改动，避免把已存的密钥覆盖成星号
    incoming_url = str(payload.get("api_url") or "").strip()
    if incoming_url and "***" not in incoming_url:
        data["api_url"] = incoming_url
    if "protocol" in payload:
        proto = str(payload.get("protocol") or "").strip().lower()
        data["protocol"] = proto if proto in PROTOCOLS else "http"
    if "minute" in payload:
        data["minute"] = _clamp_int(payload.get("minute"), 1, 120, 10)
    if "retries" in payload:
        data["retries"] = _clamp_int(payload.get("retries"), 1, MAX_RETRIES, 3)
    PROXY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROXY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def mask_url(url: str) -> str:
    """把链接里的 key / phone 打码后再回前端，密钥不必往浏览器送第二趟。"""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlparse(text)
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in {"key", "phone", "secret", "pwd"} and value:
                value = value[:3] + "***" + value[-2:] if len(value) > 6 else "***"
            query.append((key, value))
        # safe="*" 不能省：默认会把星号转义成 %2A，save_proxy 就认不出这是打码链接了
        return urlunparse(parts._replace(query=urlencode(query, safe="*")))
    except Exception:
        return "***"


def public_proxy(data: dict | None = None) -> dict:
    data = data or load_proxy()
    out = dict(data)
    out["api_url_set"] = bool(data.get("api_url"))
    out["api_url"] = mask_url(data.get("api_url"))
    return out


def build_url(api_url: str, area: str, minute: int) -> str:
    """在用户配的链接上覆盖 area / minute，其余参数（key、phone 等）原样保留。"""
    parts = urlparse(str(api_url or "").strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in {"area", "minute", "province", "city"}]
    query.append(("area", area))
    query.append(("minute", str(minute)))
    return urlunparse(parts._replace(query=urlencode(query)))


def parse_extract(text: str) -> str:
    """接口正常返回 JSON，但也兼容直接配 core-extract 链接时的纯文本 ip:port。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        code = data.get("code")
        if code is not None and str(code) != "0":
            return ""
        extract = data.get("extract")
        if isinstance(extract, dict):
            if extract.get("ok") is False:
                return ""
            raw = str(extract.get("data") or extract.get("raw") or "")
        else:
            raw = str(data.get("data") or "")
    m = IP_PORT.search(raw)
    if not m:
        return ""
    port = int(m.group(2))
    if not 1 <= port <= 65535:
        return ""
    return f"{m.group(1)}:{port}"


def _failure_reason(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw[:160]
        extract = data.get("extract")
        if isinstance(extract, dict) and extract.get("data"):
            return str(extract.get("data"))[:160]
        return str(data.get("message") or raw)[:160]
    return raw[:160]


def fetch_proxy(area: str, cfg: dict | None = None) -> str | None:
    """返回可直接交给 Playwright 的 server 串，失败返回 None 由调用方走直连。"""
    cfg = cfg or load_proxy()
    if not cfg.get("enabled") or not cfg.get("api_url"):
        return None
    code = normalize_area(area)
    if not code:
        logger.debug("账号没有设置地区，跳过代理直接直连")
        return None

    url = build_url(cfg["api_url"], code, cfg["minute"])
    retries = _clamp_int(cfg.get("retries"), 1, MAX_RETRIES, 3)
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
            hit = parse_extract(resp.text)
            if hit:
                server = f"{cfg['protocol']}://{hit}"
                logger.info("已提取代理 IP %s 地区=%s 第%s次", hit, area_label(code), attempt)
                return server
            logger.warning(
                "提取代理失败（第%s/%s次）地区=%s HTTP=%s %s",
                attempt, retries, area_label(code), resp.status_code, _failure_reason(resp.text),
            )
        except Exception as exc:
            logger.warning("提取代理异常（第%s/%s次）地区=%s %s", attempt, retries, area_label(code), exc)
        if attempt < retries:
            time.sleep(2)
    logger.error("提取代理连续 %s 次失败，本次回退直连 地区=%s", retries, area_label(code))
    return None
