"""设了地区的账号，凡是开浏览器都得走那个地区的代理。

漏掉任何一个入口，那次访问就从机房 IP 出去了，正好是这套功能要避免的异地登录。
每个入口都在这里钉一颗钉子，省得以后加新入口时又漏。
"""
import ast
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from webui import chat_list as chat_list_mod
from webui import cookie_probe as probe_mod
from webui import proxy as proxy_mod
from webui import qr_login as qr_mod
from webui.proxy import ProxyExpired, ProxyLease
from webui.regions import region_of

HENAN_XINXIANG = "410700"


class RegionLookupTests(unittest.TestCase):
    def test_finds_the_region_of_the_right_account(self):
        tasks = [
            {"unique_id": "a1", "region": HENAN_XINXIANG},
            {"unique_id": "a2", "region": "110100"},
        ]
        self.assertEqual(region_of(tasks, "a1"), HENAN_XINXIANG)
        self.assertEqual(region_of(tasks, "a2"), "110100")

    def test_account_without_region_stays_direct(self):
        tasks = [{"unique_id": "a1"}, {"unique_id": "a2", "region": ""}]
        self.assertEqual(region_of(tasks, "a1"), "")
        self.assertEqual(region_of(tasks, "a2"), "")

    def test_unknown_account_and_blank_id(self):
        tasks = [{"unique_id": "a1", "region": HENAN_XINXIANG}]
        self.assertEqual(region_of(tasks, "nope"), "")
        self.assertEqual(region_of(tasks, ""), "")
        self.assertEqual(region_of(tasks, None), "")
        self.assertEqual(region_of([], "a1"), "")

    def test_bad_region_code_is_dropped_rather_than_used(self):
        """脏地区码宁可直连，也不能拿它去换一个不相干的异地 IP。"""
        self.assertEqual(region_of([{"unique_id": "a1", "region": "999999"}], "a1"), "")

    def test_survives_junk_rows(self):
        tasks = ["nonsense", None, {"unique_id": "a1", "region": HENAN_XINXIANG}]
        self.assertEqual(region_of(tasks, "a1"), HENAN_XINXIANG)


class EntryPointProxyTests(unittest.TestCase):
    """每个入口都在真正开浏览器之前租一条 IP，所以打断 get_browser 就能验证租没租。"""

    def call_picker(self, region):
        return chat_list_mod.list_conversations([], unique_id="a1", force=True, region=region)

    def call_probe(self, region):
        return probe_mod.probe_cookies([{"name": "sessionid", "value": "x"}], "a1", region)

    def test_picker_uses_the_account_region(self):
        with patch("webui.proxy.proxy_enabled", return_value=True):
            with patch("webui.proxy.lease_proxy", return_value=ProxyLease("http://1.2.3.4:9000")) as rent:
                with patch.object(chat_list_mod, "get_browser", side_effect=RuntimeError("stop")):
                    self.call_picker(HENAN_XINXIANG)
        self.assertEqual(rent.call_args.args[0], HENAN_XINXIANG)

    def test_picker_stays_direct_without_region(self):
        with patch("webui.proxy.lease_proxy") as rent:
            with patch.object(chat_list_mod, "get_browser", side_effect=RuntimeError("stop")):
                self.call_picker("")
        rent.assert_not_called()

    def test_cookie_check_uses_the_account_region(self):
        """「检测」按钮以前完全不传地区，是这次报障的原点。"""
        with patch("webui.proxy.proxy_enabled", return_value=True):
            with patch("webui.proxy.lease_proxy", return_value=ProxyLease("http://1.2.3.4:9000")) as rent:
                with patch.object(probe_mod, "get_browser", side_effect=RuntimeError("stop")):
                    self.call_probe(HENAN_XINXIANG)
        self.assertEqual(rent.call_args.args[0], HENAN_XINXIANG)

    def test_cookie_check_stays_direct_without_region(self):
        with patch("webui.proxy.lease_proxy") as rent:
            with patch.object(probe_mod, "get_browser", side_effect=RuntimeError("stop")):
                self.call_probe("")
        rent.assert_not_called()

    def test_picker_stays_direct_when_switch_off(self):
        """总开关关掉：哪怕账号设了地区，也不能去租 IP。"""
        with patch("webui.proxy.proxy_enabled", return_value=False):
            with patch("webui.proxy.lease_proxy") as rent:
                with patch.object(chat_list_mod, "get_browser", side_effect=RuntimeError("stop")):
                    self.call_picker(HENAN_XINXIANG)
        rent.assert_not_called()

    def test_cookie_check_stays_direct_when_switch_off(self):
        with patch("webui.proxy.proxy_enabled", return_value=False):
            with patch("webui.proxy.lease_proxy") as rent:
                with patch.object(probe_mod, "get_browser", side_effect=RuntimeError("stop")):
                    self.call_probe(HENAN_XINXIANG)
        rent.assert_not_called()


class QrLoginProxyTests(unittest.TestCase):
    """重新登录一个已有账号，也必须从它自己的地区出去。"""

    def test_extracts_proxy_for_the_region(self):
        with patch("webui.proxy.proxy_enabled", return_value=True):
            with patch("webui.proxy.lease_proxy", return_value=ProxyLease("http://1.2.3.4:9000")) as rent:
                self.assertEqual(qr_mod._login_proxy(HENAN_XINXIANG).server, "http://1.2.3.4:9000")
        self.assertEqual(rent.call_args.args[0], HENAN_XINXIANG)

    def test_new_account_without_region_stays_direct(self):
        with patch("webui.proxy.lease_proxy") as rent:
            self.assertIsNone(qr_mod._login_proxy(""))
        rent.assert_not_called()

    def test_extract_failure_falls_back_to_direct(self):
        with patch("webui.proxy.proxy_enabled", return_value=True):
            with patch("webui.proxy.lease_proxy", side_effect=RuntimeError("boom")):
                self.assertIsNone(qr_mod._login_proxy(HENAN_XINXIANG))
            with patch("webui.proxy.lease_proxy", return_value=None):
                self.assertIsNone(qr_mod._login_proxy(HENAN_XINXIANG))

    def test_context_gets_the_proxy(self):
        browser = unittest.mock.MagicMock()
        qr_mod._login_context(browser, "http://1.2.3.4:9000")
        self.assertEqual(
            browser.new_context.call_args.kwargs["proxy"], {"server": "http://1.2.3.4:9000"}
        )

    def test_context_without_proxy_has_no_proxy_key(self):
        browser = unittest.mock.MagicMock()
        qr_mod._login_context(browser, None)
        self.assertNotIn("proxy", browser.new_context.call_args.kwargs)

    def test_context_retries_direct_when_proxy_context_fails(self):
        browser = unittest.mock.MagicMock()
        browser.new_context.side_effect = [RuntimeError("代理连不上"), "ok"]
        self.assertEqual(qr_mod._login_context(browser, "http://1.2.3.4:9000"), "ok")
        self.assertNotIn("proxy", browser.new_context.call_args.kwargs)

    def test_context_without_proxy_raises_instead_of_looping(self):
        browser = unittest.mock.MagicMock()
        browser.new_context.side_effect = RuntimeError("浏览器挂了")
        with self.assertRaises(RuntimeError):
            qr_mod._login_context(browser, None)


class LeaseTests(unittest.TestCase):
    """IP 只有 10 分钟，到点必须收手：过期的 IP 不是变慢，是彻底没网。"""

    def test_deadline_leaves_a_safety_margin(self):
        """卡着整 10 分钟停，最后几步已经在断网里跑了。"""
        lease = ProxyLease("http://1.2.3.4:9000", minutes=10, grace=40)
        self.assertAlmostEqual(lease.remaining(), 10 * 60 - 40, delta=2)
        self.assertFalse(lease.expired())

    def test_expires_after_the_window(self):
        lease = ProxyLease("http://1.2.3.4:9000")
        lease.deadline = time.time() - 1
        self.assertTrue(lease.expired())
        self.assertLess(lease.remaining(), 0)

    def test_check_raises_once_expired(self):
        lease = ProxyLease("http://1.2.3.4:9000")
        lease.check()
        lease.deadline = time.time() - 1
        with self.assertRaises(ProxyExpired) as caught:
            lease.check("发消息")
        self.assertIn("发消息", str(caught.exception))

    def test_grace_never_makes_the_window_negative(self):
        """就算宽限值配得比时长还大，也得留一点时间，不能一拿到就算过期。"""
        lease = ProxyLease("http://1.2.3.4:9000", minutes=1, grace=999)
        self.assertFalse(lease.expired())
        self.assertGreater(lease.remaining(), 0)

    def test_lease_proxy_returns_none_when_extraction_fails(self):
        with patch("webui.proxy.fetch_proxy", return_value=None):
            self.assertIsNone(proxy_mod.lease_proxy(HENAN_XINXIANG))

    def test_lease_proxy_wraps_the_server(self):
        with patch("webui.proxy.fetch_proxy", return_value="http://1.2.3.4:9000"):
            lease = proxy_mod.lease_proxy(HENAN_XINXIANG)
        self.assertEqual(lease.server, "http://1.2.3.4:9000")
        self.assertGreater(lease.remaining(), 0)


class CallSiteTests(unittest.TestCase):
    """真正的故障不是这些函数不支持地区，是 app.py 调用时忘了传。

    app.py 依赖 fastapi，这里装不了，所以用 AST 直接查调用点，
    比跑不起来的集成测试更能守住「又漏一个入口」这类回归。
    """

    # 函数名 -> 地区参数排第几个位置参数（没有位置形式就写 None）
    ENTRIES = {
        "probe_cookies": 2,
        "list_conversations": None,
        "start_qr_login": None,
    }

    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).resolve().parent.parent / "webui" / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(source)

    def calls_to(self, name):
        return [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        ]

    def test_every_browser_entry_point_passes_a_region(self):
        for name, region_pos in self.ENTRIES.items():
            calls = self.calls_to(name)
            self.assertTrue(calls, f"app.py 里没找到 {name} 的调用，测试该更新了")
            for call in calls:
                by_keyword = any(kw.arg == "region" for kw in call.keywords)
                by_position = region_pos is not None and len(call.args) > region_pos
                self.assertTrue(
                    by_keyword or by_position,
                    f"app.py 第 {call.lineno} 行调用 {name} 没传地区，这个入口会走直连",
                )


class BrowserExclusionTests(unittest.TestCase):
    """同一时刻只许开一个浏览器。两个一起开会互相抢资源，
    碰上同一个账号还会两边一起写同一份快照，把登录态写坏。"""

    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parent.parent / "webui" / "app.py").read_text(encoding="utf-8")

    def block(self, marker: str) -> str:
        start = self.source.index(marker)
        return self.source[start:self.source.index("\n\n", start)]

    def test_keepalive_stands_down_for_tasks_and_qr(self):
        guard = self.block("def _tick_keepalive()")
        self.assertIn('_run_state["running"]', guard)
        self.assertIn("_keepalive_slot.busy()", guard)
        self.assertIn("qr_busy()", guard, "扫码窗口开着时保活也必须让位，否则会同时开两个浏览器")
        self.assertIn("try_start", guard)

    def test_every_browser_entry_stands_down_for_keepalive(self):
        """保活是后台偷偷开的浏览器，前台各入口都得让它让路，不能直接把扫码挡死。"""
        for marker in ("def _deny_if_browser_busy()", "def run_now(", "def douyin_login_start("):
            self.assertIn(
                "_preempt_keepalive",
                self.block(marker),
                f"{marker} 没有让保活让路，两个浏览器会同时写同一份快照",
            )
        login = self.block("def douyin_login_start(")
        self.assertNotIn("扫码登录被拒绝：保活正在跑", login)
        slot = (Path(__file__).resolve().parent.parent / "webui" / "keepalive_slot.py").read_text(encoding="utf-8")
        self.assertIn("class KeepaliveSlot", slot)
        self.assertIn("def kill_owned_chromium", slot)
        self.assertNotIn("time.sleep", slot)


if __name__ == "__main__":
    unittest.main()
