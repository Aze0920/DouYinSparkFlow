import unittest

import core.tasks as tasks


class FakeKeyboard:
    def __init__(self, editor):
        self.editor = editor
        self.presses = []

    def insert_text(self, text):
        self.editor.text += text

    def press(self, key):
        self.presses.append(key)
        if key == "Delete":
            self.editor.text = ""
        elif key == "Enter":
            self.editor.on_enter()


class FakeEditor:
    """模拟 contenteditable：回车后被前端清空才算真的发出去了。"""

    def __init__(self, sends=True, api_reply=None, records=None, shows_in_chat=True):
        self.text = ""
        self.sends = sends
        self.api_reply = api_reply
        self.records = records
        self.shows_in_chat = shows_in_chat
        self.sent = []
        self.first = self
        self.page = None

    def click(self):
        pass

    def press(self, key):
        if self.page is not None:
            self.page.keyboard.press(key)
        elif key == "Enter":
            self.on_enter()

    def count(self):
        return 1

    def locator(self, _selector):
        return self

    def get_attribute(self, name):
        return "true" if name == "contenteditable" else None

    def evaluate(self, script):
        if "focus" in script:
            return None
        return self.text

    def on_enter(self):
        if self.sends and self.text:
            self.sent.append(self.text)
            if self.shows_in_chat and self.page is not None:
                self.page.chat_texts.append(self.text)
            self.text = ""
        # 接口响应总是在回车之后才回来
        if self.api_reply is not None and self.records is not None:
            self.records.append(self.api_reply)


class FakeEmpty:
    def count(self):
        return 0

    @property
    def first(self):
        return self

    def click(self, timeout=0):
        raise RuntimeError("no send button")


class FakePage:
    def __init__(self, editor):
        self.editor = editor
        self.keyboard = FakeKeyboard(editor)
        self.chat_texts = []
        self.previews = []
        self.frames = []
        editor.page = self

    def locator(self, _selector):
        return FakeEmpty()

    def evaluate(self, script):
        text = str(script or "")
        if "ConversationItemwrapper" in text or "conversation-item" in text:
            return list(self.previews)
        return list(self.chat_texts)


class FakeResponse:
    def __init__(self, url, status=200, payload=None):
        self.url = url
        self.status = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _patched(editor, toast=""):
    page = FakePage(editor)
    tasks._wait_locator = lambda *a, **k: (editor, page, ".fake")
    tasks._toast_warning = lambda _page: toast
    return page


class SendChatMessageTests(unittest.TestCase):
    def setUp(self):
        self._wait_locator = tasks._wait_locator
        self._toast_warning = tasks._toast_warning
        self._sleep = tasks.time.sleep
        tasks.time.sleep = lambda *_: None

    def tearDown(self):
        tasks._wait_locator = self._wait_locator
        tasks._toast_warning = self._toast_warning
        tasks.time.sleep = self._sleep

    def test_sends_message_and_splits_literal_newlines(self):
        editor = FakeEditor()
        page = _patched(editor)
        tasks._send_chat_message(page, "第一行\\n第二行")
        self.assertEqual(editor.sent, ["第一行第二行"])
        self.assertIn("Shift+Enter", page.keyboard.presses)

    def test_raises_when_editor_never_clears(self):
        editor = FakeEditor(sends=False)
        page = _patched(editor)
        with self.assertRaises(RuntimeError) as ctx:
            tasks._send_chat_message(page, "你好")
        self.assertIn("没发出去", str(ctx.exception))

    def test_never_presses_enter_twice(self):
        """回车之后重发会让好友收到两条，任何情况下都只能按一次。"""
        editor = FakeEditor(sends=False)
        page = _patched(editor)
        with self.assertRaises(RuntimeError):
            tasks._send_chat_message(page, "你好")
        self.assertEqual(page.keyboard.presses.count("Enter"), 1)

    def test_stray_placeholder_chars_do_not_look_like_leftover_text(self):
        """清空后残留零宽字符时不能误判成没发出去。"""
        editor = FakeEditor()
        page = _patched(editor)

        def on_enter():
            page.chat_texts.append("你好")
            editor.text = "\u200b\n"

        editor.on_enter = on_enter
        tasks._send_chat_message(page, "你好")
        self.assertEqual(page.keyboard.presses.count("Enter"), 1)

    def test_raises_when_text_never_lands_in_editor(self):
        editor = FakeEditor()
        page = _patched(editor)
        page.keyboard.insert_text = lambda _text: None
        with self.assertRaises(RuntimeError) as ctx:
            tasks._send_chat_message(page, "你好")
        self.assertIn("没能发出去", str(ctx.exception))

    def test_missing_editor_raises(self):
        page = FakePage(FakeEditor())
        tasks._wait_locator = lambda *a, **k: (None, page, "")
        with self.assertRaises(RuntimeError):
            tasks._send_chat_message(page, "你好")

    def test_editor_cleared_but_api_rejected_is_a_failure(self):
        """风控时抖音照样清空输入框，只有接口返回能拆穿。"""
        records = []
        editor = FakeEditor(
            api_reply={"url": "https://imapi.douyin.com/v1/message/send", "http": 200, "code": 3, "msg": "操作过于频繁"},
            records=records,
        )
        page = _patched(editor)
        with self.assertRaises(RuntimeError) as ctx:
            tasks._send_chat_message(page, "你好", records)
        self.assertIn("操作过于频繁", str(ctx.exception))
        self.assertEqual(editor.sent, ["你好"])

    def test_editor_cleared_but_toast_warns_is_a_failure(self):
        editor = FakeEditor()
        page = _patched(editor, toast="操作频繁，请稍后再试")
        with self.assertRaises(RuntimeError) as ctx:
            tasks._send_chat_message(page, "你好", [])
        self.assertIn("操作频繁", str(ctx.exception))

    def test_clean_api_response_still_succeeds(self):
        records = []
        editor = FakeEditor(
            api_reply={"url": "https://imapi.douyin.com/v1/message/send", "http": 200, "code": 0, "msg": ""},
            records=records,
        )
        page = _patched(editor)
        tasks._send_chat_message(page, "你好", records)
        self.assertEqual(editor.sent, ["你好"])

    def test_succeeds_when_no_api_response_captured(self):
        """抖音私信主要走 WebSocket，抓不到 HTTP 响应时看聊天区气泡。"""
        editor = FakeEditor()
        page = _patched(editor)
        tasks._send_chat_message(page, "你好", [])
        self.assertEqual(editor.sent, ["你好"])

    def test_editor_cleared_but_chat_missing_is_failure(self):
        """输入框空了但对话里没出现这条，就是假成功。"""
        editor = FakeEditor(shows_in_chat=False)
        page = _patched(editor)
        page.chat_texts = ["昨天的旧消息"]
        with self.assertRaises(RuntimeError) as ctx:
            tasks._send_chat_message(page, "你好")
        self.assertIn("会话预览没有更新", str(ctx.exception))

    def test_snippet_skips_decoration_lines(self):
        snippet = tasks._message_snippet(
            "[盖瑞]今日火花[加一]\\n—— [右边] 每日一言 [左边] ——\\n海内存知己"
        )
        self.assertEqual(snippet, "[盖瑞]今日火花[加一]")

    def test_list_preview_counts_as_delivered(self):
        """抖音聊天气泡会把 [盖瑞] 收成表情，但左侧会话预览仍留原文。"""
        editor = FakeEditor(shows_in_chat=False)
        page = _patched(editor)
        page.chat_texts = ["昨天的旧消息"]
        page.previews = [{"title": "王洁", "preview": "昨天聊的", "time": "02:40", "current": True}]

        def on_enter():
            editor.sent.append(editor.text)
            editor.text = ""
            page.previews = [
                {"title": "王洁", "preview": "[盖瑞]今日火花[加一]", "time": "刚刚", "current": True}
            ]

        editor.on_enter = on_enter
        tasks._send_chat_message(page, "[盖瑞]今日火花[加一]", friend_name="王洁")
        self.assertEqual(editor.sent, ["[盖瑞]今日火花[加一]"])

    def test_emoji_codes_still_match_plain_preview(self):
        self.assertTrue(tasks._text_has_snippet("刚刚 今日火花", "[盖瑞]今日火花[加一]"))

    def test_other_friends_same_preview_does_not_count(self):
        """前面好友已经发出同一条文案时，不能拿别人的预览给当前好友凑成功。"""
        editor = FakeEditor(shows_in_chat=False)
        page = _patched(editor)
        page.chat_texts = ["昨天的旧消息"]
        page.previews = [
            {"title": "静静", "preview": "[盖瑞]今日火花[加一]", "time": "刚刚", "current": False},
            {"title": "凯凯", "preview": "昨天晚上见", "time": "02:40", "current": True},
        ]
        with self.assertRaises(RuntimeError) as ctx:
            tasks._send_chat_message(page, "[盖瑞]今日火花[加一]", friend_name="凯凯")
        self.assertIn("会话预览没有更新", str(ctx.exception))

    def test_just_now_without_preview_change_is_not_success(self):
        """点开会话也会变成「刚刚」，正文没变就不能算发出去。"""
        editor = FakeEditor(shows_in_chat=False)
        page = _patched(editor)
        page.chat_texts = ["昨天的旧消息"]
        page.previews = [
            {"title": "超欣", "preview": "[盖瑞]今日火花[加一]", "time": "02:40", "current": True}
        ]

        def on_enter():
            editor.sent.append(editor.text)
            editor.text = ""
            page.previews = [
                {"title": "超欣", "preview": "[盖瑞]今日火花[加一]", "time": "刚刚", "current": True}
            ]

        editor.on_enter = on_enter
        with self.assertRaises(RuntimeError) as ctx:
            tasks._send_chat_message(page, "[盖瑞]今日火花[加一]", friend_name="超欣")
        self.assertIn("会话预览没有更新", str(ctx.exception))

    def test_current_friend_just_now_counts(self):
        editor = FakeEditor(shows_in_chat=False)
        page = _patched(editor)
        page.chat_texts = ["昨天的旧消息"]
        page.previews = [
            {"title": "静静", "preview": "[盖瑞]今日火花[加一]", "time": "刚刚", "current": False},
            {"title": "凯凯", "preview": "昨天晚上见", "time": "02:40", "current": True},
        ]

        def on_enter():
            editor.sent.append(editor.text)
            editor.text = ""
            page.previews = [
                {"title": "静静", "preview": "[盖瑞]今日火花[加一]", "time": "刚刚", "current": False},
                {"title": "凯凯", "preview": "[盖瑞]今日火花[加一]", "time": "刚刚", "current": True},
            ]

        editor.on_enter = on_enter
        tasks._send_chat_message(page, "[盖瑞]今日火花[加一]", friend_name="凯凯")
        self.assertEqual(editor.sent, ["[盖瑞]今日火花[加一]"])


class FinishSendResultTests(unittest.TestCase):
    def test_partial_failure_is_not_success(self):
        with self.assertRaises(RuntimeError) as ctx:
            tasks._finish_send_result("王洁", ["儿子", "苗苗"], ["凯凯"])
        self.assertIn("部分好友没发出去", str(ctx.exception))
        self.assertIn("凯凯", str(ctx.exception))

    def test_all_sent_is_silent(self):
        tasks._finish_send_result("王洁", ["儿子", "凯凯"], [])


class SendHandlerTests(unittest.TestCase):
    def _record(self, url, status=200, payload=None):
        store = []
        tasks._make_send_handler(store)(FakeResponse(url, status, payload))
        return store

    def test_ignores_unrelated_requests(self):
        self.assertEqual(self._record("https://www.douyin.com/aweme/v1/web/im/user/info"), [])

    def test_captures_status_code_and_message(self):
        store = self._record(
            "https://imapi.douyin.com/v1/message/send",
            payload={"status_code": 3, "status_msg": "操作过于频繁"},
        )
        self.assertEqual(store[0]["code"], 3)
        self.assertEqual(store[0]["msg"], "操作过于频繁")

    def test_non_json_response_is_still_recorded(self):
        store = self._record("https://imapi.douyin.com/v1/message/send", status=500)
        self.assertEqual(store[0]["http"], 500)

    def test_failure_summary_flags_bad_code_and_bad_http(self):
        self.assertIn("status_code=3", tasks._send_failure_from_api([{"http": 200, "code": 3, "msg": "频繁"}]))
        self.assertIn("HTTP 500", tasks._send_failure_from_api([{"http": 500, "code": None, "msg": ""}]))
        self.assertEqual(tasks._send_failure_from_api([{"http": 200, "code": 0, "msg": ""}]), "")
        self.assertEqual(tasks._send_failure_from_api([]), "")


class SendGapTests(unittest.TestCase):
    def tearDown(self):
        tasks.config.pop("sendMinDelay", None)
        tasks.config.pop("sendMaxDelay", None)

    def test_gap_stays_inside_configured_range(self):
        tasks.config["sendMinDelay"] = 2
        tasks.config["sendMaxDelay"] = 5
        for _ in range(50):
            self.assertTrue(2 <= tasks._send_gap() <= 5)

    def test_gap_falls_back_when_unset(self):
        tasks.config["sendMinDelay"] = 0
        tasks.config["sendMaxDelay"] = 0
        self.assertEqual(tasks._send_gap(), 0.5)


class BlockedDetectionTests(unittest.TestCase):
    def test_only_douyin_refusals_count_as_blocked(self):
        self.assertTrue(tasks._looks_blocked(RuntimeError("抖音拒绝了这条消息：接口返回 status_code=3")))
        self.assertTrue(tasks._looks_blocked(RuntimeError("抖音提示：操作频繁")))
        self.assertFalse(tasks._looks_blocked(RuntimeError("消息没能发出去：文字没有进入输入框")))


if __name__ == "__main__":
    unittest.main()
