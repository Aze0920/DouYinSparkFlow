import unittest

from webui.chat_list import harvest_api_conversations, merge_conversations, parse_spark_days


class ChatListTests(unittest.TestCase):
    def test_parse_spark_days(self):
        self.assertEqual(parse_spark_days("王洁 火花 12"), 12)
        self.assertEqual(parse_spark_days("连续互相关心 8 天"), 8)
        self.assertIsNone(parse_spark_days("3天前 在干嘛"))
        self.assertIsNone(parse_spark_days(""))

    def test_merge_prefers_group_and_spark(self):
        rows = merge_conversations(
            [{"name": "家庭群", "kind": "friend", "spark_days": None}],
            [{"name": "家庭群", "kind": "group", "spark_days": 4}],
            [{"name": "王洁", "kind": "friend", "spark_days": 12}],
        )
        by_name = {item["name"]: item for item in rows}
        self.assertEqual(by_name["家庭群"]["kind"], "group")
        self.assertEqual(by_name["家庭群"]["spark_days"], 4)
        self.assertEqual(by_name["王洁"]["spark_days"], 12)

    def test_harvest_api_conversations(self):
        payload = {
            "data": [
                {"name": "思文", "conversation_type": 1, "streak": 9},
                {"name": "同事群", "conversation_type": 2},
            ]
        }
        rows = harvest_api_conversations(payload)
        by_name = {item["name"]: item for item in rows}
        self.assertEqual(by_name["思文"]["kind"], "friend")
        self.assertEqual(by_name["思文"]["spark_days"], 9)
        self.assertEqual(by_name["同事群"]["kind"], "group")


if __name__ == "__main__":
    unittest.main()
