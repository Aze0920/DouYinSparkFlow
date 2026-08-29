"""检测结论不能把「网慢」判成「掉线」。

线上出过：资料接口刚正常返回昵称和抖音号，只因为私信页 25 秒超时，
就判 valid=False、把账号标成掉线、还推了一条「抖音账号掉线」通知。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webui import cookie_probe as probe_mod

COOKIES = [{"name": "sessionid", "value": "x" * 20, "domain": ".douyin.com", "path": "/"}]
PROFILE = {"username": "阮言泽", "unique_id": "92483184909", "avatar": ""}


class Page:
    def __init__(self, chat_opens=True):
        self.chat_opens = chat_opens

    def goto(self, url, **kwargs):
        if "/chat" in url and not self.chat_opens:
            raise TimeoutError("Page.goto: Timeout 25000ms exceeded")


class Context:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page

    def close(self):
        pass


EMPTY_PROFILE = {"username": "抖音账号", "unique_id": "", "avatar": ""}


def run_probe(chat_opens: bool, chat_state: str, signals=None, has_session=True, profile=None):
    page = Page(chat_opens)
    context = Context(page)
    patches = [
        patch.object(probe_mod, "get_browser", return_value=(None, None)),
        patch.object(probe_mod, "make_context", return_value=context),
        patch.object(probe_mod, "extract_profile", return_value=profile or PROFILE),
        patch.object(probe_mod, "wait_chat_access", return_value=chat_state),
        patch.object(probe_mod, "_page_signals", return_value=signals or {}),
        patch.object(probe_mod, "_has_session", return_value=has_session),
        patch.object(probe_mod, "_cookies_for_save", return_value=COOKIES),
        patch.object(probe_mod, "save_state", return_value=True),
        patch.object(probe_mod, "load_state_path", return_value=None),
    ]
    for p in patches:
        p.start()
    try:
        return probe_mod.probe_cookies(COOKIES, "92483184909")
    finally:
        for p in patches:
            p.stop()


class VerdictTests(unittest.TestCase):
    def test_healthy_account_is_valid(self):
        result = run_probe(chat_opens=True, chat_state="chat")
        self.assertTrue(result["valid"])
        self.assertTrue(result["chat_ok"])
        self.assertIn("网页私信可打开", result["message"])

    def test_chat_page_timeout_is_not_offline(self):
        """私信页没打开 ≠ 掉线。资料接口都拿到抖音号了，登录态明摆着是好的。"""
        result = run_probe(chat_opens=False, chat_state="empty")
        self.assertTrue(result["valid"], "网慢被判掉线，会误推掉线通知")
        self.assertFalse(result["chat_ok"])
        self.assertIn("没打开私信页", result["message"])
        self.assertEqual(result["unique_id"], "92483184909")

    def test_login_wall_is_still_offline(self):
        """页面真打开了、而且在要求扫码，那才是掉线，不能被上面的兜底放过。"""
        result = run_probe(chat_opens=True, chat_state="login")
        self.assertFalse(result["valid"])
        self.assertIn("扫码", result["message"])

    def test_login_wall_wins_even_if_navigation_failed(self):
        result = run_probe(chat_opens=False, chat_state="empty", signals={"hasScan": True})
        self.assertFalse(result["valid"])

    def test_no_session_is_offline(self):
        """连登录态 cookie 都没有、又没抓到资料，才是真·无效。"""
        result = run_probe(
            chat_opens=False, chat_state="empty", has_session=False, profile=EMPTY_PROFILE
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["undecided"])

    def test_named_profile_is_valid_even_without_chat_list(self):
        """资料接口要登录态才会返回昵称+抖音号，抓到了就证明号是活的，
        私信列表没渲染出来只是页面慢，不该判掉线。"""
        result = run_probe(chat_opens=True, chat_state="empty")
        self.assertTrue(result["valid"])

    def test_dead_proxy_is_undecided_not_offline(self):
        """代理死了：什么资料都没抓到、私信页也没打开，但 session 还在、没撞扫码墙。
        这是「无法确认」，绝不能判掉线、绝不能推通知。"""
        result = run_probe(chat_opens=False, chat_state="empty", profile=EMPTY_PROFILE)
        self.assertTrue(result["valid"], "没连上抖音被误判成掉线，会白推掉线通知")
        self.assertTrue(result["undecided"])
        self.assertIn("无法确认", result["message"])

    def test_dead_proxy_but_login_wall_still_offline(self):
        """哪怕线路差，只要真看见了扫码墙，就是掉线，不能被『无法确认』兜过去。"""
        result = run_probe(
            chat_opens=False, chat_state="empty", signals={"hasScan": True}, profile=EMPTY_PROFILE
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["undecided"])


class ProxyTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "webui" / "cookie_probe.py").read_text(encoding="utf-8")

    def test_chat_navigation_gets_longer_budget_behind_a_proxy(self):
        self.assertIn("30000 if proxy else 20000", self.source)

    def test_element_wait_also_gets_longer_behind_a_proxy(self):
        """走代理时会话列表渲染得慢，等元素的时间也要跟着放宽。"""
        self.assertIn("timeout_s=25 if proxy else 12", self.source)


if __name__ == "__main__":
    unittest.main()
