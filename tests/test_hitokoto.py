import unittest
from unittest.mock import patch

import core.msg_builder as msg_builder
import utils.hitokoto as hitokoto


class HitokotoTests(unittest.TestCase):
    def setUp(self):
        hitokoto._REMOTE_CACHE["text"] = ""
        hitokoto._REMOTE_CACHE["ts"] = 0

    def test_never_sends_error_placeholder(self):
        with patch.object(hitokoto, "_fetch_hitokoto", return_value=""):
            with patch.object(hitokoto, "_fetch_jinrishici", return_value=""):
                with patch.object(hitokoto, "get_config", return_value={"hitokotoTypes": ["诗词"]}):
                    text = hitokoto.request_hitokoto()
        self.assertTrue(text)
        self.assertNotIn("[error]", text)

    def test_build_message_uses_fallback_not_error(self):
        with patch.object(hitokoto, "_fetch_hitokoto", return_value=""):
            with patch.object(hitokoto, "_fetch_jinrishici", return_value=""):
                with patch.object(hitokoto, "get_config", return_value={"hitokotoTypes": ["文学"]}):
                    with patch.object(msg_builder, "get_config", return_value={"messageTemplate": "续火花"}):
                        text = msg_builder.build_message("今日火花\n[API]")
        self.assertNotIn("[error]", text)
        self.assertNotIn("[API]", text)
        self.assertIn("今日火花", text)

    def test_caches_remote_success(self):
        with patch.object(hitokoto, "_fetch_hitokoto", return_value="远程一句 —— 测试 (作者)") as fetch:
            with patch.object(hitokoto, "get_config", return_value={"hitokotoTypes": ["文学"]}):
                first = hitokoto.request_hitokoto()
                second = hitokoto.request_hitokoto()
        self.assertEqual(first, second)
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
