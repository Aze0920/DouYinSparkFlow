"""上下文建不起来时，让步的顺序不能搞反。

代理没了顶多是走直连；快照没了这个号就等于掉线，要重新扫码。
所以先丢代理、再丢快照，绝不能反过来。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.browser import make_context


class FakeContext:
    def __init__(self):
        self.headers = None
        self.cookies = []

    def set_extra_http_headers(self, headers):
        self.headers = headers

    def add_init_script(self, script):
        pass

    def add_cookies(self, cookies):
        self.cookies = cookies


class FakeBrowser:
    """带某几个参数就建不起来，用来模拟坏代理 / 坏快照。"""

    def __init__(self, breaks_on=()):
        self.breaks_on = set(breaks_on)
        self.attempts = []

    def new_context(self, **kwargs):
        self.attempts.append(kwargs)
        if self.breaks_on & set(kwargs):
            raise RuntimeError("建不起来：" + ",".join(sorted(self.breaks_on & set(kwargs))))
        return FakeContext()


class MakeContextTests(unittest.TestCase):
    def test_uses_proxy_and_snapshot_when_both_work(self):
        browser = FakeBrowser()
        make_context(browser, storage_state="s.json", proxy="http://1.2.3.4:9000")
        self.assertEqual(len(browser.attempts), 1)
        self.assertEqual(browser.attempts[0]["proxy"], {"server": "http://1.2.3.4:9000"})
        self.assertEqual(browser.attempts[0]["storage_state"], "s.json")

    def test_bad_proxy_falls_back_to_direct_but_keeps_the_login(self):
        browser = FakeBrowser(breaks_on={"proxy"})
        make_context(browser, storage_state="s.json", proxy="http://1.2.3.4:9000")
        used = browser.attempts[-1]
        self.assertNotIn("proxy", used)
        self.assertEqual(used["storage_state"], "s.json", "丢代理就够了，不该把登录态一起丢掉")

    def test_bad_snapshot_keeps_the_proxy(self):
        browser = FakeBrowser(breaks_on={"storage_state"})
        make_context(browser, storage_state="坏的.json", proxy="http://1.2.3.4:9000")
        used = browser.attempts[-1]
        self.assertNotIn("storage_state", used)
        self.assertEqual(used["proxy"], {"server": "http://1.2.3.4:9000"}, "快照坏了不代表代理不能用")

    def test_gives_up_both_only_as_a_last_resort(self):
        browser = FakeBrowser(breaks_on={"proxy", "storage_state"})
        make_context(browser, storage_state="s.json", proxy="http://1.2.3.4:9000")
        used = browser.attempts[-1]
        self.assertNotIn("proxy", used)
        self.assertNotIn("storage_state", used)
        self.assertGreaterEqual(len(browser.attempts), 3, "应该逐样试过来，不是一步退到底")

    def test_no_duplicate_attempts_when_nothing_to_drop(self):
        browser = FakeBrowser(breaks_on={"user_agent"})
        with self.assertRaises(RuntimeError):
            make_context(browser)
        self.assertEqual(len(browser.attempts), 1, "没代理没快照就只有一种建法，别白试三遍")

    def test_real_failure_is_raised_not_swallowed(self):
        browser = FakeBrowser(breaks_on={"viewport"})
        with self.assertRaises(RuntimeError):
            make_context(browser, storage_state="s.json", proxy="http://1.2.3.4:9000")

    def test_cookies_are_applied(self):
        browser = FakeBrowser()
        ctx = make_context(browser, cookies=[{"name": "sessionid", "value": "x", "domain": ".douyin.com", "path": "/"}])
        self.assertEqual(len(ctx.cookies), 1)


if __name__ == "__main__":
    unittest.main()
