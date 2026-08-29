"""SSO 返回风控挑战页时，扫码必须能自己过去。

挑战页是一段 JS，跑起来才会种 cookie；context.request 不执行 JS，
所以在那条路上重试多少次都是同一张 HTML —— 表现出来就是「一直拿不到二维码」。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webui import qr_login as qr

# 线上实际收到的报文：HTTP 200，但 body 是种 gfkadpd cookie 的混淆 JS
CHALLENGE = (
    '<!doctype html><html><head>\n<script  >    !function(){var e="10006",t="31827";'
    'function n(){try{var n="gfkadpd";document.cookie=n+"="+o+"; path=/; Secure;"}catch(e){}}'
)
GOOD = {"data": {"token": "T0KEN", "qrcode": ""}}


class Resp:
    def __init__(self, body, payload=None, status=200):
        self.status = status
        self._body = body
        self._payload = payload

    def text(self):
        return self._body

    def json(self):
        if self._payload is None:
            raise ValueError("不是 JSON")
        return self._payload


class Ctx:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        outer = self

        class Request:
            def get(self, url, **kwargs):
                outer.calls.append((url, kwargs))
                return outer.responses.pop(0)

        self.request = Request()


class Page:
    """跑一次 goto 就当挑战通过，种上 cookie。"""

    def __init__(self, solves=True):
        self.solves = solves
        self.gotos = []
        self._cookies = [{"name": "ttwid"}]
        self.context = self

    def cookies(self):
        return list(self._cookies)

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        if self.solves and "get_qrcode" in url:
            self._cookies.append({"name": "gfkadpd"})

    def wait_for_timeout(self, ms):
        pass


class ChallengeDetectionTests(unittest.TestCase):
    def test_spots_the_html_challenge(self):
        self.assertTrue(qr._is_challenge_page(CHALLENGE))
        self.assertTrue(qr._is_challenge_page("<HTML><body>x</body></HTML>"))

    def test_does_not_mistake_json_for_a_challenge(self):
        self.assertFalse(qr._is_challenge_page('{"data":{"token":"x"}}'))
        self.assertFalse(qr._is_challenge_page(""))


class RequestQrTests(unittest.TestCase):
    def test_solves_the_challenge_then_gets_the_token(self):
        ctx = Ctx([Resp(CHALLENGE), Resp("{}", GOOD)])
        page = Page(solves=True)
        token, _, _ = qr._request_qr(ctx, "fp123", page)
        self.assertEqual(token, "T0KEN")
        self.assertEqual(len(ctx.calls), 2, "过完挑战要再请求一次接口")
        self.assertTrue(any("get_qrcode" in u for u in page.gotos), "挑战必须在真页面里跑")
        self.assertEqual(page.gotos[-1], qr.HOME, "跑完要回首页，别把页面停在挑战页上")

    def test_only_retries_once(self):
        """挑战一直过不去时不能无限递归，得让上层去走回退方案。"""
        ctx = Ctx([Resp(CHALLENGE), Resp(CHALLENGE)])
        token, png, jump = qr._request_qr(ctx, "fp123", Page(solves=True))
        self.assertEqual((token, png, jump), ("", "", ""))
        self.assertEqual(len(ctx.calls), 2)

    def test_gives_up_when_the_challenge_sets_no_cookie(self):
        ctx = Ctx([Resp(CHALLENGE)])
        token, _, _ = qr._request_qr(ctx, "fp123", Page(solves=False))
        self.assertEqual(token, "")
        self.assertEqual(len(ctx.calls), 1, "cookie 没种上就别再打接口了")

    def test_plain_non_json_is_not_treated_as_a_challenge(self):
        ctx = Ctx([Resp("boom")])
        page = Page()
        qr._request_qr(ctx, "fp123", page)
        self.assertEqual(page.gotos, [], "不是挑战页就不该白跑一次导航")

    def test_works_without_a_page(self):
        ctx = Ctx([Resp(CHALLENGE)])
        self.assertEqual(qr._request_qr(ctx, "fp123"), ("", "", ""))


class TimeoutBudgetTests(unittest.TestCase):
    """走代理每一跳都更慢，直连够用的超时在代理下会一路超时。"""

    def setUp(self):
        self.addCleanup(qr._use_timeouts, False)

    def test_proxy_gets_a_longer_budget(self):
        qr._use_timeouts(True)
        self.assertEqual(qr._TIMEOUTS["nav"], qr.NAV_PROXY)
        self.assertEqual(qr._TIMEOUTS["api"], qr.API_PROXY)
        self.assertGreater(qr.NAV_PROXY, qr.NAV_DIRECT)
        self.assertGreater(qr.API_PROXY, qr.API_DIRECT)

    def test_direct_keeps_the_original_budget(self):
        qr._use_timeouts(False)
        self.assertEqual(qr._TIMEOUTS["nav"], 25000)
        self.assertEqual(qr._TIMEOUTS["api"], 20000)

    def test_http_get_uses_the_current_budget(self):
        qr._use_timeouts(True)
        ctx = Ctx([Resp("{}", {})])
        qr._http_get(ctx, "https://sso.douyin.com/x")
        self.assertEqual(ctx.calls[0][1]["timeout"], qr.API_PROXY)


class WorkerGuardTests(unittest.TestCase):
    """首页打不开就得当场收手，不能带着一条坏 IP 继续往下跑将近一分钟。"""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "webui" / "qr_login.py").read_text(encoding="utf-8")

    def test_aborts_when_the_proxy_cannot_open_douyin(self):
        start = self.source.index('logger.exception("打开抖音首页失败")')
        block = self.source[start:start + 700]
        self.assertIn("if lease:", block)
        self.assertIn("return", block)
        self.assertIn("刷新二维码", block, "要告诉用户点刷新会换一条新 IP")


if __name__ == "__main__":
    unittest.main()
