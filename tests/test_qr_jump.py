import unittest

from webui.qr_login import (
    douyin_app_scheme,
    extract_jump_from_data,
    is_app_jump_url,
    is_image_qr_src,
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
        self.assertTrue(fields["app_scheme"].startswith("snssdk1128://webview?url="))

    def test_existing_scheme_kept(self):
        scheme = "snssdk1128://webview?url=https%3A%2F%2Faweme.snssdk.com%2Fx"
        self.assertEqual(douyin_app_scheme(scheme), scheme)
        self.assertTrue(is_app_jump_url(scheme))

    def test_empty_rejected(self):
        self.assertEqual(_jump_fields(""), {"app_jump_url": "", "app_scheme": ""})
        self.assertEqual(douyin_app_scheme(""), "")

    def test_any_https_index_url_counts(self):
        url = "https://foo.example.com/scan?token=abc"
        self.assertTrue(is_app_jump_url(url))
        self.assertEqual(extract_jump_from_data({"qrcode": "iVBORxxx", "qrcode_index_url": url}), url)

    def test_extract_prefers_index_over_image(self):
        jump = "https://v.douyin.com/AbCdEf/"
        data = {
            "qrcode": "iVBORw0KGgoAAA",
            "qrcode_url": "https://cdn.example.com/qr.png",
            "qrcode_index_url": jump,
        }
        self.assertEqual(extract_jump_from_data(data), jump)


if __name__ == "__main__":
    unittest.main()
