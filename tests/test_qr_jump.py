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

    def test_aweme_index_url_is_jump(self):
        url = "https://aweme.snssdk.com/oauth/authorize/?aid=1128&token=abc"
        self.assertTrue(is_app_jump_url(url))
        self.assertFalse(is_image_qr_src(url))
        fields = _jump_fields(url)
        self.assertEqual(fields["app_jump_url"], url)
        self.assertTrue(fields["app_scheme"].startswith("snssdk1128://scan?from=web&url="))
        self.assertTrue(fields["app_scheme_ios"].startswith("aweme://scan?from=web&url="))
        self.assertTrue(fields["app_open_url"].startswith("https://www.douyin.com/open/sdk/ul?schema="))
        self.assertNotIn("aweme.snssdk.com", fields["app_open_url"].split("schema=")[0])

    def test_amemv_landing_is_not_used_as_open_href(self):
        url = "https://api.amemv.com/aweme/v1/fancy/qrconnect/?token=abc"
        fields = _jump_fields(url)
        self.assertTrue(fields["app_open_url"].startswith("https://www.douyin.com/open/sdk/ul?schema="))
        self.assertTrue(fields["app_scheme_ios"].startswith("aweme://scan?"))
        self.assertNotIn("webview", fields["app_scheme"])
        self.assertNotEqual(fields["app_open_url"], url)

    def test_v_douyin_used_as_direct_open(self):
        url = "https://v.douyin.com/AbCdEf/"
        self.assertTrue(is_douyin_app_link(url))
        fields = _jump_fields(url)
        self.assertEqual(fields["app_open_url"], url)
        self.assertEqual(fields["app_open_url_android"], url)
        self.assertTrue(fields["app_scheme"].startswith("snssdk1128://webview?url="))

    def test_existing_scheme_kept(self):
        scheme = "snssdk1128://webview?url=https%3A%2F%2Faweme.snssdk.com%2Fx"
        self.assertEqual(douyin_app_scheme(scheme), scheme)
        self.assertTrue(is_app_jump_url(scheme))

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

    def test_extract_prefers_short_over_landing(self):
        jump = "https://v.douyin.com/AbCdEf/"
        data = {
            "qrcode": "iVBORw0KGgoAAA",
            "qrcode_url": "https://cdn.example.com/qr.png",
            "qrcode_index_url": "https://api.amemv.com/aweme/v1/fancy/qrconnect/?token=abc",
            "short_url": jump,
        }
        self.assertEqual(extract_jump_from_data(data), jump)
        self.assertEqual(
            pick_best_jump(data["qrcode_index_url"], jump),
            jump,
        )

    def test_decode_empty_png(self):
        self.assertEqual(decode_qr_payload(""), "")
        self.assertEqual(decode_qr_payload("AAAA"), "")


if __name__ == "__main__":
    unittest.main()
