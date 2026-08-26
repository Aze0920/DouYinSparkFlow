import unittest

from webui.qr_login import (
    decode_qr_payload,
    douyin_app_scheme,
    extract_jump_from_data,
    is_app_jump_url,
    is_douyin_app_link,
    is_image_qr_src,
    pick_best_jump,
    _jump_fields,
)


class QrJumpTests(unittest.TestCase):
    def test_image_src_not_jump(self):
        self.assertTrue(is_image_qr_src("https://cdn.example.com/qr.png"))
        self.assertTrue(is_image_qr_src("data:image/png;base64,AAAA"))
        self.assertFalse(is_app_jump_url("https://cdn.example.com/qr.png"))
        self.assertFalse(is_app_jump_url("data:image/png;base64,AAAA"))

    def test_landing_url_opens_webview_not_scan(self):
        url = "https://aweme.snssdk.com/oauth/authorize/?aid=1128&token=abc"
        self.assertTrue(is_app_jump_url(url))
        fields = _jump_fields(url)
        self.assertEqual(fields["app_jump_url"], url)
        self.assertTrue(fields["app_scheme"].startswith("snssdk1128://webview?url="))
        self.assertTrue(fields["app_scheme_ios"].startswith("aweme://webview?url="))
        self.assertTrue(fields["app_open_url"].startswith("https://www.douyin.com/open/sdk/ul?schema="))
        self.assertTrue(fields["app_open_url_android"].startswith("intent://aweme.snssdk.com/"))
        self.assertIn("com.ss.android.ugc.aweme", fields["app_open_url_android"])
        self.assertNotIn("scan?", fields["app_scheme"])
        self.assertNotEqual(fields["app_open_url"], url)

    def test_amemv_not_used_as_safari_href(self):
        url = "https://api.amemv.com/aweme/v1/fancy/qrconnect/?token=abc"
        fields = _jump_fields(url)
        self.assertTrue(fields["app_open_url"].startswith("https://www.douyin.com/open/sdk/ul?schema="))
        self.assertTrue(fields["app_scheme_ios"].startswith("aweme://webview?url="))
        self.assertFalse(fields["app_open_url"].startswith(("aweme://", "snssdk1128://", "intent://")))
        self.assertTrue(fields["app_open_url_android"].startswith("intent://api.amemv.com/"))
        self.assertNotEqual(fields["app_open_url"], url)
        self.assertIn("append_common_params%3D1", fields["app_scheme_ios"])

    def test_v_douyin_not_used_as_direct_open(self):
        url = "https://v.douyin.com/AbCdEf/"
        self.assertTrue(is_douyin_app_link(url))
        fields = _jump_fields(url)
        self.assertTrue(fields["app_open_url"].startswith("https://www.douyin.com/open/sdk/ul?schema="))
        self.assertTrue(fields["app_scheme_ios"].startswith("aweme://webview?url="))
        self.assertTrue(fields["app_open_url_android"].startswith("intent://v.douyin.com/"))
        self.assertNotEqual(fields["app_open_url"], url)

    def test_existing_scheme_kept(self):
        scheme = "snssdk1128://webview?url=https%3A%2F%2Faweme.snssdk.com%2Fx"
        self.assertEqual(douyin_app_scheme(scheme), scheme)
        self.assertTrue(is_app_jump_url(scheme))
        fields = _jump_fields(scheme)
        self.assertEqual(fields["app_scheme"], scheme)
        self.assertTrue(fields["app_scheme_ios"].startswith("aweme://"))
        self.assertTrue(fields["app_open_url"].startswith("https://www.douyin.com/open/sdk/ul?schema="))

    def test_empty_rejected(self):
        fields = _jump_fields("")
        self.assertEqual(fields["app_jump_url"], "")
        self.assertEqual(fields["app_scheme"], "")
        self.assertEqual(fields["app_open_url"], "")
        self.assertEqual(douyin_app_scheme(""), "")

    def test_any_https_index_url_counts(self):
        url = "https://foo.example.com/scan?token=abc"
        self.assertTrue(is_app_jump_url(url))
        self.assertEqual(extract_jump_from_data({"qrcode": "iVBORxxx", "qrcode_index_url": url}), url)

    def test_extract_prefers_landing_over_short(self):
        short = "https://v.douyin.com/AbCdEf/"
        landing = "https://api.amemv.com/aweme/v1/fancy/qrconnect/?token=abc"
        data = {
            "qrcode": "iVBORw0KGgoAAA",
            "qrcode_url": "https://cdn.example.com/qr.png",
            "qrcode_index_url": landing,
            "short_url": short,
        }
        self.assertEqual(extract_jump_from_data(data), landing)
        self.assertEqual(pick_best_jump(short, landing), landing)

    def test_decode_empty_png(self):
        self.assertEqual(decode_qr_payload(""), "")
        self.assertEqual(decode_qr_payload("AAAA"), "")


if __name__ == "__main__":
    unittest.main()
