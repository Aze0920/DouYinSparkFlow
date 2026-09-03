"""找出手机端到底是哪个元素顶出了屏幕右边（横向裁切的元凶）。

document.scrollWidth 只能告诉你「有没有溢出」，而且当祖先设了 overflow:hidden 时
它会显示一切正常、可内容其实已经被切掉了。这里逐个元素比对可视区右边界，直接点名。
"""
from __future__ import annotations

import argparse
import sys
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from jinja2 import Template
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_preview import Handler, VERSION, file_from_rev  # noqa: E402

SCAN = """
(vw) => {
  const out = [];
  document.querySelectorAll('.main *').forEach((el) => {
    if (!el.getClientRects().length) return;
    const r = el.getBoundingClientRect();
    const over = Math.round(r.right - vw);
    if (over > 1) {
      const parent = el.parentElement;
      const pr = parent ? parent.getBoundingClientRect() : null;
      out.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 60),
        over,
        width: Math.round(r.width),
        parentCls: parent ? (parent.className || '').toString().slice(0, 40) : '',
        parentWidth: pr ? Math.round(pr.width) : 0,
      });
    }
  });
  return out;
}
"""


def run(rev: str | None, width: int, page_name: str):
    html = Template(file_from_rev(rev, "webui/templates/index.html")).render(version=VERSION)
    css = file_from_rev(rev, "webui/static/app.css")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, html=html, css=css))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 844})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.wait_for_timeout(700)
            page.evaluate("() => { const m=document.getElementById('announceModal'); if(m) m.classList.add('hidden'); }")
            page.evaluate(f"() => showPage('{page_name}')")
            page.wait_for_timeout(400)
            rows = page.evaluate(SCAN, width)
            total = page.evaluate("() => Math.round(document.querySelector('.main').scrollHeight)")
            browser.close()
    finally:
        server.shutdown()
    return rows, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default=None)
    ap.add_argument("--width", type=int, default=393)
    ap.add_argument("--page", default="accounts")
    args = ap.parse_args()
    rows, total = run(args.rev, args.width, args.page)
    tag = args.rev or "工作区"
    print(f"[{tag}] {args.page} @ {args.width}px 宽　页面总高 {total}px")
    if not rows:
        print("  没有元素顶出右边界")
        return 0
    seen = {}
    for r in rows:
        key = (r["tag"], r["cls"])
        if key not in seen:
            seen[key] = r
    print(f"  顶出右边界的元素 {len(rows)} 个（去重后 {len(seen)} 类）：")
    for r in list(seen.values())[:15]:
        print(f"    <{r['tag']} class=\"{r['cls']}\"> 超出 {r['over']}px  自身宽 {r['width']}  "
              f"父级 .{r['parentCls']} 宽 {r['parentWidth']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
