"""折叠改动的功能自检。

折叠是用 display:none 藏起 .account-body 的，必须确认：
1) 收起状态下 collectAccounts() 仍能读到完整数据（保存不会把配置清空）；
2) 展开/收起能正常切换，且展开后没有横向溢出；
3) 桌面端布局完全没被动到。
"""
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


def boot(browser, port, width, height):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
    page.wait_for_timeout(700)
    page.evaluate("() => { const m=document.getElementById('announceModal'); if(m) m.classList.add('hidden'); }")
    page.evaluate("() => showPage('accounts')")
    page.wait_for_timeout(400)
    return page


def serve(rev):
    html = Template(file_from_rev(rev, "webui/templates/index.html")).render(version=VERSION)
    css = file_from_rev(rev, "webui/static/app.css")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, html=html, css=css))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def main():
    failures = []
    server, port = serve(None)
    old_server, old_port = serve("HEAD")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()

            # ---- 手机端：收起时的数据完整性 ----
            page = boot(browser, port, 393, 844)
            collapsed = page.evaluate("() => collectAccounts()")
            n_open = page.evaluate("() => document.querySelectorAll('.account.is-open').length")
            print(f"手机 收起状态：卡片 {len(collapsed)} 张，展开 {n_open} 张")
            if n_open != 0:
                failures.append("手机端默认应当全部收起")

            first = collapsed[0] if collapsed else {}
            print(f"  收起时读到的第 1 张：unique_id={first.get('unique_id')!r} "
                  f"targets={len(first.get('targets') or [])} "
                  f"template_len={len(first.get('message_template') or '')} "
                  f"cron={first.get('cron_hour')}:{first.get('cron_minute'):02d} "
                  f"region={first.get('region')!r}")
            if not first.get("unique_id"):
                failures.append("收起状态下读不到 unique_id")
            if not (first.get("targets") or []):
                failures.append("收起状态下读不到目标好友——保存会清空配置！")
            if not (first.get("message_template") or ""):
                failures.append("收起状态下读不到消息模板")
            if not first.get("region"):
                failures.append("收起状态下读不到上网地区")

            # 和展开后读到的数据逐字段比对，必须完全一致
            page.evaluate("() => toggleAccountCard(0, null)")
            page.wait_for_timeout(300)
            expanded = page.evaluate("() => collectAccounts()")
            if expanded[0] != collapsed[0]:
                failures.append(f"展开前后读到的数据不一致：{collapsed[0]} != {expanded[0]}")
            else:
                print("  展开前后 collectAccounts() 结果完全一致 ✓")

            n_open = page.evaluate("() => document.querySelectorAll('.account.is-open').length")
            if n_open != 1:
                failures.append(f"点击后应有 1 张展开，实际 {n_open}")

            over = page.evaluate(
                """(vw) => [...document.querySelectorAll('.main *')]
                     .filter(el => el.getClientRects().length &&
                                   Math.round(el.getBoundingClientRect().right - vw) > 1).length""",
                393,
            )
            h_open = page.evaluate("() => Math.round(document.querySelector('.main').scrollHeight)")
            print(f"  展开 1 张后：总高 {h_open}px，横向溢出元素 {over} 个")
            if over:
                failures.append(f"展开后有 {over} 个元素顶出右边界")

            # 收回去
            page.evaluate("() => toggleAccountCard(0, null)")
            page.wait_for_timeout(200)
            if page.evaluate("() => document.querySelectorAll('.account.is-open').length") != 0:
                failures.append("再点一次没有收起")
            else:
                print("  再点一次能正常收起 ✓")

            # 点「检测」按钮不应触发折叠
            page.evaluate("() => { window.__checkCalled = 0; window.checkAccount = () => { window.__checkCalled++; }; }")
            page.evaluate("() => toggleAccountCard(0, null)")   # 先展开，让按钮可见
            page.wait_for_timeout(250)
            page.eval_on_selector('.account[data-index="0"] .account-head-actions button', "el => el.click()")
            page.wait_for_timeout(200)
            still_open = page.evaluate("() => document.querySelector('.account[data-index=\\'0\\']').classList.contains('is-open')")
            called = page.evaluate("() => window.__checkCalled")
            print(f"  点「检测」：checkAccount 被调用 {called} 次，卡片仍展开={still_open}")
            if not called:
                failures.append("点「检测」没有触发 checkAccount")
            if not still_open:
                failures.append("点「检测」把卡片折叠了（事件冒泡没挡住）")
            page.close()

            # ---- 桌面端：必须和改动前一模一样 ----
            new_page = boot(browser, port, 1440, 900)
            old_page = boot(browser, old_port, 1440, 900)
            new_h = new_page.evaluate("() => Math.round(document.querySelector('.main').scrollHeight)")
            old_h = old_page.evaluate("() => Math.round(document.querySelector('.main').scrollHeight)")
            new_card = new_page.evaluate("() => Math.round(document.querySelector('.account').getBoundingClientRect().height)")
            old_card = old_page.evaluate("() => Math.round(document.querySelector('.account').getBoundingClientRect().height)")
            print(f"桌面 1440：页面总高 改前 {old_h} → 改后 {new_h}；单卡高 改前 {old_card} → 改后 {new_card}")
            if new_h != old_h or new_card != old_card:
                failures.append(f"桌面端布局被改动了（{old_h}/{old_card} -> {new_h}/{new_card}）")
            browser.close()
    finally:
        server.shutdown()
        old_server.shutdown()

    print()
    if failures:
        print("自检失败：")
        for f in failures:
            print("  -", f)
        return 1
    print("自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
