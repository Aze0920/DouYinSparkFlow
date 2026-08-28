"""发送诊断：给一个好友发一条消息，把全过程记录下来，用来判断是不是被抖音限制了。

抖音私信主要走 WebSocket，正常任务里抓不到 HTTP 响应，所以这里额外记录 WS 帧。

用法（在项目根目录）：
    python diagnose_send.py 好友昵称
    python diagnose_send.py 好友昵称 --message 测试一下
    python diagnose_send.py 好友昵称 --headless

结果写到 logs/send-diagnose.log 和 logs/send-diagnose-*.png。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
for _path in (Path(os.getenv("CONFIG_ENV_FILE") or ""), ROOT / ".env", ROOT / "config" / ".env"):
    if _path and _path.is_file():
        load_dotenv(_path)
        break

from core import tasks  # noqa: E402
from core.browser import get_browser, make_context  # noqa: E402
from core.msg_builder import build_message  # noqa: E402
from utils.config import get_config, get_userData  # noqa: E402

OUT = ROOT / "logs" / "send-diagnose.log"


def log(line=""):
    print(line)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(str(line) + "\n")


def pick_account(wanted=""):
    users = get_userData()
    if not users:
        raise SystemExit("没有账号，先在网页里保存一个账号再来诊断")
    if wanted:
        for user in users:
            if wanted in (user.get("username", ""), str(user.get("unique_id", ""))):
                return user
        raise SystemExit(f"找不到账号 {wanted}")
    return users[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("friend", help="要发给谁（会话列表里的昵称）")
    parser.add_argument("--account", default="", help="用哪个账号，默认第一个")
    parser.add_argument("--message", default="", help="发什么，默认用配置里的模板")
    parser.add_argument("--headless", action="store_true", help="不开浏览器窗口")
    args = parser.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("", encoding="utf-8")
    os.environ["HEADLESS"] = "1" if args.headless else "0"

    config = get_config()
    user = pick_account(args.account)
    username = user.get("username", "未知用户")
    message = args.message or build_message(user.get("messageTemplate") or config.get("messageTemplate") or "")

    log(f"账号: {username}")
    log(f"目标好友: {args.friend}")
    log(f"消息: {message!r}")
    log("-" * 60)

    requests_seen = []
    ws_frames = []

    playwright, browser = get_browser()
    context = None
    try:
        from webui.session_store import load_state_path

        context = make_context(
            browser,
            storage_state=load_state_path(str(user.get("unique_id") or "")),
            cookies=user["cookies"],
        )
        context.set_default_timeout(15000)
        page = context.new_page()

        def on_response(response):
            url = response.url
            if not any(k in url for k in ("/im/", "message", "send")):
                return
            body = ""
            try:
                body = json.dumps(response.json(), ensure_ascii=False)[:600]
            except Exception:
                body = "<非 JSON>"
            requests_seen.append((response.status, url[:160], body))

        def on_ws(ws):
            log(f"[WS] 连接 {ws.url[:120]}")
            ws.on("framesent", lambda payload: ws_frames.append(("发出", len(payload))))
            ws.on("framereceived", lambda payload: ws_frames.append(("收到", len(payload))))

        page.on("response", on_response)
        page.on("websocket", on_ws)

        page.goto("https://www.douyin.com/chat")
        time.sleep(2)

        if tasks._looks_like_login(page):
            log("!! 页面在要求登录，Cookie 已失效，先去网页里重新扫码")
            return

        item_loc, scope, item_sel = tasks._wait_locator(page, tasks.CONVERSATION_ITEM_SELECTORS, timeout_ms=15000)
        if item_loc is None:
            log("!! 打不开会话列表")
            return
        log(f"会话列表 selector={item_sel} 条目数={tasks._locator_count(item_loc)}")

        target = None
        for index in range(min(tasks._locator_count(item_loc), 40)):
            element = item_loc.nth(index)
            if tasks._item_title(element) == args.friend:
                target = element
                break
        if target is None:
            log(f"!! 会话列表前 40 条里没找到 {args.friend}，先在抖音网页里把这个会话置顶再试")
            return
        target.click()
        time.sleep(1.5)

        chat_input, _, editor_sel = tasks._wait_locator(page, tasks.CHAT_EDITOR_SELECTORS, timeout_ms=15000)
        if chat_input is None:
            log("!! 找不到输入框")
            return
        editor = tasks._editor_target(page, chat_input)
        log(f"输入框 selector={editor_sel}")
        log(f"真正可编辑元素: {editor.evaluate('el => el.tagName + ` contenteditable=` + el.getAttribute(`contenteditable`)')}")

        requests_seen.clear()
        ws_frames.clear()

        tasks._type_message(page, editor, message.split("\\n"))
        typed = tasks._editor_text(editor)
        log(f"输入后编辑器内容: {typed!r}")
        if not typed:
            log("!! 文字根本没进输入框，是选择器问题，不是风控")
            return

        page.screenshot(path=str(OUT.parent / "send-diagnose-before.png"))
        page.keyboard.press("Enter")

        for _ in range(20):
            time.sleep(0.25)
            if not tasks._editor_text(editor):
                break
        log(f"回车后编辑器内容: {tasks._editor_text(editor)!r}")

        time.sleep(2.5)
        page.screenshot(path=str(OUT.parent / "send-diagnose-after.png"))

        log("-" * 60)
        log(f"WebSocket 帧: {len(ws_frames)} 个 (发出 {sum(1 for d, _ in ws_frames if d == '发出')})")
        log(f"相关 HTTP 响应: {len(requests_seen)} 条")
        for status, url, body in requests_seen[:15]:
            log(f"  [{status}] {url}")
            log(f"         {body}")

        toast = tasks._toast_warning(page)
        log(f"提示条: {toast or '无'}")

        texts = page.evaluate(
            """() => [...document.querySelectorAll('[class*="essage"],[class*="ubble"]')]
                 .slice(-12).map(el => (el.innerText || '').slice(0, 80)).filter(Boolean)"""
        )
        log("聊天区最后几条内容:")
        for text in texts:
            log(f"  | {text!r}")

        log("")
        log("怎么看结果：")
        log("  1. 打开 logs/send-diagnose-after.png，看消息有没有出现在对话里、旁边有没有红色感叹号")
        log("  2. 上面的 HTTP 响应里如果有 status_code 非 0，括号里的文字就是抖音给的拒绝理由")
        log("  3. 如果消息出现在页面上、也没有报错，那就是发出去了，问题在别处")
    finally:
        for closer in (
            lambda: context and context.close(),
            browser.close,
            playwright.stop,
        ):
            try:
                closer()
            except Exception:
                pass
        log(f"\n完整记录: {OUT}")


if __name__ == "__main__":
    sys.exit(main())
