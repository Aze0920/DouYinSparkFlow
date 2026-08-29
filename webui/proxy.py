"""按账号地区提取住宅代理 IP。

提取接口本身必须直连（不能走代理），拿到的 ip:port 才交给 Playwright。
地区码为空时一律返回 None：宁可用直连，也不能给账号配一个异地 IP —— 
登录态 IP 归属地突变在抖音风控里比机房 IP 更敏感，可能直接作废 sessionid。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from utils.logger import setup_logger
from webui import safe_io
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
# 提前这么多秒收手。IP 是掐着点失效的，等真到点再停，最后那几步已经在断网里跑了。
LEASE_GRACE = 40
# 探活：打抖音自己的地址才有意义（能连通用网站、访问抖音却超时是常事）。
# 但只能走 http：https 要多一次 TLS 握手，这台机器直连抖音握手就要好几秒，
# 用 https 探活会把好 IP 全判死。这里只要证明「这条 IP 转得到抖音」就够了。
PROBE_URL = "http://www.douyin.com/favicon.ico"
PROBE_TIMEOUT = 10

# 多账号是并发跑的，会同一秒一起来提取，供应商那边按调用频率直接拒。
# 串着来、两次之间留点间隔，比一起挤反而更快都拿到 IP。
_extract_lock = threading.Lock()
_EXTRACT_GAP = 1.5
_last_extract = 0.0


def _throttled_get(url: str):
    global _last_extract
    with _extract_lock:
        wait = _EXTRACT_GAP - (time.time() - _last_extract)
        if wait > 0:
            time.sleep(wait)
        try:
            return httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
        finally:
            _last_extract = time.time()


def _reachable(server: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """先确认这条 IP 真能代理流量，再交给浏览器。

    池子里给的 IP 有一定概率已经死了。死 IP 不会让 Playwright 回退直连，
    只会在打开页面时一路超时，白开一次浏览器还耽误整轮任务。

    这里必须真发一个走代理的请求：只测 TCP 端口通不通没用——
    机器上挂着 TUN 模式的透明代理时，连不存在的 IP 也会秒连成功。
    """
    parts = urlparse(server)
    if not parts.hostname or not parts.port:
        return False
    try:
        resp = httpx.get(PROBE_URL, proxy=server, timeout=timeout, follow_redirects=False)
        # 3xx 一样算通：收到抖音的回应就说明这条 IP 在转发，跳不跳转不关我们的事
        if resp.status_code >= 500:
            logger.warning("代理 %s 探活返回 HTTP %s", server, resp.status_code)
            return False
        return True
    except Exception as exc:
        logger.warning("代理 %s 探活失败：%s", server, exc)
        return False


class ProxyExpired(RuntimeError):
    """代理 IP 用满时长了。"""


class ProxyLease:
    """一条限时代理 IP，连同它的到期时间。

    接口没有「释放」这个动作，IP 到点自己失效，所以只能由我们主动收手：
    一条过期的 IP 不是变慢，是彻底没网，抖音那边看到的就是操作做到一半断了。
    """

    def __init__(self, server: str, minutes: int = MINUTE, grace: int = LEASE_GRACE):
        self.server = server
        self.minutes = minutes
        self.started = time.time()
        self.deadline = self.started + max(30, minutes * 60 - grace)

    def used(self) -> float:
        return time.time() - self.started

    def remaining(self) -> float:
        return self.deadline - time.time()

    def expired(self) -> bool:
        return self.remaining() <= 0

    def check(self, what: str = "") -> None:
        """在循环里调用。到点就抛出，让调用方顺着已有的错误处理收拾场面。"""
        if self.expired():
            raise ProxyExpired(
                f"代理 IP {self.server} 已用满 {self.minutes} 分钟，{what or '本次操作'}中断"
            )

    def release(self, what: str = "") -> None:
        """用完立刻登记一笔。IP 收不回来，但浏览器上下文必须当场关掉，别再往这条 IP 上发请求。"""
        logger.info(
            "用完代理 %s 耗时 %.0f 秒（额度 %s 分钟）%s",
            self.server, self.used(), self.minutes, what,
        )


def lease_proxy(area: str, cfg: dict | None = None, reasons: list | None = None) -> ProxyLease | None:
    """取一条 IP 并带上它的有效期。返回 None 表示这次走直连。"""
    server = fetch_proxy(area, cfg, reasons)
    if not server:
        return None
    lease = ProxyLease(server)
    logger.info("代理 %s 有效期 %s 分钟，%.0f 秒后必须收手", server, MINUTE, lease.remaining())
    return lease


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


def proxy_enabled(cfg: dict | None = None) -> bool:
    """住宅代理总开关。关掉、或没配 API 密钥/账号，就当整套 IP 功能都不存在。

    这是唯一的判据：所有「按地区取 IP」的入口都先问它，为 False 就一律走直连、
    不取 IP、不探活、不重试换 IP，地区字段直接忽略。
    """
    cfg = cfg or load_proxy()
    return bool(cfg.get("enabled") and cfg.get("api_key") and cfg.get("phone"))


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
    safe_io.write_json(PROXY_FILE, data)
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
    # 探活没过、但确实提取到的第一条 IP。探活是「优选」不是「否决」：
    # 它自己也可能误判（超时太紧、只测 http），真全都没过时，
    # 带着一条本地区的 IP 去试也比从机房 IP 出去强 —— 设了地区就不该走直连。
    fallback = ""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = _throttled_get(url)
            hit = parse_extract(resp.text)
            if hit:
                server = f"{PROTOCOL}://{hit}"
                if _reachable(server):
                    logger.info("已提取代理 IP %s 地区=%s 第%s次", hit, area_label(code), attempt)
                    return server
                fallback = fallback or server
                last_reason = f"提取到 {hit}，但探活没通过"
                logger.warning("代理 IP %s 探活没过，换一条（第%s/%s次）", hit, attempt, RETRIES)
            else:
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
    if fallback:
        logger.warning(
            "%s 条 IP 都没通过探活，仍按地区要求使用 %s（探活比真实访问更严，可能是误判）",
            RETRIES, fallback,
        )
        return fallback
    logger.error("提取代理连续 %s 次失败，本次回退直连 地区=%s %s", RETRIES, area_label(code), last_reason)
    if reasons is not None and last_reason:
        reasons.append(last_reason)
    return None
