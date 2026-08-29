import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webui import proxy as proxy_mod
from webui.proxy import (
    DEFAULT_BASE_URL,
    build_url,
    fetch_proxy,
    list_accounts,
    load_proxy,
    mask_key,
    parse_extract,
    public_proxy,
    save_proxy,
)
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
    def test_builds_extract_url_from_key_and_params(self):
        cfg = {"api_key": "SECRET", "base_url": DEFAULT_BASE_URL}
        url = build_url(cfg, phone="138", area="130100", minute=10)
        self.assertTrue(url.startswith("https://ba.cd/ip/extract.php?"))
        self.assertIn("key=SECRET", url)
        self.assertIn("phone=138", url)
        self.assertIn("area=130100", url)
        self.assertIn("minute=10", url)

    def test_builds_accounts_url(self):
        url = build_url({"api_key": "K", "base_url": DEFAULT_BASE_URL}, action="accounts")
        self.assertIn("action=accounts", url)
        self.assertIn("key=K", url)

    def test_key_in_base_url_is_replaced_not_duplicated(self):
        """用户可能连 key 一起贴进接口地址，不能出现两个 key 参数。"""
        cfg = {"api_key": "NEW", "base_url": "https://ba.cd/ip/extract.php?key=OLD"}
        url = build_url(cfg, area="130100")
        self.assertIn("key=NEW", url)
        self.assertNotIn("OLD", url)
        self.assertEqual(url.count("key="), 1)


class MaskTests(unittest.TestCase):
    def test_key_is_masked(self):
        masked = mask_key("abcdef123456")
        self.assertNotIn("abcdef123456", masked)
        self.assertIn("*", masked)

    def test_short_key_fully_masked(self):
        self.assertEqual(mask_key("abc"), "***")
        self.assertEqual(mask_key(""), "")


class ProxyConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = patch.object(proxy_mod, "PROXY_FILE", Path(self.tmp.name) / "proxy.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_defaults_and_roundtrip(self):
        data = load_proxy()
        self.assertFalse(data["enabled"])
        self.assertEqual(data["base_url"], DEFAULT_BASE_URL)
        save_proxy({"enabled": True, "api_key": "K", "phone": "13800000000"})
        data = load_proxy()
        self.assertTrue(data["enabled"])
        self.assertEqual(data["api_key"], "K")
        self.assertEqual(data["phone"], "13800000000")

    def test_masked_key_does_not_overwrite_stored_key(self):
        """前端回显的是打码密钥，原样提交回来不能把真密钥冲掉。"""
        save_proxy({"enabled": True, "api_key": "REALKEY123", "phone": "1"})
        masked = public_proxy()["api_key"]
        save_proxy({"enabled": True, "api_key": masked, "phone": "1"})
        self.assertEqual(load_proxy()["api_key"], "REALKEY123")

    def test_public_hides_the_key_but_shows_phone(self):
        save_proxy({"api_key": "REALKEY123", "phone": "13800000000"})
        pub = public_proxy()
        self.assertNotIn("REALKEY123", pub["api_key"])
        self.assertTrue(pub["api_key_set"])
        self.assertEqual(pub["phone"], "13800000000")
        self.assertTrue(pub["ready"])

    def test_not_ready_without_phone(self):
        save_proxy({"api_key": "K"})
        self.assertFalse(public_proxy()["ready"])

    def test_phone_keeps_digits_only(self):
        save_proxy({"api_key": "K", "phone": " 138-0000-0000 "})
        self.assertEqual(load_proxy()["phone"], "13800000000")


class ListAccountsTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"api_key": "K", "base_url": DEFAULT_BASE_URL}

    def test_parses_documented_accounts_shape(self):
        body = {
            "code": 0,
            "count": 2,
            "accounts": [
                {"phone": "13800000000", "name": "张三", "no": "1", "balance": "12.5", "ready": True},
                {"phone": "13900000000", "name": "李四", "no": "", "balance": "", "ready": False},
            ],
        }
        with patch.object(proxy_mod.httpx, "get", return_value=FakeResponse(json.dumps(body))) as get:
            rows = list_accounts(self.cfg)
        self.assertIn("action=accounts", get.call_args[0][0])
        self.assertEqual([r["phone"] for r in rows], ["13800000000", "13900000000"])
        self.assertTrue(rows[0]["ready"])
        self.assertFalse(rows[1]["ready"])

    def test_bad_key_raises_with_server_message(self):
        body = '{"code":-1,"message":"缺少或错误的 API 密钥"}'
        with patch.object(proxy_mod.httpx, "get", return_value=FakeResponse(body, 401)):
            with self.assertRaises(ValueError) as ctx:
                list_accounts(self.cfg)
        self.assertIn("API 密钥", str(ctx.exception))

    def test_html_response_raises_readable_error(self):
        with patch.object(proxy_mod.httpx, "get", return_value=FakeResponse("<html>404</html>", 404)):
            with self.assertRaises(ValueError) as ctx:
                list_accounts(self.cfg)
        self.assertIn("JSON", str(ctx.exception))

    def test_requires_key(self):
        with patch.object(proxy_mod.httpx, "get") as get:
            with self.assertRaises(ValueError):
                list_accounts({"api_key": "", "base_url": DEFAULT_BASE_URL})
        get.assert_not_called()


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class FetchProxyTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "enabled": True,
            "api_key": "K",
            "phone": "13800000000",
            "base_url": DEFAULT_BASE_URL,
        }
        sleep = patch.object(proxy_mod.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_returns_playwright_ready_server(self):
        ok = FakeResponse('{"code":0,"extract":{"ok":true,"data":"1.2.3.4:20000"}}')
        with patch.object(proxy_mod.httpx, "get", return_value=ok) as get:
            self.assertEqual(fetch_proxy("130100", self.cfg), "http://1.2.3.4:20000")
        url = get.call_args[0][0]
        self.assertIn("area=130100", url)
        self.assertIn("phone=13800000000", url)
        self.assertIn(f"minute={proxy_mod.MINUTE}", url)

    def test_no_phone_never_calls_api(self):
        """还没选套餐账号时不能瞎调接口。"""
        with patch.object(proxy_mod.httpx, "get") as get:
            self.assertIsNone(fetch_proxy("130100", {**self.cfg, "phone": ""}))
        get.assert_not_called()

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
        self.assertEqual(get.call_count, proxy_mod.RETRIES, "失败 3 次后应放弃并回退直连")

    def test_network_exception_is_retried_not_raised(self):
        with patch.object(proxy_mod.httpx, "get", side_effect=OSError("boom")) as get:
            self.assertIsNone(fetch_proxy("130100", self.cfg))
        self.assertEqual(get.call_count, proxy_mod.RETRIES)

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
            self.assertIsNone(fetch_proxy("130100", {**self.cfg, "api_key": ""}))
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
