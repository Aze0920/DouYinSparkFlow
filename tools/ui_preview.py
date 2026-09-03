"""本地渲染控制台页面并截图，用来肉眼核对手机端布局。

不连真后端：起一个小 HTTP 服务，把模板渲染出来，/api/* 一律返回假数据，
这样能在没有账号、没有 .env 的情况下把仪表盘 / 账号列表 / 我的 / 设置都渲染出来。

用法：
    python tools/ui_preview.py out_dir [--rev HEAD]
--rev 指定时从 git 取那个版本的 index.html 和 app.css（用于改动前后对比）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jinja2 import Template
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VERSION = "preview"

USER = {
    "username": "小天秤",
    "role": "admin",
    "remain_label": "还剩 128 天",
    "account_limit_label": "最多 10 个账号",
    "expires_label": "2027-01-08",
    "last_login_label": "2026-09-03 16:40",
    "last_login_ip": "223.104.38.77",
    "wxpusher_bound": False,
    "invite_pending": False,
    "max_accounts": 10,
}


def make_account(idx: int, bound: bool = True) -> dict:
    return {
        "username": f"火花小号{idx}",
        "unique_id": f"douyin_{idx}0086",
        "avatar": "",
        "cookies_set": bound,
        "cookie_status": "ok" if bound else "",
        "cookie_source": "qr",
        "owner": "小天秤",
        "region": "410700",
        "cron_hour": 9,
        "cron_minute": 0,
        "message_template": "早安，今天也要元气满满呀～",
        "targets": ["阿橙", "小满"] if bound else [],
        "target_avatars": {},
        "target_sparks": {"阿橙": 128, "小满": 47},
    }


ACCOUNTS = [make_account(1), make_account(2), make_account(3, bound=False)]

FAKE_API = {
    "/api/me": {"authed": True, "user": USER},
    "/api/status": {
        "ok": True,
        "running": False,
        "run_message": "空闲",
        "running_ids": [],
        "local_version": "1.3.6",
        "remote_version": "1.3.6",
        "update_available": False,
        "total_accounts": 3,
        "total_users": 12,
        "total_cards": 40,
        "unused_cards": 17,
        "allow_self_unbind": True,
        "invite_enabled": True,
        "proxy_enabled": True,
        "me": USER,
        "spark_stats": {"today_ok": 12, "today_fail": 1, "week_ok": 76},
        "accounts": ACCOUNTS,
    },
    "/api/config": {
        "ok": True,
        "max_task_threads": 10,
        "message_template": "早安～",
        "proxy_enabled": True,
        "accounts": ACCOUNTS,
    },
    "/api/regions": {
        "ok": True,
        "provinces": [
            {"code": "410000", "name": "河南省", "cities": [{"code": "410700", "name": "新乡市"}]},
            {"code": "110000", "name": "北京市", "cities": [{"code": "110100", "name": "北京市"}]},
        ],
    },
    "/api/announcement": {
        "ok": True,
        "active": True,
        "title": "系统维护通知",
        "content": "今晚 00:30-01:00 将进行例行维护，期间续火花任务会暂停一次。\n维护结束后系统会自动恢复，无需手动操作。",
        "version": 1725000000,
    },
    "/api/settings/notify": {"ok": True, "wechat": {}, "wxpusher": {}, "notifyx": {}, "events": {}},
    "/api/settings/proxy": {"ok": True, "enabled": True, "api_key": "****abcd", "phone": "13800000000"},
    "/api/settings/announcement": {
        "ok": True,
        "enabled": True,
        "title": "系统维护通知",
        "content": "今晚 00:30-01:00 将进行例行维护，期间续火花任务会暂停一次。",
        "version": 1725000000,
    },
    "/api/settings/invite": {"ok": True, "enabled": True, "inviter_days": 1, "invitee_days": 1},
    "/api/users": {"ok": True, "users": []},
    "/api/cards": {"ok": True, "cards": []},
    "/api/invite/me": {"ok": True, "enabled": True, "link": "", "records": []},
    "/api/logs": {"ok": True, "text": "预览模式没有日志"},
}


def file_from_rev(rev: str | None, rel: str) -> str:
    if not rev:
        return (ROOT / rel).read_text(encoding="utf-8")
    out = subprocess.run(
        ["git", "show", f"{rev}:{rel}"], cwd=ROOT, capture_output=True, check=True
    )
    return out.stdout.decode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, html: str = "", css: str = "", **kwargs):
        self.html = html
        self.css = css
        super().__init__(*args, **kwargs)

    def log_message(self, *args):  # 静音，别刷屏
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/static/app.css"):
            return self._send(self.css.encode("utf-8"), "text/css; charset=utf-8")
        if path.startswith("/api/"):
            body = FAKE_API.get(path, {"ok": True})
            return self._send(json.dumps(body).encode("utf-8"), "application/json")
        return self._send(self.html.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = FAKE_API.get(self.path.split("?")[0], {"ok": True})
        self._send(json.dumps(body).encode("utf-8"), "application/json")


def shoot(out_dir: Path, rev: str | None, label: str):
    html = Template(file_from_rev(rev, "webui/templates/index.html")).render(version=VERSION)
    css = file_from_rev(rev, "webui/static/app.css")

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, html=html, css=css))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    out_dir.mkdir(parents=True, exist_ok=True)
    pages = [("home", "首页仪表盘"), ("accounts", "账号列表"), ("mine", "我的"), ("settings", "设置")]
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.wait_for_timeout(600)
            # 公告弹窗会挡住页面，先收掉
            page.evaluate("() => { const m = document.getElementById('announceModal'); if (m) m.classList.add('hidden'); }")
            for name, _cn in pages:
                page.evaluate(f"() => showPage('{name}')")
                page.wait_for_timeout(350)
                page.evaluate("() => window.scrollTo(0, 0)")
                shot = out_dir / f"{label}-{name}.png"
                page.screenshot(path=str(shot), full_page=True)
                height = page.evaluate("() => document.documentElement.scrollHeight")
                results.append((name, height, shot))
            browser.close()
    finally:
        server.shutdown()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--rev", default=None, help="从该 git 版本取模板与样式")
    ap.add_argument("--label", default="after")
    args = ap.parse_args()
    for name, height, shot in shoot(Path(args.out_dir), args.rev, args.label):
        print(f"{args.label:6s} {name:9s} 页面总高 {height:5d}px  -> {shot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
