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
DEFAULT_BASE_URL = "https://ba.cd/ip/extract.php"
FETCH_TIMEOUT = 30

# 下面几个不给用户填：单账号任务一分多钟就跑完，10 分钟留足余量；
# 协议固定 http；失败重试 3 次后回退直连。
PROTOCOL = "http"
MINUTE = 10
RETRIES = 3


def default_proxy() -> dict:
    return {
        "enabled": False,
        "api_key": "",
        "phone": "",
        "base_url": DEFAULT_BASE_URL,
    }


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
    data["api_key"] = str(data.get("api_key") or "").strip()
    data["phone"] = str(data.get("phone") or "").strip()
    data["base_url"] = str(data.get("base_url") or "").strip() or DEFAULT_BASE_URL
    return data


def save_proxy(payload: dict) -> dict:
    data = load_proxy()
    payload = payload or {}
    if "enabled" in payload:
        data["enabled"] = bool(payload.get("enabled"))
    # 前端回显的是打码密钥，原样提交回来要当成「没改」，否则真密钥会被星号冲掉
    key = str(payload.get("api_key") or "").strip()
    if key and "*" not in key:
        data["api_key"] = key
    if "phone" in payload:
        data["phone"] = re.sub(r"\D", "", str(payload.get("phone") or ""))[:20]
    if payload.get("base_url"):
        data["base_url"] = str(payload.get("base_url")).strip()
    PROXY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROXY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def mask_key(key: str) -> str:
    text = str(key or "").strip()
    if not text:
        return ""
    if len(text) <= 6:
        return "***"
    return text[:3] + "***" + text[-3:]


def public_proxy(data: dict | None = None) -> dict:
    data = data or load_proxy()
    return {
        "enabled": bool(data.get("enabled")),
        "api_key": mask_key(data.get("api_key")),
        "api_key_set": bool(data.get("api_key")),
        "phone": str(data.get("phone") or ""),
        "ready": bool(data.get("api_key") and data.get("phone")),
    }


def build_url(cfg: dict, **params) -> str:
    """按配置拼提取链接：域名和密钥来自配置，其余参数由调用方给。"""
    parts = urlparse(str(cfg.get("base_url") or DEFAULT_BASE_URL).strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in params and k != "key"]
    query.append(("key", str(cfg.get("api_key") or "")))
    query.extend((k, str(v)) for k, v in params.items() if v not in (None, ""))
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


def whitelist_ip_from_error(text: str) -> str:
    """从「请先将 x.x.x.x 加入到白名单再进行提取」里取出要加白的 IP。

    接口默认只加白「调用方来源 IP」，但品赞校验的是真正发起提取那一跳的来源，
    两者不一致时就得拿它报的这个 IP 显式加一次白。

    注意必须先解码：接口的 JSON 里中文是 \\uXXXX 转义的，直接在原始报文里搜中文永远搜不到。
    """
    raw = _failure_reason(text)
    if "白名单" not in raw:
        return ""
    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", raw)
    if not m:
        return ""
    ip = m.group(1)
    return ip if all(0 <= int(p) <= 255 for p in ip.split(".")) else ""


def add_whitelist(cfg: dict, ip: str) -> bool:
    """只加白、不提取（extract=0），给自愈重试用。"""
    if not ip:
        return False
    url = build_url(cfg, phone=cfg.get("phone"), ip=ip, whitelist=1, extract=0)
    try:
        resp = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
        data = json.loads(resp.text) if resp.text.strip().startswith("{") else {}
        wl = data.get("whitelist") if isinstance(data, dict) else None
        ok = bool(wl.get("ok")) if isinstance(wl, dict) else str(data.get("code")) == "0"
        if ok:
            logger.info("已把 %s 加入代理白名单", ip)
            return True
        why = ""
        if isinstance(wl, dict):
            why = str(wl.get("message") or wl.get("data") or wl.get("error") or "")
        logger.warning("把 %s 加白失败：%s", ip, why or _failure_reason(resp.text))
        return False
    except Exception as exc:
        logger.warning("把 %s 加白异常：%s", ip, exc)
        return False


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


def list_accounts(cfg: dict | None = None) -> list[dict]:
    """拉套餐账号列表，给设置页的下拉用。密钥不对会直接抛出，让用户看到原因。"""
    cfg = cfg or load_proxy()
    if not cfg.get("api_key"):
        raise ValueError("请先填写 API 密钥")
    resp = httpx.get(build_url(cfg, action="accounts"), timeout=FETCH_TIMEOUT, follow_redirects=True)
    try:
        data = json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        raise ValueError(f"接口没有返回 JSON（HTTP {resp.status_code}），请检查接口地址是否正确")
    if not isinstance(data, dict):
        raise ValueError(f"接口返回格式不对（HTTP {resp.status_code}）")
    if str(data.get("code")) != "0":
        raise ValueError(str(data.get("message") or f"获取账号失败（HTTP {resp.status_code}）"))
    out = []
    for item in data.get("accounts") or []:
        if not isinstance(item, dict):
            continue
        phone = str(item.get("phone") or "").strip()
        if not phone:
            continue
        out.append(
            {
                "phone": phone,
                "name": str(item.get("name") or "").strip(),
                "balance": str(item.get("balance") or "").strip(),
                "ready": bool(item.get("ready")),
            }
        )
    return out


def fetch_proxy(area: str, cfg: dict | None = None, reasons: list | None = None) -> str | None:
    """返回可直接交给 Playwright 的 server 串，失败返回 None 由调用方走直连。

    传入 reasons 时会把最后一次失败原因塞进去，方便设置页把真实原因显示给用户。
    """
    cfg = cfg or load_proxy()
    if not cfg.get("enabled") or not cfg.get("api_key") or not cfg.get("phone"):
        return None
    code = normalize_area(area)
    if not code:
        logger.debug("账号没有设置地区，跳过代理直接直连")
        return None

    url = build_url(cfg, phone=cfg["phone"], area=code, minute=MINUTE)
    healed = False
    last_reason = ""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
            hit = parse_extract(resp.text)
            if hit:
                server = f"{PROTOCOL}://{hit}"
                logger.info("已提取代理 IP %s 地区=%s 第%s次", hit, area_label(code), attempt)
                return server
            last_reason = _failure_reason(resp.text)
            logger.warning(
                "提取代理失败（第%s/%s次）地区=%s HTTP=%s %s",
                attempt, RETRIES, area_label(code), resp.status_code, last_reason,
            )
            # 报的是「某某 IP 没加白」就照它说的加一次，然后立刻重试，不必等下一轮
            if not healed:
                need = whitelist_ip_from_error(resp.text)
                if need:
                    healed = True
                    if add_whitelist(cfg, need):
                        continue
                elif "白名单" in last_reason:
                    logger.warning("接口说要加白，但没能从回复里认出 IP：%s", last_reason)
        except Exception as exc:
            last_reason = str(exc)
            logger.warning("提取代理异常（第%s/%s次）地区=%s %s", attempt, RETRIES, area_label(code), exc)
        if attempt < RETRIES:
            time.sleep(2)
    logger.error("提取代理连续 %s 次失败，本次回退直连 地区=%s %s", RETRIES, area_label(code), last_reason)
    if reasons is not None and last_reason:
        reasons.append(last_reason)
    return None
