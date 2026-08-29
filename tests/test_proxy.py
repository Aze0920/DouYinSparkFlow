import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webui import proxy as proxy_mod
from webui.proxy import build_url, fetch_proxy, load_proxy, mask_url, parse_extract, public_proxy, save_proxy
from webui.regions import area_label, normalize_area, region_tree


class RegionTests(unittest.TestCase):
    def test_province_and_city_labels(self):
        self.assertEqual(area_label("130000"), "河北省")
        self.assertEqual(area_label("130100"), "河北省 · 石家庄市")

    def test_codes_that_do_not_match_their_province_prefix(self):
        """万宁 571500、兵团四市的前两位和所属省对不上，必须手工归位。"""
        self.assertEqual(area_label("571500"), "海南省 · 万宁")
        self.assertEqual(area_label("832061"), "新疆 · 石河子市")
        self.assertEqual(area_label("843300"), "新疆 · 阿拉尔市")

    def test_all_and_invalid_normalize_to_empty(self):
        # area=all 是全国随机，会给账号配异地 IP，必须当成「没设地区」处理
        self.assertEqual(normalize_area("all"), "")
        self.assertEqual(normalize_area("999999"), "")
        self.assertEqual(normalize_area(None), "")
        self.assertEqual(normalize_area("130100"), "130100")

    def test_tree_is_complete_and_clean(self):
        tree = region_tree()
        self.assertEqual(len(tree), 31)
        cities = [c for p in tree for c in p["cities"]]
        self.assertEqual(len(cities), 367)
        codes = [c["code"] for c in cities]
        self.assertEqual(len(codes), len(set(codes)), "地区码不能重复")
        for province in tree:
            self.assertTrue(province["cities"], f"{province['name']} 没有城市")
        blob = "".join(c["name"] + c["code"] for c in cities)
        self.assertFalse(
            any(ord(ch) in (0x200B, 0x200C, 0x200D, 0xFEFF) for ch in blob),
            "文档里带零宽字符，解析时必须清掉",
        )
        self.assertNotIn("襄樊市", [c["name"] for c in cities])


class ParseExtractTests(unittest.TestCase):
    def test_parses_documented_json_shape(self):
        body = (
            '{"code":0,"phone":"138","area":"130100",'
            '"whitelist":{"ip":"1.2.3.4","ok":true},'
            '"extract":{"ok":true,"data":"111.22.33.44:20000","raw":"111.22.33.44:20000"}}'
        )
        self.assertEqual(parse_extract(body), "111.22.33.44:20000")

    def test_plain_text_response(self):
        self.assertEqual(parse_extract("111.22.33.44:20000\n"), "111.22.33.44:20000")

    def test_rejects_auth_error_and_failed_extract(self):
        self.assertEqual(parse_extract('{"code":-1,"message":"缺少或错误的 API 密钥"}'), "")
        self.assertEqual(parse_extract('{"code":0,"extract":{"ok":false,"data":"未加白名单"}}'), "")
        self.assertEqual(parse_extract("<html>404</html>"), "")
        self.assertEqual(parse_extract(""), "")

    def test_rejects_out_of_range_port(self):
        self.assertEqual(parse_extract("1.2.3.4:99999"), "")


class BuildUrlTests(unittest.TestCase):
    def test_appends_area_and_minute_keeping_key(self):
        url = build_url("https://ba.cd/ip/extract.php?key=SECRET&phone=138", "130100", 10)
        self.assertIn("key=SECRET", url)
        self.assertIn("phone=138", url)
        self.assertIn("area=130100", url)
        self.assertIn("minute=10", url)

    def test_overrides_area_already_in_url(self):
        """用户贴的链接里常带着示例 area，不能让它盖掉账号真实地区。"""
        url = build_url("https://ba.cd/ip/extract.php?key=K&area=440300&minute=5", "130100", 10)
        self.assertIn("area=130100", url)
        self.assertNotIn("440300", url)
        self.assertNotIn("minute=5", url)


class MaskTests(unittest.TestCase):
    def test_key_and_phone_are_masked(self):
        masked = mask_url("https://ba.cd/ip/extract.php?key=abcdef123456&phone=13800000000")
        self.assertNotIn("abcdef123456", masked)
        self.assertNotIn("13800000000", masked)
        self.assertIn("***", masked)
        self.assertIn("ba.cd", masked)


class ProxyConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = patch.object(proxy_mod, "PROXY_FILE", Path(self.tmp.name) / "proxy.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_defaults_and_roundtrip(self):
        self.assertFalse(load_proxy()["enabled"])
        save_proxy({"enabled": True, "api_url": "https://ba.cd/ip/extract.php?key=K&phone=1", "minute": 15})
        data = load_proxy()
        self.assertTrue(data["enabled"])
        self.assertEqual(data["minute"], 15)
        self.assertEqual(data["protocol"], "http")

    def test_masked_url_does_not_overwrite_stored_key(self):
        """前端回显的是打码链接，原样提交回来不能把真密钥冲掉。"""
        save_proxy({"enabled": True, "api_url": "https://ba.cd/ip/extract.php?key=REALKEY&phone=1"})
        masked = public_proxy()["api_url"]
        save_proxy({"enabled": True, "api_url": masked})
        self.assertIn("REALKEY", load_proxy()["api_url"])

    def test_public_hides_the_key(self):
        save_proxy({"api_url": "https://ba.cd/ip/extract.php?key=REALKEY&phone=13800000000"})
        pub = public_proxy()
        self.assertNotIn("REALKEY", pub["api_url"])
        self.assertTrue(pub["api_url_set"])

    def test_out_of_range_values_are_clamped(self):
        save_proxy({"minute": 9999, "retries": 99, "protocol": "ftp"})
        data = load_proxy()
        self.assertEqual(data["minute"], 120)
        self.assertEqual(data["retries"], proxy_mod.MAX_RETRIES)
        self.assertEqual(data["protocol"], "http")


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class FetchProxyTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "enabled": True,
            "api_url": "https://ba.cd/ip/extract.php?key=K&phone=138",
            "protocol": "http",
            "minute": 10,
            "retries": 3,
        }
        sleep = patch.object(proxy_mod.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_returns_playwright_ready_server(self):
        ok = FakeResponse('{"code":0,"extract":{"ok":true,"data":"1.2.3.4:20000"}}')
        with patch.object(proxy_mod.httpx, "get", return_value=ok) as get:
            self.assertEqual(fetch_proxy("130100", self.cfg), "http://1.2.3.4:20000")
        self.assertIn("area=130100", get.call_args[0][0])

    def test_socks5_protocol_prefix(self):
        cfg = {**self.cfg, "protocol": "socks5"}
        ok = FakeResponse("1.2.3.4:20000")
        with patch.object(proxy_mod.httpx, "get", return_value=ok):
            self.assertEqual(fetch_proxy("130100", cfg), "socks5://1.2.3.4:20000")

    def test_retries_then_succeeds(self):
        bad = FakeResponse('{"code":-1,"message":"未加白"}')
        ok = FakeResponse('{"code":0,"extract":{"ok":true,"data":"5.6.7.8:9000"}}')
        with patch.object(proxy_mod.httpx, "get", side_effect=[bad, bad, ok]) as get:
            self.assertEqual(fetch_proxy("130100", self.cfg), "http://5.6.7.8:9000")
        self.assertEqual(get.call_count, 3)

    def test_gives_up_after_configured_retries(self):
        bad = FakeResponse('{"code":-1,"message":"未加白"}')
        with patch.object(proxy_mod.httpx, "get", return_value=bad) as get:
            self.assertIsNone(fetch_proxy("130100", self.cfg))
        self.assertEqual(get.call_count, 3, "失败 3 次后应放弃并回退直连")

    def test_network_exception_is_retried_not_raised(self):
        with patch.object(proxy_mod.httpx, "get", side_effect=OSError("boom")) as get:
            self.assertIsNone(fetch_proxy("130100", self.cfg))
        self.assertEqual(get.call_count, 3)

    def test_no_region_never_calls_api(self):
        """没设地区的账号必须直连，绝不能拿全国随机 IP 顶上。"""
        with patch.object(proxy_mod.httpx, "get") as get:
            self.assertIsNone(fetch_proxy("", self.cfg))
            self.assertIsNone(fetch_proxy("all", self.cfg))
            self.assertIsNone(fetch_proxy("999999", self.cfg))
        get.assert_not_called()

    def test_disabled_or_unconfigured_never_calls_api(self):
        with patch.object(proxy_mod.httpx, "get") as get:
            self.assertIsNone(fetch_proxy("130100", {**self.cfg, "enabled": False}))
            self.assertIsNone(fetch_proxy("130100", {**self.cfg, "api_url": ""}))
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
