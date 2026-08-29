"""续火花：一条代理 IP 打不开会话列表时，应换一条新 IP 重试，而不是整轮失败。

线上住宅 IP 质量参差：同一账号，这条 2.7 秒就出列表、那条 70 秒还是空白页。
以前一条坏 IP 就把整次续火花判失败、还推「续火花失败」。现在要换几条再试。
但「扫码墙 / 被限流 / 输入框改版」换 IP 也没用，必须快速失败、不重试。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import tasks


class FakeLease:
    def __init__(self, server):
        self.server = server
        self.minutes = 10
        self.released = False

    def expired(self):
        return False

    def release(self, what=""):
        self.released = True


class FakePage:
    url = "https://www.douyin.com/chat"

    def on(self, *a, **k):
        pass

    def goto(self, *a, **k):
        pass

    def wait_for_selector(self, *a, **k):
        raise RuntimeError("no flame")

    def screenshot(self, *a, **k):
        pass


class FakeContext:
    def __init__(self):
        self._page = FakePage()

    def set_default_navigation_timeout(self, *a, **k):
        pass

    def set_default_timeout(self, *a, **k):
        pass

    def new_page(self):
        return self._page

    def close(self):
        pass


class FakeBrowser:
    def close(self):
        pass


class FakePW:
    def stop(self):
        pass


def run_task(*, region, leases, wait_results, login_wall=False, friends=()):
    """把 do_user_task 需要的外部依赖全部替换成可控假件，返回 (raised, leases)。

    wait_results: _wait_locator 每次返回的 item_loc，None 表示这条 IP 没出列表。
    """
    lease_iter = iter(leases)
    proxy_calls = {"n": 0}

    def fake_account_proxy(username, region_):
        proxy_calls["n"] += 1
        return next(lease_iter)

    wait_iter = iter(wait_results)

    def fake_wait_locator(page, selectors, timeout_ms=0, **k):
        item = next(wait_iter, None)
        return (item, page, "sel" if item is not None else "")

    patches = [
        patch.object(tasks, "get_browser", return_value=(FakePW(), FakeBrowser())),
        patch.object(tasks, "make_context", return_value=FakeContext()),
        patch.object(tasks, "_account_proxy", side_effect=fake_account_proxy),
        patch.object(tasks, "retry_operation", return_value=None),
        patch.object(tasks, "_looks_like_login", return_value=login_wall),
        patch.object(tasks, "_wait_locator", side_effect=fake_wait_locator),
        patch.object(tasks, "_find_locator", return_value=(None, None, "")),
        patch.object(tasks, "_dump_chat_debug", return_value=None),
        patch.object(tasks, "scroll_and_select_user", return_value=iter(friends)),
        patch.object(tasks.time, "sleep", lambda *a, **k: None),
        patch("webui.session_store.load_state_path", return_value=None),
        patch("webui.session_store.save_state", return_value=True),
        patch("webui.chat_list._collect_dom", return_value=[]),
    ]
    for p in patches:
        p.start()
    try:
        raised = None
        try:
            tasks.do_user_task(
                "阮言泽", [{"name": "sessionid", "value": "x"}], ["王洁"],
                message_template="hi", unique_id="92483184909", region=region,
            )
        except Exception as exc:
            raised = exc
        return raised, proxy_calls["n"]
    finally:
        for p in patches:
            p.stop()


class ProxyRetryTests(unittest.TestCase):
    def setUp(self):
        tasks.config["proxyIpTries"] = 3
        tasks.config.setdefault("taskRetryTimes", 1)
        tasks.config.setdefault("browserTimeout", 30000)

    def test_dead_ip_triggers_fresh_ip_retry_until_exhausted(self):
        leases = [FakeLease("http://a:1"), FakeLease("http://b:2"), FakeLease("http://c:3")]
        raised, proxy_calls = run_task(region="河南省", leases=leases, wait_results=[None, None, None])
        self.assertIsInstance(raised, RuntimeError)
        self.assertEqual(proxy_calls, 3, "没有把 3 条 IP 都试一遍")
        self.assertIn("连换 3 条", str(raised))
        for lease in leases:
            self.assertTrue(lease.released, "每条用过的 IP 都必须登记收手")

    def test_stops_as_soon_as_a_good_ip_opens_the_list(self):
        """第 2 条 IP 就出了列表：不该再去取第 3 条。"""
        leases = [FakeLease("http://a:1"), FakeLease("http://b:2"), FakeLease("http://c:3")]
        # 第 1 条没列表(None)，第 2 条出列表(object)。列表出来后没好友可发 → 抛别的错，但不再换 IP。
        raised, proxy_calls = run_task(
            region="河南省", leases=leases, wait_results=[None, object()], friends=[]
        )
        self.assertEqual(proxy_calls, 2, "好 IP 出现后仍在换 IP，白烧额度")
        self.assertNotIn("连换", str(raised or ""))
        self.assertTrue(leases[0].released and leases[1].released)
        self.assertFalse(leases[2].released, "第 3 条根本不该被取用")

    def test_login_wall_does_not_retry(self):
        """页面在要求扫码：换 IP 也没用，必须一次就失败。"""
        leases = [FakeLease("http://a:1"), FakeLease("http://b:2")]
        raised, proxy_calls = run_task(
            region="河南省", leases=leases, wait_results=[object()], login_wall=True
        )
        self.assertIsInstance(raised, RuntimeError)
        self.assertIn("要求登录", str(raised))
        self.assertEqual(proxy_calls, 1, "扫码墙不该触发换 IP 重试")

    def test_direct_connection_tries_once(self):
        """没设地区(直连)没得换 IP，只试一次，报原来的文案。"""
        raised, proxy_calls = run_task(region="", leases=[None], wait_results=[None])
        self.assertIsInstance(raised, RuntimeError)
        self.assertEqual(proxy_calls, 1)
        self.assertIn("打不开会话列表", str(raised))
        self.assertNotIn("连换", str(raised))


if __name__ == "__main__":
    unittest.main()
