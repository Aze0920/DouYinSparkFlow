import random
import re
import threading
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.logger import LOG_FILE, setup_logger
from utils.config import get_config, get_userData
from utils import norm
from core.msg_builder import build_message
from core.browser import get_browser, make_context
from playwright.sync_api import Response
import time

config = get_config()
logger = setup_logger(level=config.get("logLevel", "Info"))
_preview_ctx = threading.local()


def _preview_bind(account: str = "", unique_id: str = ""):
    _preview_ctx.account = account
    _preview_ctx.unique_id = unique_id


def _live(page=None, **kwargs):
    """实时预览失败不能拖累发送。"""
    try:
        from webui.run_preview import publish

        kwargs.setdefault("account", getattr(_preview_ctx, "account", "") or "")
        kwargs.setdefault("unique_id", getattr(_preview_ctx, "unique_id", "") or "")
        publish(page, **kwargs)
    except Exception:
        logger.debug("实时预览更新失败", exc_info=True)

CONVERSATION_ITEM_SELECTOR = ".conversationConversationItemwrapper"
CONVERSATION_TITLE_SELECTOR = ".conversationConversationItemtitle"
CONVERSATION_LIST_SELECTOR = ".conversationConversationListwrapper"
CHAT_EDITOR_SELECTOR = ".messageEditorimChatEditorContainer"
CONVERSATION_LIST_SELECTORS = [
    CONVERSATION_LIST_SELECTOR,
    "[class*='ConversationListwrapper']",
    "[class*='conversationListwrapper']",
    "[class*='ConversationList']",
    "[class*='conversation-list']",
    "[class*='conversationList']",
    "[class*='session-list']",
    "[class*='SessionList']",
    "[class*='chat-list']",
    "[class*='ChatListwrapper']",
]
CONVERSATION_ITEM_SELECTORS = [
    CONVERSATION_ITEM_SELECTOR,
    '[data-e2e="conversation-item"]',
    '[data-e2e="session-item"]',
    "[class*='ConversationItemwrapper']",
    "[class*='conversationItemwrapper']",
    "[class*='ConversationItem']",
    "[class*='conversation-item']",
    "[class*='conversationItem']",
    "[class*='sessionItem']",
    "[class*='SessionItem']",
    "[class*='session-item']",
    "[class*='chatListItem']",
]
CONVERSATION_TITLE_SELECTORS = [
    CONVERSATION_TITLE_SELECTOR,
    "[class*='ConversationItemtitle']",
    "[class*='conversationItemtitle']",
    "[class*='ItemTitle']",
    "[class*='item-title']",
    "[class*='nickName']",
    "[class*='nickname']",
]
CHAT_EDITOR_SELECTORS = [
    CHAT_EDITOR_SELECTOR,
    "[class*='imChatEditor']",
    "[class*='ChatEditor']",
    "[class*='messageEditor']",
    "[contenteditable='true']",
]
SEND_BUTTON_SELECTORS = [
    "button:has-text('发送')",
    "[class*='sendBtn']",
    "[class*='SendBtn']",
    "[class*='SendButton']",
    "[data-e2e='im-send']",
    "[class*='send-btn']",
]
CHAT_BUBBLE_JS = """() => {
  const nodes = [...document.querySelectorAll(
    '[data-e2e*="message"],[class*="essage"],[class*="ubble"],[class*="MessageItem"],[class*="messageItem"],[class*="imMessage"],[class*="chatItem"],[class*="ChatItem"]'
  )];
  return nodes.slice(-24).map(el => (el.innerText || '').replace(/\\s+/g, ' ').trim()).filter(Boolean);
}"""
LIST_PREVIEW_JS = """() => {
  const items = [...document.querySelectorAll(
    '[data-e2e="conversation-item"], .conversationConversationItemwrapper, [class*="ConversationItemwrapper"]'
  )];
  return items.map((el) => {
    const title = el.querySelector('[class*="ConversationItemtitle"], [class*="Itemtitle"], [class*="nickName"]');
    const hint = el.querySelector('[class*="HinttextBox"], [class*="Deschint"], [class*="ItemHint"], [class*="Descleft"]');
    const time = el.querySelector('[class*="timeStr"], [class*="Titletime"]');
    return {
      title: ((title && title.innerText) || '').replace(/\\s+/g, ' ').trim(),
      preview: ((hint && hint.innerText) || '').replace(/\\s+/g, ' ').trim(),
      time: ((time && time.innerText) || '').replace(/\\s+/g, ' ').trim(),
      current: /curConversation|isActive|selected/i.test(el.className || ''),
    };
  }).filter((row) => row.title || row.preview);
}"""
CLICK_CONVERSATION_JS = """(title) => {
  /* click-conversation */
  const items = [...document.querySelectorAll(
    '[data-e2e="conversation-item"], .conversationConversationItemwrapper, [class*="ConversationItemwrapper"]'
  )];
  const el = items.find((item) => {
    const t = item.querySelector('[class*="ConversationItemtitle"], [class*="Itemtitle"], [class*="nickName"]');
    return ((t && t.innerText) || '').replace(/\\s+/g, ' ').trim() === title;
  });
  if (!el) return false;
  el.click();
  return true;
}"""
LOGIN_HINTS = ("扫码登录", "登录后免费畅享", "打开「抖音APP」", "验证码登录", "请使用抖音APP")
CHALLENGE_HINTS = (
    "请完成验证",
    "滑动验证",
    "拖动滑块",
    "安全验证",
    "智能验证",
    "异常访问",
    "请进行验证",
    "访问验证",
)
TOAST_SELECTORS = [
    "[class*='toast']",
    "[class*='Toast']",
    "[class*='messageNotice']",
    "[class*='message-notice']",
    "[role='alert']",
]
# 抖音把消息拦下来时，前端照样会清空输入框，只在这些提示里说明原因
BLOCK_HINTS = (
    "操作频繁",
    "操作过于频繁",
    "发送太快",
    "请稍后再试",
    "稍后重试",
    "发送失败",
    "账号异常",
    "存在异常",
    "涉嫌违规",
    "违规",
    "已被限制",
    "限制使用",
    "触发风控",
    "还不是好友",
    "需要先关注",
    "好友验证",
    "拒收",
    "拉黑",
)
SEND_API_HINTS = ("message/send", "/im/send", "send_message")


def _make_info_handler(store: dict):
    def handle_response(response: Response):
        if "aweme/v1/web/im/user/info" not in response.url:
            return
        try:
            json_data = response.json()
            for item in json_data.get("data", []):
                short_id = item.get("short_id")
                unique_id = item.get("unique_id")
                sec_uid = item.get("sec_uid", "")
                nickname = norm(item.get("nickname"))
                remark_name = norm(item.get("remark_name", nickname))
                store[remark_name] = [short_id, unique_id, sec_uid, nickname, remark_name]
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            last = tb[-1] if tb else None
            logger.debug(
                "解析好友资料失败: %s %s",
                e,
                f"{last.filename}:{last.lineno}" if last else "",
            )

    return handle_response


def _make_send_handler(store: list):
    """记录 IM 发送接口的返回。status_code 非 0 就是服务端把消息拦下来了。"""

    def handle_response(response: Response):
        url = response.url
        if not any(hint in url for hint in SEND_API_HINTS):
            return
        record = {"url": url, "http": response.status, "code": None, "msg": ""}
        try:
            data = response.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            for key in ("status_code", "statusCode", "status", "code", "err_no", "error_code"):
                if key in data:
                    try:
                        record["code"] = int(data[key])
                    except (TypeError, ValueError):
                        pass
                    break
            for key in ("status_msg", "status_message", "message", "msg", "err_msg", "prompt"):
                value = data.get(key)
                if value:
                    record["msg"] = str(value)
                    break
        store.append(record)

    return handle_response


def _send_failure_from_api(records: list) -> str:
    for record in records:
        code = record.get("code")
        if code not in (None, 0):
            return f"接口返回 status_code={code} {record.get('msg') or ''}".strip()
        if not 200 <= int(record.get("http") or 0) < 400:
            return f"接口 HTTP {record.get('http')} {record.get('msg') or ''}".strip()
    return ""


def _toast_warning(page) -> str:
    """只扫提示条，不扫全页，避免把聊天记录里的历史文字误判成风控。"""
    for scope in _iter_scopes(page):
        for selector in TOAST_SELECTORS:
            try:
                loc = scope.locator(selector)
                count = min(_locator_count(loc), 5)
            except Exception:
                continue
            for index in range(count):
                try:
                    text = (loc.nth(index).inner_text(timeout=800) or "").strip()
                except Exception:
                    continue
                if not text or len(text) > 60:
                    continue
                if any(hint in text for hint in BLOCK_HINTS):
                    return text
    return ""


def _iter_scopes(page):
    yield page
    try:
        for frame in page.frames:
            if frame is not page:
                yield frame
    except Exception:
        return


def _locator_count(locator) -> int:
    try:
        return locator.count()
    except Exception:
        return 0


def _find_locator(page, selectors, min_count=1):
    for scope in _iter_scopes(page):
        for selector in selectors:
            try:
                loc = scope.locator(selector)
            except Exception:
                continue
            if _locator_count(loc) >= min_count:
                return loc, scope, selector
    return None, page, ""


def _wait_locator(page, selectors, timeout_ms=12000, min_count=1):
    deadline = time.time() + max(timeout_ms, 500) / 1000
    last = (None, page, "")
    while time.time() < deadline:
        last = _find_locator(page, selectors, min_count=min_count)
        if last[0] is not None:
            return last
        time.sleep(0.35)
    return last


def _page_text(page) -> str:
    chunks = []
    for scope in _iter_scopes(page):
        try:
            chunks.append(scope.inner_text("body", timeout=1500) or "")
        except Exception:
            continue
    return "\n".join(chunks)


def _looks_like_login(page) -> bool:
    text = _page_text(page)
    return any(hint in text for hint in LOGIN_HINTS)


def _looks_like_challenge(page) -> bool:
    text = _page_text(page)
    return any(hint in text for hint in CHALLENGE_HINTS)


def _item_title(element) -> str:
    for selector in CONVERSATION_TITLE_SELECTORS:
        loc = element.locator(selector)
        if _locator_count(loc) > 0:
            try:
                return (loc.first.inner_text(timeout=1500) or "").strip()
            except Exception:
                continue
    try:
        return (element.inner_text(timeout=1500) or "").split("\n")[0].strip()
    except Exception:
        return ""


def _dump_chat_debug(page, username: str):
    logger.warning("账号 %s 当前页面 url=%s", username, getattr(page, "url", ""))
    try:
        logger.warning("账号 %s 页面文字: %s", username, _page_text(page).replace("\n", " ")[:400])
    except Exception:
        pass
    try:
        classes = page.evaluate(
            """() => [...document.querySelectorAll('[class*="conversation"],[class*="Conversation"],[class*="im-"],[class*="messageEditor"]')]
              .slice(0, 25)
              .map((el) => String(el.className || '').slice(0, 120))"""
        )
        logger.warning("账号 %s 会话相关 class: %s", username, classes)
    except Exception:
        logger.debug("账号 %s 读取 class 失败", username, exc_info=True)
    try:
        safe = "".join(ch for ch in username if ch.isalnum() or ch in ("_", "-")) or "account"
        path = Path(LOG_FILE).parent / f"chat-debug-{safe}.png"
        # 走代理时页面慢，screenshot 默认会「等字体加载完」，动不动就超时。
        # 这只是张排障截图，给足时间、加载不出字体也无所谓，别为它甩堆栈。
        page.screenshot(path=str(path), full_page=False, timeout=20000)
        logger.warning("账号 %s 已保存页面截图 %s", username, path)
    except Exception as exc:
        logger.debug("账号 %s 保存截图失败（%s）", username, type(exc).__name__)


def _scroll_list(scope, list_loc, item_loc) -> bool:
    handle = None
    try:
        if list_loc is not None:
            handle = list_loc.first.element_handle(timeout=2500)
    except Exception:
        handle = None
    if handle is None and item_loc is not None:
        try:
            item_handle = item_loc.first.element_handle(timeout=2500)
            handle = item_handle
        except Exception:
            handle = None
    if handle is None:
        return False
    try:
        before = scope.evaluate(
            """(el) => {
              let p = el;
              while (p) {
                const s = getComputedStyle(p);
                if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && p.scrollHeight > p.clientHeight + 8) {
                  return { top: p.scrollTop, found: true };
                }
                p = p.parentElement;
              }
              return { top: el.scrollTop || 0, found: false };
            }""",
            handle,
        )
        scope.evaluate(
            """(el) => {
              let p = el;
              while (p) {
                const s = getComputedStyle(p);
                if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && p.scrollHeight > p.clientHeight + 8) {
                  p.scrollTop += 400;
                  return;
                }
                p = p.parentElement;
              }
              if (el.scrollTop !== undefined) el.scrollTop += 400;
            }""",
            handle,
        )
        after = scope.evaluate(
            """(el) => {
              let p = el;
              while (p) {
                const s = getComputedStyle(p);
                if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && p.scrollHeight > p.clientHeight + 8) {
                  return p.scrollTop;
                }
                p = p.parentElement;
              }
              return el.scrollTop || 0;
            }""",
            handle,
        )
        return bool(before) and after != (before.get("top") if isinstance(before, dict) else before)
    except Exception:
        return False


def retry_operation(name, operation, retries=3, delay=2, *args, **kwargs):
    for attempt in range(retries):
        try:
            return operation(*args, **kwargs)
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f"{name} 失败，正在重试第 {attempt + 1} 次，错误：{e}")
                time.sleep(delay)
            else:
                logger.error(f"{name} 失败，已达到最大重试次数，错误：{e}")
                raise


def checkTargetName(targetName, targets, user_id_dict):
    targetName = norm(targetName)
    if targetName in user_id_dict:
        matched = next((v for v in user_id_dict[targetName] if v and v in targets), None)
        if matched is not None:
            return matched
    if targetName in targets:
        return targetName
    return None


def scroll_and_select_user(page, username, targets, user_id_dict, item_loc, list_loc, scope):
    logger.debug(f"账号 {username} 开始查找目标好友列表")
    logger.debug(f"账号 {username} 目标好友列表: {targets}")

    found_targets = set()
    remaining_targets = set(targets)
    empty_scroll_count = 0
    MAX_EMPTY_SCROLLS = 6

    while True:
        target_elements = item_loc.all() if item_loc is not None else []
        prev_found_count = len(found_targets)

        for element in target_elements:
            try:
                targetName = _item_title(element)
                if not targetName or targetName in found_targets:
                    continue
                found_targets.add(targetName)

                logger.debug(f"账号 {username} 找到好友 {targetName}")
                targetSymbol = checkTargetName(targetName, targets, user_id_dict)
                if not targetSymbol:
                    continue

                element.click()
                time.sleep(0.8)
                yield targetSymbol, targetName

                if targetSymbol in remaining_targets:
                    remaining_targets.remove(targetSymbol)
                if not remaining_targets:
                    logger.debug(f"账号 {username} 所有目标好友均已找到，停止搜索")
                    return
                break
            except Exception:
                traceback.print_exc()
        else:
            new_found = len(found_targets) > prev_found_count
            if new_found:
                empty_scroll_count = 0
            else:
                empty_scroll_count += 1

            if empty_scroll_count >= MAX_EMPTY_SCROLLS:
                logger.warning(
                    f"账号 {username} 连续 {MAX_EMPTY_SCROLLS} 次滚动未发现新好友，判定已到达底部"
                )
                if remaining_targets:
                    logger.warning(
                        f"账号 {username} 搜索结束，仍有以下好友未找到: {remaining_targets}"
                    )
                break

            moved = _scroll_list(scope, list_loc, item_loc)
            if not moved:
                empty_scroll_count += 2
                logger.debug(f"账号 {username} 会话列表无法继续滚动 (空滚动计数: {empty_scroll_count}/{MAX_EMPTY_SCROLLS})")
            time.sleep(0.35)
            item_loc, scope, item_sel = _find_locator(page, CONVERSATION_ITEM_SELECTORS)
            list_loc, _, _ = _find_locator(page, CONVERSATION_LIST_SELECTORS)
            if item_loc is None:
                logger.warning("账号 %s 滚动后会话条目消失 selector=%s", username, item_sel)
                break


def _editor_target(page, chat_input):
    """定位真正可编辑的元素。命中的往往是外层容器，Enter 打在容器上不会发送。"""
    try:
        inner = chat_input.first.locator("[contenteditable='true']")
        if _locator_count(inner) > 0:
            return inner.first
    except Exception:
        pass
    try:
        if (chat_input.first.get_attribute("contenteditable") or "") == "true":
            return chat_input.first
    except Exception:
        pass
    loc, _, _ = _find_locator(page, ["[contenteditable='true']"])
    if loc is not None:
        return loc.first
    return chat_input.first


def _editor_text(editor) -> str:
    """富文本清空后常留下 <br>、零宽字符和换行，全部当成空，否则会误判成没发出去。"""
    try:
        raw = editor.evaluate("el => el.innerText || el.textContent || ''") or ""
    except Exception:
        return ""
    for junk in ("\u200b", "\u200c", "\u200d", "\ufeff", "\xa0"):
        raw = raw.replace(junk, "")
    return raw.strip()


def _type_message(page, editor, lines):
    editor.click()
    try:
        editor.evaluate("el => el.focus && el.focus()")
    except Exception:
        pass
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    for index, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if index != len(lines) - 1:
            page.keyboard.press("Shift+Enter")


def _press_enter(page, editor):
    try:
        editor.press("Enter")
        return
    except Exception:
        pass
    page.keyboard.press("Enter")


def _looks_blocked(exc) -> bool:
    text = str(exc)
    return "抖音拒绝" in text or "抖音提示" in text


def _send_gap() -> float:
    """好友之间留随机间隔，固定节奏最容易撞上频控。"""
    low = float(config.get("sendMinDelay") or 0)
    high = float(config.get("sendMaxDelay") or 0)
    if high < low:
        low, high = high, low
    if high <= 0:
        return 0.5
    return random.uniform(max(low, 0), high)


def _message_snippet(message: str) -> str:
    """从文案里挑一小段，用来核对聊天区是不是真的出现了这条消息。"""
    text = str(message or "").replace("\\n", "\n")
    skip = ("每日一言", "自动续火花助手", "[error]")
    for line in text.splitlines():
        line = " ".join(line.split()).strip()
        if len(line) < 2 or any(token in line for token in skip):
            continue
        return line[:32]
    return " ".join(text.split())[:32]


def _plain_snippet(snippet: str) -> str:
    """[盖瑞][加一] 这类表情码发出去后会变成图标，对比时要去掉。"""
    return re.sub(r"\[[^\[\]\n]{1,16}\]", "", snippet or "").strip()


def _text_has_snippet(text: str, snippet: str) -> bool:
    if not text or not snippet:
        return False
    if snippet in text:
        return True
    bare = _plain_snippet(snippet)
    return bool(bare) and len(bare) >= 2 and bare in text


def _chat_texts(page) -> list[str]:
    try:
        texts = page.evaluate(CHAT_BUBBLE_JS)
    except Exception:
        return []
    if not isinstance(texts, list):
        return []
    return [str(item).strip() for item in texts if str(item).strip()]


def _list_previews(page) -> list[dict]:
    try:
        rows = page.evaluate(LIST_PREVIEW_JS)
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        preview = str(row.get("preview") or "").strip()
        if not title and not preview:
            continue
        out.append(
            {
                "title": title,
                "preview": preview,
                "time": str(row.get("time") or "").strip(),
                "current": bool(row.get("current")),
            }
        )
    return out


def _snippet_hits(texts, snippet: str) -> int:
    if not snippet:
        return 0
    return sum(1 for item in texts if _text_has_snippet(item, snippet))


def _row_for_friend(rows: list[dict], friend_name: str) -> dict | None:
    if friend_name:
        for row in rows:
            if row.get("title") == friend_name:
                return row
    for row in rows:
        if row.get("current"):
            return row
    return None


def _preview_updated(before_row: dict | None, after_row: dict | None, snippet: str) -> bool:
    """预览正文必须变。点开会话也会把时间改成「刚刚」，那不算发出去。"""
    if not after_row:
        return False
    after_preview = after_row.get("preview") or ""
    before_preview = (before_row or {}).get("preview") or ""
    if not after_preview or after_preview == before_preview:
        return False
    return _text_has_snippet(after_preview, snippet)


def _other_conversation_title(rows: list[dict], friend_name: str) -> str:
    for row in rows:
        title = str(row.get("title") or "").strip()
        if title and title != friend_name:
            return title
    return ""


def _click_conversation_title(page, title: str) -> bool:
    if not title:
        return False
    try:
        return bool(page.evaluate(CLICK_CONVERSATION_JS, title))
    except TypeError:
        return False
    except Exception:
        logger.debug("点开会话 %s 失败", title, exc_info=True)
        return False


def _reveal_hidden_preview(page, friend_name: str) -> dict | None:
    """当前会话被点开后，列表预览经常被收成空的。点一下别人再读回来。"""
    rows = _list_previews(page)
    other = _other_conversation_title(rows, friend_name)
    if not other:
        return _row_for_friend(rows, friend_name)
    if not _click_conversation_title(page, other):
        return _row_for_friend(rows, friend_name)
    logger.info("发送核对 %s 当前预览被收空，点开 %s 后再读", friend_name or "?", other)
    time.sleep(0.4)
    return _row_for_friend(_list_previews(page), friend_name)


def _finish_send_result(username: str, sent: list, failed: list, blocked: str = ""):
    if blocked:
        raise RuntimeError(
            f"账号 {username} 被抖音限制，成功 {len(sent)} 个后中断：{blocked}"
        )
    if not sent:
        raise RuntimeError(
            f"账号 {username} 一条消息都没发出去，聊天输入框可能已经改版，已保存截图到 logs/chat-debug-*.png"
        )
    if failed:
        raise RuntimeError(
            f"账号 {username} 部分好友没发出去：成功 {len(sent)}，失败 {len(failed)}（{'、'.join(failed)}）"
        )


def _click_send_button(page) -> bool:
    loc, _, selector = _find_locator(page, SEND_BUTTON_SELECTORS)
    if loc is None:
        return False
    try:
        loc.first.click(timeout=1500)
        logger.debug("回车没清空，改点发送按钮 selector=%s", selector)
        return True
    except Exception:
        return False


def _send_chat_message(page, message: str, send_records=None, friend_name=""):
    chat_input, _, selector = _wait_locator(page, CHAT_EDITOR_SELECTORS, timeout_ms=15000)
    if chat_input is None:
        raise RuntimeError("找不到聊天输入框，会话可能没打开")
    logger.debug("使用输入框 selector=%s", selector)
    _live(
        page,
        friend=friend_name,
        phase="sending",
        caption=f"正在给 {friend_name or '好友'} 发消息",
        force=True,
    )
    editor = _editor_target(page, chat_input)
    lines = [part for part in message.replace("\\n", "\n").split("\n")]
    snippet = _message_snippet(message)
    before_row = _row_for_friend(_list_previews(page), friend_name)
    before_chat = _chat_texts(page)

    # 回车之前可以重试：还没发出去，重来一次不会造成重复
    typed = False
    for attempt in range(2):
        if send_records is not None:
            send_records.clear()
        _type_message(page, editor, lines)
        if _editor_text(editor):
            typed = True
            break
        logger.warning("输入框没收到文字，第 %s 次重试", attempt + 1)
    if not typed:
        raise RuntimeError("消息没能发出去：文字没有进入输入框")

    # 回车之后一律不再重发。此刻消息可能已经送达，重试就会让好友收到两条
    _press_enter(page, editor)
    cleared = False
    landed = False
    clicked_send = False
    after_row = before_row
    after_chat = before_chat
    for _ in range(24):
        time.sleep(0.15)
        if not _editor_text(editor):
            cleared = True
        after_row = _row_for_friend(_list_previews(page), friend_name)
        after_chat = _chat_texts(page)
        if _preview_updated(before_row, after_row, snippet):
            landed = True
            break
        if _snippet_hits(after_chat, snippet) > _snippet_hits(before_chat, snippet):
            landed = True
            break
        if not cleared and not clicked_send:
            clicked_send = _click_send_button(page)

    time.sleep(0.6)
    after_row = _row_for_friend(_list_previews(page), friend_name)
    after_chat = _chat_texts(page)
    if _preview_updated(before_row, after_row, snippet):
        landed = True
    if _snippet_hits(after_chat, snippet) > _snippet_hits(before_chat, snippet):
        landed = True
    # 点开当前会话后，抖音常把这一行预览收成空的。静静靠气泡过了，凯凯气泡也是 0。
    if not landed and friend_name and not ((after_row or {}).get("preview") or "").strip():
        _live(
            page,
            friend=friend_name,
            phase="reveal",
            caption=f"{friend_name} 当前预览被收空，点开别人再核对",
            before=(before_row or {}).get("preview") or "",
            after="",
            force=True,
        )
        revealed = _reveal_hidden_preview(page, friend_name)
        if revealed:
            after_row = revealed
        if _preview_updated(before_row, after_row, snippet):
            landed = True
        after_chat = _chat_texts(page)
        if _snippet_hits(after_chat, snippet) > _snippet_hits(before_chat, snippet):
            landed = True
    logger.info(
        "发送核对 %s 预览「%s」→「%s」 气泡 %s→%s",
        friend_name or "?",
        ((before_row or {}).get("preview") or "")[:40],
        ((after_row or {}).get("preview") or "")[:40],
        _snippet_hits(before_chat, snippet),
        _snippet_hits(after_chat, snippet),
    )
    before_txt = (before_row or {}).get("preview") or ""
    after_txt = (after_row or {}).get("preview") or ""
    api_error = _send_failure_from_api(list(send_records or []))
    if api_error:
        _live(
            page,
            friend=friend_name,
            phase="fail",
            result="fail",
            caption=f"{friend_name or '好友'} 被抖音拒绝",
            before=before_txt,
            after=after_txt,
            force=True,
        )
        raise RuntimeError(f"抖音拒绝了这条消息：{api_error}")
    toast = _toast_warning(page)
    if toast:
        _live(
            page,
            friend=friend_name,
            phase="fail",
            result="fail",
            caption=f"{friend_name or '好友'} 出现提示：{toast}",
            before=before_txt,
            after=after_txt,
            force=True,
        )
        raise RuntimeError(f"抖音提示：{toast}")
    if landed:
        _live(
            page,
            friend=friend_name,
            phase="ok",
            result="ok",
            caption=f"{friend_name or '好友'} 预览已更新",
            before=before_txt,
            after=after_txt,
            force=True,
        )
        return
    if not cleared:
        _live(
            page,
            friend=friend_name,
            phase="fail",
            result="fail",
            caption=f"{friend_name or '好友'} 输入框没清空",
            before=before_txt,
            after=after_txt,
            force=True,
        )
        raise RuntimeError("消息可能没发出去：回车后输入框内容没有被清空")
    logger.warning(
        "发送核对失败 friend=%s before=%s after=%s",
        friend_name or "?",
        before_txt,
        after_txt,
    )
    if after_row or after_chat:
        _live(
            page,
            friend=friend_name,
            phase="fail",
            result="fail",
            caption=f"{friend_name or '好友'} 预览没有更新",
            before=before_txt,
            after=after_txt,
            force=True,
        )
        raise RuntimeError("消息可能没发出去：这个好友的会话预览没有更新")
    logger.warning("聊天区没抓到气泡，只能按输入框已清空判断，核对片段=%s", snippet)
    _live(
        page,
        friend=friend_name,
        phase="checking",
        caption=f"{friend_name or '好友'} 没抓到预览，只看到输入框已清空",
        before=before_txt,
        after=after_txt,
        force=True,
    )


def _proxy_off(region: str) -> bool:
    """总开关关掉、或这个账号没设地区 —— 都当作纯直连，一点 IP 逻辑都不碰。"""
    if not str(region or "").strip():
        return True
    try:
        from webui.proxy import proxy_enabled

        return not proxy_enabled()
    except Exception:
        logger.exception("读取代理开关失败，保险起见按直连处理")
        return True


def _account_proxy(username: str, region: str):
    """没开代理总开关、或没设地区的账号返回 None 走直连，绝不用全国随机 IP 顶替。"""
    if _proxy_off(region):
        return None
    try:
        from webui.proxy import lease_proxy
        from webui.regions import area_label

        lease = lease_proxy(region)
        if lease:
            logger.info("账号 %s 使用代理 %s（%s）", username, lease.server, area_label(region))
        else:
            logger.warning("账号 %s 没能拿到代理 IP，本次走直连", username)
        return lease
    except Exception:
        logger.exception("账号 %s 提取代理出错，本次走直连", username)
        return None


class _ChatListUnavailable(RuntimeError):
    """会话列表没能打开——多半是这条代理 IP 太慢/太差，换一条再试就好。

    专门跟「扫码墙」「被限流」区分开：那两种换 IP 也没用，不该重试。
    """


def _proxy_ip_tries(region: str) -> int:
    """开了代理才有「换 IP 重试」这回事；总开关关掉或直连，只试一次。"""
    if _proxy_off(region):
        return 1
    try:
        raw = int(config.get("proxyIpTries") or 3)
    except (TypeError, ValueError):
        raw = 3
    return max(1, min(raw, 5))


def do_user_task(username, cookies, targets, message_template="", unique_id="", region=""):
    user_id_dict = {}
    _preview_bind(username, unique_id)
    _live(
        None,
        account=username,
        unique_id=unique_id,
        phase="opening",
        caption=f"正在打开 {username} 的私信页",
    )
    playwright, browser = get_browser()
    spark_seen: dict[str, dict] = {}
    spark_error = []

    def harvest_sparks(page):
        """顺手记一次当前视口的火花天数。列表本来就要滚，这是白拿的数据，绝不能让它影响发送。"""
        try:
            from webui.chat_list import _collect_dom

            for row in _collect_dom(page) or []:
                name = str((row or {}).get("name") or "").strip()
                if name and (row or {}).get("spark_days"):
                    spark_seen[name] = row
        except Exception as exc:
            # 每个好友都会扫一次，出错只记第一次，不然 DEBUG 日志会被刷屏
            if not spark_error:
                spark_error.append(str(exc))

    def save_sparks():
        if not unique_id:
            return
        if not spark_seen:
            logger.info("账号 %s 这次没读到火花天数%s", username, f"：{spark_error[0]}" if spark_error else "")
            return
        try:
            from webui.chat_list import update_spark_snapshot

            n = update_spark_snapshot(unique_id, list(spark_seen.values()))
            logger.info("账号 %s 已刷新 %s 个会话的火花天数", username, n)
        except Exception:
            logger.exception("账号 %s 刷新火花天数失败", username)

    from webui.session_store import load_state_path, save_state

    def _attempt(lease, proxy, dump_debug):
        """用给定的这条 IP 跑一遍：打开私信页→等会话列表→逐个发送。

        会话列表打不开就抛 _ChatListUnavailable，交给外层换一条 IP 重试；
        扫码墙 / 被限流 / 输入框改版则直接抛普通错误，换 IP 也没用、不重试。

        dump_debug=False 时（还要换 IP 重试）只记一行 URL，不截图——
        截图在死代理上要等满超时，几条 IP 试下来光截图就烧一两分钟。
        """
        state = load_state_path(unique_id)
        try:
            context = make_context(browser, storage_state=state, cookies=cookies, proxy=proxy)
        except Exception:
            if not proxy:
                raise
            logger.exception("账号 %s 用代理建上下文失败，改走直连", username)
            context = make_context(browser, storage_state=state, cookies=cookies)
        try:
            context.set_default_navigation_timeout(config["browserTimeout"])
            # 走代理时私信这个重型 IM 应用每一步都更慢，默认超时也得放宽，
            # 否则找输入框、读文字、截图动不动就先超时了。
            slow = bool(proxy)
            context.set_default_timeout(12000 if slow else 8000)
            page = context.new_page()
            page.on("response", _make_info_handler(user_id_dict))
            send_records = []
            page.on("response", _make_send_handler(send_records))

            opened = False
            last_nav_err = None
            for chat_url in ("https://www.douyin.com/chat", "https://www.douyin.com/chat?isPopup=1"):
                try:
                    retry_operation(
                        "打开抖音网页聊天页面",
                        page.goto,
                        retries=config["taskRetryTimes"],
                        delay=2,
                        url=chat_url,
                        # 默认是等 load（连图片都要加载完），私信页这么重的应用走代理根本等不到。
                        # 只等响应头，真正要等的会话列表下面有 _wait_locator 专门盯着。
                        wait_until="commit",
                    )
                    opened = True
                    break
                except Exception as exc:
                    last_nav_err = exc
                    logger.warning("账号 %s 打开 %s 失败：%s", username, chat_url, exc)
            if not opened:
                # 连响应头都等不到，这条 IP 基本是死的——换一条再来，别在这条上耗
                raise _ChatListUnavailable(
                    f"账号 {username} 这条代理连私信页都打不开：{last_nav_err}"
                ) from last_nav_err
            # commit 只保证开始接收文档，给页面一点时间把内容渲染出来，
            # 否则下面这句在空 body 上判断，登录墙会认不出来
            time.sleep(4 if slow else 2.5)
            if _looks_like_login(page):
                _live(page, phase="fail", caption=f"{username} 打开的是登录页", force=True)
                _dump_chat_debug(page, username)
                raise RuntimeError(f"账号 {username} 打开私信页失败：页面在要求登录，请点「检测」或重新登录后再续火花")
            if _looks_like_challenge(page):
                raise _ChatListUnavailable(
                    f"账号 {username} 私信页在做安全验证，换一条 IP 再试"
                )

            # 会话列表是抖音私信里最重的一块，走代理时渲染很慢。检测那边给到 35 秒才判「没列表」，
            # 这边只给 15 秒就会出现「检测说正常、续火花却打不开列表」。代理下放宽到 40 秒对齐。
            item_loc, scope, item_sel = _wait_locator(
                page, CONVERSATION_ITEM_SELECTORS, timeout_ms=40000 if slow else 20000
            )
            list_loc, _, list_sel = _find_locator(page, CONVERSATION_LIST_SELECTORS)
            if item_loc is None:
                try:
                    page.goto("https://www.douyin.com/chat?isPopup=1", wait_until="commit")
                    time.sleep(3 if slow else 2)
                    item_loc, scope, item_sel = _wait_locator(
                        page, CONVERSATION_ITEM_SELECTORS, timeout_ms=20000 if slow else 12000
                    )
                    list_loc, _, list_sel = _find_locator(page, CONVERSATION_LIST_SELECTORS)
                except Exception as exc:
                    logger.warning("账号 %s 回退弹层私信失败：%s", username, exc)
            if item_loc is None:
                if dump_debug:
                    _dump_chat_debug(page, username)
                else:
                    logger.warning("账号 %s 这条 IP 没渲染出会话列表 url=%s", username, getattr(page, "url", ""))
                raise _ChatListUnavailable(
                    f"账号 {username} 打不开会话列表（这条 IP 太慢，页面没渲染出好友列表）"
                )
            logger.info("账号 %s 已找到会话列表 item=%s list=%s", username, item_sel, list_sel or "父级滚动")
            _live(page, phase="list", caption=f"{username} 已打开会话列表", force=True)
            if unique_id:
                save_state(context, unique_id)
            # 火焰图标是懒加载的，等一下再扫，否则列表刚出来时天数还没渲染出来
            try:
                page.wait_for_selector(
                    ".commonStreaknormalText, img.commonStreakicon, img[src*='flame_icon']",
                    timeout=9000 if slow else 5000,
                )
            except Exception:
                pass
            harvest_sparks(page)

            logger.debug(f"账号 {username} 开始发送消息")
            message = build_message(message_template)
            logger.info(
                "账号 %s 本轮文案：%s",
                username,
                message.replace("\n", " / ").replace("\\n", " / ")[:160],
            )
            sent = []
            failed = []
            blocked = ""
            for target_symbol, friend_name in scroll_and_select_user(
                page, username, targets, user_id_dict, item_loc, list_loc, scope
            ):
                # 代理到点就收手，剩下的好友留给下次。硬撑只会在断网里空转，已发出去的还得算数
                if lease and lease.expired():
                    logger.warning(
                        "账号 %s 代理已用满 %s 分钟，本次发到第 %s 个为止，剩下的等下次",
                        username, lease.minutes, len(sent),
                    )
                    break
                # 找好友的过程本身就在滚列表，每滚到一个人就把当前视口的天数一起收了
                harvest_sparks(page)
                logger.debug(f"账号 {username} 已选中好友 {friend_name} 发送消息")
                logger.debug(f"账号 {username} 准备发送消息给好友 {friend_name}：\n\t{message}")
                try:
                    _live(
                        page,
                        friend=friend_name,
                        phase="sending",
                        caption=f"{username} 已选中 {friend_name}",
                        force=True,
                    )
                    _send_chat_message(page, message, send_records, friend_name=friend_name)
                except Exception as exc:
                    failed.append(friend_name)
                    logger.error(f"账号 {username} 给好友 {friend_name} 发送失败：{exc}")
                    if _looks_blocked(exc):
                        blocked = str(exc)
                        logger.error("账号 %s 疑似被抖音限制，停止本次发送：%s", username, blocked)
                        break
                    continue
                sent.append(friend_name)
                logger.info(f"账号 {username} 给好友 {friend_name} 发送成功")
                time.sleep(_send_gap())

            harvest_sparks(page)
            logger.info("账号 %s 发送结果 成功=%s 失败=%s", username, len(sent), len(failed))
            if failed:
                logger.warning("账号 %s 以下好友没发出去: %s", username, failed)
            if blocked or not sent:
                _dump_chat_debug(page, username)
            _finish_send_result(username, sent, failed, blocked)
        finally:
            try:
                context.close()
            except Exception:
                pass

    try:
        tries = _proxy_ip_tries(region)
        last_err: _ChatListUnavailable | None = None
        for ip_try in range(1, tries + 1):
            lease = _account_proxy(username, region)
            proxy = lease.server if lease else None
            try:
                _attempt(lease, proxy, dump_debug=ip_try == tries)
                return  # 成功，收工
            except _ChatListUnavailable as exc:
                last_err = exc
                if ip_try < tries:
                    logger.warning(
                        "账号 %s 第 %s/%s 条 IP 打不开会话列表，换一条新 IP 重试：%s",
                        username, ip_try, tries, exc,
                    )
            finally:
                # 每一条 IP 用完当场登记收手，再去取下一条，绝不同时占着两条
                if lease:
                    lease.release(f"账号 {username}（第 {ip_try} 条 IP）")
        # 几条都打不开：这才是真失败，给出带排障截图的最终错误
        if tries > 1:
            raise RuntimeError(
                f"账号 {username} 连换 {tries} 条代理 IP 都打不开会话列表。"
                f"多半是这批住宅 IP 太慢，稍后重试或换个地区；已截图到 logs/chat-debug-*.png"
            ) from last_err
        raise RuntimeError(
            f"账号 {username} 打不开会话列表。不是扫码失败，是私信页没有出现好友列表，已截图到 logs/chat-debug-*.png"
        ) from last_err
    finally:
        # 放在最外层 finally：中途被限流或报错也要把已经扫到的天数留下来，它不依赖页面还开着
        save_sparks()
        try:
            browser.close()
        except Exception:
            pass
        try:
            playwright.stop()
        except Exception:
            pass


def _max_task_threads(n_users: int) -> int:
    try:
        raw = int(config.get("maxTaskThreads") or 10)
    except (TypeError, ValueError):
        raw = 10
    return max(1, min(raw, 32, max(n_users, 1)))


def _run_one_account(user: dict):
    username = user.get("username", "未知用户")
    logger.info(f"开始处理账号 {username}")
    do_user_task(
        username,
        user["cookies"],
        user["targets"],
        user.get("messageTemplate") or "",
        unique_id=str(user.get("unique_id") or ""),
        region=str(user.get("region") or ""),
    )
    logger.info(f"账号 {username} 任务完成")


def runTasks():
    global config
    config = get_config()
    user_data = get_userData()
    try:
        from webui.run_preview import begin_run

        begin_run([str(user.get("unique_id") or "") for user in user_data])
    except Exception:
        logger.debug("实时预览开场失败", exc_info=True)
    logger.info("开始执行任务")
    logger.debug("当前配置如下：")
    logger.debug(f"消息模板: {config.get('messageTemplate', '未找到消息模板')}")
    logger.debug(f"一言类型: {config['hitokotoTypes']}")
    for user in user_data:
        logger.debug(
            f"用户: {user.get('username', '未知用户')}, 目标好友: {user['targets']}"
        )

    try:
        if not user_data:
            logger.warning("没有可执行的账号任务")
            return

        workers = _max_task_threads(len(user_data))
        logger.info("并发执行账号任务 accounts=%s threads=%s", len(user_data), workers)
        errors = []
        if workers == 1 or len(user_data) == 1:
            for user in user_data:
                try:
                    _run_one_account(user)
                except Exception:
                    username = user.get("username", "未知用户")
                    logger.exception("账号 %s 任务失败", username)
                    errors.append(username)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="spark") as pool:
                futures = {pool.submit(_run_one_account, user): user for user in user_data}
                for future in as_completed(futures):
                    user = futures[future]
                    username = user.get("username", "未知用户")
                    try:
                        future.result()
                    except Exception:
                        logger.exception("账号 %s 任务失败", username)
                        errors.append(username)

        if errors:
            raise RuntimeError("部分账号任务失败: " + ", ".join(errors))
    finally:
        try:
            from webui.run_preview import finish_run

            finish_run()
        except Exception:
            logger.debug("实时预览收尾失败", exc_info=True)
