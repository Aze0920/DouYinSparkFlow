import unittest

from webui.http_log import filter_probe_lines, is_app_path, is_probe_log_line


class AppPathTests(unittest.TestCase):
    def test_real_pages_and_apis_count(self):
        for path in ("/", "/home", "/logs", "/api/run", "/api/logs", "/static/app.css"):
            self.assertTrue(is_app_path(path), path)

    def test_scanner_paths_do_not_count(self):
        for path in ("/wp-admin", "/cgi-bin/luci", "/phpunit", "/.env", "/api.php"):
            self.assertFalse(is_app_path(path), path)


class ProbeLogLineTests(unittest.TestCase):
    def test_keeps_spark_and_real_api_lines(self):
        lines = [
            "2026-09-06 07:17:19 - app - INFO - tasks.py:715 - 发送核对 凯凯 预览「旧」→「」 气泡 0→0",
            "2026-09-06 07:17:20 - app - INFO - app.py:218 - 请求 POST /api/run",
            "2026-09-06 07:10:01 - app - WARNING - app.py:218 - 请求失败 GET /wp-admin -> 404",
            "2026-09-06 07:10:02 - app - INFO - app.py:211 - 请求 GET /cgi-bin/luci",
        ]
        kept = filter_probe_lines(lines)
        self.assertEqual(len(kept), 2)
        self.assertIn("发送核对", kept[0])
        self.assertIn("/api/run", kept[1])

    def test_probe_helper_matches_warning_and_info(self):
        self.assertTrue(is_probe_log_line("请求失败 GET /wp-admin -> 404"))
        self.assertFalse(is_probe_log_line("请求 POST /api/account"))


if __name__ == "__main__":
    unittest.main()
