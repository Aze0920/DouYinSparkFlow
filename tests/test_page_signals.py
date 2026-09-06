"""代理没连通时页面会落到 chrome-error:// 这种不透明源，读 localStorage 会抛 SecurityError。

线上表现：wait_chat_access 每 0.4 秒轮询一次、连轮 25 秒，每次都甩一整屏
`SecurityError: Failed to read the 'localStorage'` 堆栈，看着像程序崩了，其实只是那条代理死了。
"""
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webui import qr_login


class FakePage:
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.calls = 0

    def evaluate(self, _script):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._result


class PageSignalsTests(unittest.TestCase):
    def test_security_error_is_swallowed_quietly(self):
        """opaque-origin 页面读 localStorage 抛错时，_page_signals 只能返回 {}，不能往外抛。"""
        boom = RuntimeError(
            "Page.evaluate: SecurityError: Failed to read the 'localStorage' property"
        )
        page = FakePage(raises=boom)
        self.assertEqual(qr_login._page_signals(page), {})

    def test_evaluate_uses_timeout(self):
        """页面卡死时 evaluate 不设超时，保活会把扫码锁死几十分钟。"""
        source = (ROOT / "webui" / "qr_login.py").read_text(encoding="utf-8")
        start = source.index("def _page_signals")
        chunk = source[start:source.index("def _classify_chat_signals")]
        self.assertIn("timeout=4000", chunk)

    def test_js_reads_localstorage_defensively(self):
        """真正的修法是在 JS 里逐个 try 兜住 localStorage，从源头不抛 SecurityError。"""
        source = (ROOT / "webui" / "qr_login.py").read_text(encoding="utf-8")
        start = source.index("PAGE_SIGNALS_JS")
        end = source.index("def _empty_signals")
        body = source[start:end]
        self.assertIn('try { hasUser = localStorage.getItem', body)
        self.assertIn('try { loginStatus = localStorage.getItem', body)
        self.assertIn("blank", body)
        self.assertIn("shadowRoot", body)
        self.assertIn("hasChallenge", body)

    def test_blank_error_page_bails_out_fast(self):
        """确认落在 chrome-error 时，wait_chat_access 该几轮内收手，而不是死等满超时。"""
        page = FakePage(result={"blank": True, "href": "chrome-error://chromewebdata/"})
        started = time.time()
        state = qr_login.wait_chat_access(page, timeout_s=20)
        elapsed = time.time() - started
        self.assertEqual(state, "empty")
        self.assertLess(elapsed, 5, "空错误页没有提前收手，白等了满超时")

    def test_about_blank_is_not_treated_as_dead(self):
        """刚 commit 时常见 about:blank，那是导航空档，不能当成死页提前收手。"""
        page = FakePage(result={"blank": True, "href": "about:blank"})
        started = time.time()
        state = qr_login.wait_chat_access(page, timeout_s=1.3)
        elapsed = time.time() - started
        self.assertEqual(state, "empty")
        self.assertGreaterEqual(elapsed, 1.0, "about:blank 被当成死页提前收手了")

    def test_login_wall_still_detected(self):
        page = FakePage(result={"hasScan": True})
        self.assertEqual(qr_login.wait_chat_access(page, timeout_s=5), "login")

    def test_chat_list_still_detected(self):
        page = FakePage(result={"hasChat": True})
        self.assertEqual(qr_login.wait_chat_access(page, timeout_s=5), "chat")

    def test_challenge_page_is_not_login_or_chat(self):
        page = FakePage(result={"hasChallenge": True, "snippet": "请完成验证"})
        self.assertEqual(qr_login.wait_chat_access(page, timeout_s=5), "challenge")


if __name__ == "__main__":
    unittest.main()
