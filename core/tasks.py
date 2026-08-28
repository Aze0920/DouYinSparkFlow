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
]
CONVERSATION_ITEM_SELECTORS = [
    CONVERSATION_ITEM_SELECTOR,
    '[data-e2e="conversation-item"]',
    "[class*='ConversationItemwrapper']",
    "[class*='conversationItemwrapper']",
    "[class*='ConversationItem']",
    "[class*='conversation-item']",
    "[class*='conversationItem']",
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
LOGIN_HINTS = ("扫码登录", "登录后免费畅享", "打开「抖音APP」", "验证码登录", "请使用抖音APP")


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
        page.screenshot(path=str(path), full_page=False)
        logger.warning("账号 %s 已保存页面截图 %s", username, path)
    except Exception:
        logger.debug("账号 %s 保存截图失败", username, exc_info=True)


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
    try:
        return (editor.evaluate("el => el.innerText || el.textContent || ''") or "").strip()
    except Exception:
        return ""


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


def _send_chat_message(page, message: str):
    chat_input, _, selector = _wait_locator(page, CHAT_EDITOR_SELECTORS, timeout_ms=15000)
    if chat_input is None:
        raise RuntimeError("找不到聊天输入框，会话可能没打开")
    logger.debug("使用输入框 selector=%s", selector)
    editor = _editor_target(page, chat_input)
    lines = message.split("\\n")
    last_error = "未知原因"
    for attempt in range(2):
        _type_message(page, editor, lines)
        if not _editor_text(editor):
            last_error = "文字没有进入输入框"
            logger.warning("输入框没收到文字，第 %s 次重试", attempt + 1)
            continue
        page.keyboard.press("Enter")
        for _ in range(20):
            time.sleep(0.15)
            if not _editor_text(editor):
                return
        last_error = "回车后输入框内容没有被清空"
        logger.warning("消息可能没发出去，第 %s 次重试", attempt + 1)
    raise RuntimeError(f"消息没能发出去：{last_error}")


def do_user_task(username, cookies, targets, message_template="", unique_id=""):
    user_id_dict = {}
    playwright, browser = get_browser()
    context = None
    try:
        from webui.session_store import load_state_path, save_state

        context = make_context(browser, storage_state=load_state_path(unique_id), cookies=cookies)
        context.set_default_navigation_timeout(config["browserTimeout"])
        context.set_default_timeout(8000)
        page = context.new_page()
        page.on("response", _make_info_handler(user_id_dict))

        retry_operation(
            "打开抖音网页聊天页面",
            page.goto,
            retries=config["taskRetryTimes"],
            delay=2,
            url="https://www.douyin.com/chat",
        )
        time.sleep(0.8)
        if _looks_like_login(page):
            _dump_chat_debug(page, username)
            raise RuntimeError(f"账号 {username} 打开私信页失败：页面在要求登录，请点「检测」或重新登录后再续火花")

        item_loc, scope, item_sel = _wait_locator(page, CONVERSATION_ITEM_SELECTORS, timeout_ms=15000)
        list_loc, _, list_sel = _find_locator(page, CONVERSATION_LIST_SELECTORS)
        if item_loc is None:
            _dump_chat_debug(page, username)
            raise RuntimeError(
                f"账号 {username} 打不开会话列表。不是扫码失败，是私信页没有出现好友列表，已截图到 logs/chat-debug-*.png"
            )
        logger.info("账号 %s 已找到会话列表 item=%s list=%s", username, item_sel, list_sel or "父级滚动")
        if unique_id:
            save_state(context, unique_id)

        logger.debug(f"账号 {username} 开始发送消息")
        sent = []
        failed = []
        for target_symbol, friend_name in scroll_and_select_user(
            page, username, targets, user_id_dict, item_loc, list_loc, scope
        ):
            logger.debug(f"账号 {username} 已选中好友 {friend_name} 发送消息")
            message = build_message(message_template)
            logger.debug(f"账号 {username} 准备发送消息给好友 {friend_name}：\n\t{message}")
            try:
                _send_chat_message(page, message)
            except Exception as exc:
                failed.append(friend_name)
                logger.error(f"账号 {username} 给好友 {friend_name} 发送失败：{exc}")
                continue
            sent.append(friend_name)
            logger.info(f"账号 {username} 给好友 {friend_name} 发送成功")
            time.sleep(0.5)

        logger.info("账号 %s 发送结果 成功=%s 失败=%s", username, len(sent), len(failed))
        if failed:
            logger.warning("账号 %s 以下好友没发出去: %s", username, failed)
        if not sent:
            _dump_chat_debug(page, username)
            raise RuntimeError(
                f"账号 {username} 一条消息都没发出去，聊天输入框可能已经改版，已保存截图到 logs/chat-debug-*.png"
            )
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
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
    )
    logger.info(f"账号 {username} 任务完成")


def runTasks():
    global config
    config = get_config()
    user_data = get_userData()
    logger.info("开始执行任务")
    logger.debug("当前配置如下：")
    logger.debug(f"消息模板: {config.get('messageTemplate', '未找到消息模板')}")
    logger.debug(f"一言类型: {config['hitokotoTypes']}")
    for user in user_data:
        logger.debug(
            f"用户: {user.get('username', '未知用户')}, 目标好友: {user['targets']}"
        )

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
