import unittest

from webui.legal_docs import PRIVACY_HTML, TERMS_HTML


class LegalDocsTests(unittest.TestCase):
    def test_branded_as_sparkflow(self):
        self.assertIn("SparkFlow", TERMS_HTML)
        self.assertIn("SparkFlow", PRIVACY_HTML)
        self.assertNotIn("Blazfire", TERMS_HTML)
        self.assertNotIn("Blazfire", PRIVACY_HTML)

    def test_privacy_matches_actual_data(self):
        self.assertIn("不可逆哈希", PRIVACY_HTML)
        self.assertNotIn("邮箱地址", PRIVACY_HTML)
        self.assertNotIn("AES-256", PRIVACY_HTML)
        self.assertIn("Cookie", PRIVACY_HTML)

    def test_terms_cover_virtual_goods(self):
        self.assertIn("虚拟数字商品", TERMS_HTML)
        self.assertIn("不支持", TERMS_HTML)
        self.assertIn("第三方", TERMS_HTML)


if __name__ == "__main__":
    unittest.main()
