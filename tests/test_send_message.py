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

    def __init__(self, sends=True, accepts_text=True):
        self.text = ""
        self.sends = sends
        self.accepts_text = accepts_text
        self.sent = []
        self.first = self

    def click(self):
        pass

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
            self.text = ""


class FakePage:
    def __init__(self, editor):
        self.editor = editor
        self.keyboard = FakeKeyboard(editor)


def _patched(editor):
    page = FakePage(editor)
    tasks._wait_locator = lambda *a, **k: (editor, page, ".fake")
    return page


class SendChatMessageTests(unittest.TestCase):
    def setUp(self):
        self._wait_locator = tasks._wait_locator
        self._sleep = tasks.time.sleep
        tasks.time.sleep = lambda *_: None

    def tearDown(self):
        tasks._wait_locator = self._wait_locator
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
        self.assertIn("没能发出去", str(ctx.exception))

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


if __name__ == "__main__":
    unittest.main()
