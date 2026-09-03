"""改动前后对比：手机端各页面高度、可滚动性，以及桌面端是否被误伤。"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from jinja2 import Template
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_preview import Handler, VERSION, file_from_rev  # noqa: E402

PAGES = ["home", "accounts", "mine", "settings"]
VIEWPORTS = {"手机 390x844": (390, 844), "平板 768x1024": (768, 1024), "桌面 1440x900": (1440, 900)}


def probe(rev: str | None):
    html = Template(file_from_rev(rev, "webui/templates/index.html")).render(version=VERSION)
    css = file_from_rev(rev, "webui/static/app.css")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, html=html, css=css))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for vname, (w, h) in VIEWPORTS.items():
                page = browser.new_page(viewport={"width": w, "height": h})
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                page.wait_for_timeout(500)
                page.evaluate("() => { const m=document.getElementById('announceModal'); if(m) m.classList.add('hidden'); }")
                for name in PAGES:
                    page.evaluate(f"() => showPage('{name}')")
                    page.wait_for_timeout(250)
                    info = page.evaluate(
                        """() => {
                          const main = document.querySelector('.main');
                          const doc = document.documentElement;
                          // 横向溢出是手机端最常见的破版症状
                          const overflowX = Math.max(doc.scrollWidth - doc.clientWidth, 0);
                          return { h: main ? Math.round(main.scrollHeight) : 0, overflowX };
                        }"""
                    )
                    # 能不能真的滚到底
                    page.evaluate("() => window.scrollTo(0, 999999)")
                    page.wait_for_timeout(120)
                    reach = page.evaluate(
                        """() => {
                          const el = document.scrollingElement || document.documentElement;
                          const bottom = el.scrollHeight - el.clientHeight;
                          return Math.abs(el.scrollTop - bottom) < 4 || bottom <= 0;
                        }"""
                    )
                    page.evaluate("() => window.scrollTo(0, 0)")
                    out[(vname, name)] = (info["h"], info["overflowX"], reach)
                page.close()
            browser.close()
    finally:
        server.shutdown()
    return out


def main():
    before = probe("HEAD")
    after = probe(None)
    print(f"{'视口':<16}{'页面':<10}{'改前':>7}{'改后':>7}{'变化':>10}   横向溢出   能滚到底")
    print("-" * 74)
    for key in before:
        b_h, _, _ = before[key]
        a_h, a_ox, a_reach = after[key]
        delta = f"{(a_h - b_h) / b_h * 100:+.1f}%" if b_h else "-"
        flag_ox = "无" if a_ox == 0 else f"{a_ox}px !!"
        flag_reach = "是" if a_reach else "否 !!"
        print(f"{key[0]:<16}{key[1]:<10}{b_h:>7}{a_h:>7}{delta:>10}   {flag_ox:<9} {flag_reach}")
    bad = [k for k, v in after.items() if v[1] != 0 or not v[2]]
    print("\n结论：", "全部通过（无横向溢出、都能滚到底）" if not bad else f"有问题：{bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
