import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.logger import setup_logger
from utils.config import get_config, get_userData
from utils import norm
from core.msg_builder import build_message
from core.browser import get_browser
from playwright.sync_api import Response
import time

config = get_config()
logger = setup_logger(level=config.get("logLevel", "Info"))

CONVERSATION_ITEM_SELECTOR = ".conversationConversationItemwrapper"
CONVERSATION_TITLE_SELECTOR = ".conversationConversationItemtitle"
CONVERSATION_LIST_SELECTOR = ".conversationConversationListwrapper"
CHAT_EDITOR_SELECTOR = ".messageEditorimChatEditorContainer"


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


def scroll_and_select_user(page, username, targets, user_id_dict):
    target_selector = CONVERSATION_ITEM_SELECTOR
    scrollable_friends_selector = CONVERSATION_LIST_SELECTOR

    logger.debug(f"账号 {username} 开始查找目标好友列表")
    logger.debug(f"账号 {username} 目标好友列表: {targets}")

    found_targets = set()
    remaining_targets = set(targets)
    empty_scroll_count = 0
    MAX_EMPTY_SCROLLS = 6

    while True:
        target_elements = page.locator(target_selector).all()
        prev_found_count = len(found_targets)

        for element in target_elements:
            try:
                span = element.locator(CONVERSATION_TITLE_SELECTOR)
                targetName = span.inner_text()

                if targetName in found_targets:
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

            scrollable_element = page.locator(scrollable_friends_selector).element_handle()
            if not scrollable_element:
                logger.error(f"账号 {username} 未找到滚动容器，退出")
                break

            scroll_top_before = page.evaluate("(element) => element.scrollTop", scrollable_element)
            page.evaluate("(element) => element.scrollTop += 800", scrollable_element)
            time.sleep(0.25)
            scroll_top_after = page.evaluate("(element) => element.scrollTop", scrollable_element)
            if scroll_top_before == scroll_top_after:
                empty_scroll_count += 2
                logger.debug(
                    f"账号 {username} scrollTop 未变化 ({scroll_top_before})，可能已到底 (空滚动计数: {empty_scroll_count}/{MAX_EMPTY_SCROLLS})"
                )
            else:
                logger.debug(
                    f"账号 {username} 滚动好友列表 (scrollTop: {scroll_top_before} -> {scroll_top_after})"
                )
            time.sleep(0.35)


def _send_chat_message(page, message: str):
    chat_input = page.locator(CHAT_EDITOR_SELECTOR)
    page.wait_for_selector(CHAT_EDITOR_SELECTOR, timeout=config["browserTimeout"])
    chat_input.click()
    lines = message.split("\\n")
    for index, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if index != len(lines) - 1:
            chat_input.press("Shift+Enter")
    chat_input.press("Enter")


def do_user_task(username, cookies, targets):
    user_id_dict = {}
    playwright, browser = get_browser()
    context = None
    try:
        context = browser.new_context()
        context.set_default_navigation_timeout(config["browserTimeout"])
        context.set_default_timeout(config["browserTimeout"])
        page = context.new_page()
        page.on("response", _make_info_handler(user_id_dict))
        context.add_cookies(cookies)

        retry_operation(
            "打开抖音网页聊天页面",
            page.goto,
            retries=config["taskRetryTimes"],
            delay=2,
            url="https://www.douyin.com/chat",
        )
        try:
            page.wait_for_selector(CONVERSATION_LIST_SELECTOR, timeout=8000)
            page.wait_for_selector(CONVERSATION_ITEM_SELECTOR, timeout=5000)
        except Exception:
            logger.warning("账号 %s 会话列表加载较慢，继续尝试发送", username)
        time.sleep(0.4)

        logger.debug(f"账号 {username} 开始发送消息")
        for target_symbol, friend_name in scroll_and_select_user(page, username, targets, user_id_dict):
            logger.debug(f"账号 {username} 已选中好友 {friend_name} 发送消息")
            message = build_message()
            _send_chat_message(page, message)
            logger.debug(f"账号 {username} 准备发送消息给好友 {friend_name}：\n\t{message}")
            logger.debug(f"账号 {username} 给好友 {friend_name} 发送消息完成")
            time.sleep(0.5)
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
    do_user_task(username, user["cookies"], user["targets"])
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
