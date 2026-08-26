import unittest

from webui.chat_list import (
    harvest_api_conversations,
    is_plausible_spark,
    merge_conversations,
    parse_spark_days,
    parse_spark_near_name,
    spark_from_streak_html,
    spark_from_streak_text,
)


class ChatListTests(unittest.TestCase):
    def test_parse_spark_days(self):
        self.assertEqual(parse_spark_days("王洁 火花 12"), 12)
        self.assertEqual(parse_spark_days("连续互相关心 8 天"), 8)
        self.assertIsNone(parse_spark_days("3天前 在干嘛"))
        self.assertIsNone(parse_spark_days(""))

    def test_parse_spark_near_name(self):
        self.assertEqual(parse_spark_near_name("王洁", "王洁 711 17:09 [盖瑞]今日火花[加一]"), 711)
        self.assertEqual(parse_spark_near_name("帅帅", "帅帅 685 昨天 20:00 自动续火花助手"), 685)
        self.assertIsNone(parse_spark_near_name("折辰客", "折辰客 点燃中 1/3 38分钟前"))
        self.assertIsNone(parse_spark_near_name("阿杰", "阿杰 38分钟前 电脑店"))

    def test_rejects_year_as_spark(self):
        self.assertFalse(is_plausible_spark(2025))
        self.assertFalse(is_plausible_spark(2024))
        self.assertTrue(is_plausible_spark(711))
        self.assertTrue(is_plausible_spark(38))
        self.assertTrue(is_plausible_spark(190))
        self.assertIsNone(parse_spark_near_name("冰点..", "冰点.. 2025-08-26 17:09 你好"))
        self.assertIsNone(parse_spark_near_name("河南小李", "河南小李 2025 17:09 在吗"))
        self.assertEqual(parse_spark_near_name("王洁", "王洁 711 2025-08-26 17:09 [盖瑞]今日火花[加一]"), 711)
        self.assertEqual(parse_spark_near_name("花开富贵", "花开富贵 38 18:08 今日火花"), 38)

    def test_merge_drops_year(self):
        rows = merge_conversations(
            [{"name": "王洁", "kind": "friend", "spark_days": 2025}],
            [{"name": "王洁", "kind": "friend", "spark_days": 711}],
        )
        by_name = {item["name"]: item for item in rows}
        self.assertEqual(by_name["王洁"]["spark_days"], 711)

    def test_merge_spark_first(self):
        rows = merge_conversations(
            [{"name": "巧巧.", "kind": "friend", "spark_days": None}],
            [{"name": "帅帅", "kind": "friend", "spark_days": 685}],
            [{"name": "王洁", "kind": "friend", "spark_days": 711}],
        )
        names = [item["name"] for item in rows if item["kind"] == "friend"]
        self.assertEqual(names, ["王洁", "帅帅", "巧巧."])

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

    def test_harvest_nested_user_info(self):
        payload = {
            "data": {
                "conversation_list": [
                    {
                        "conversation_short_id": "11",
                        "conversation_type": 1,
                        "user_info": {"nickname": "王洁", "remark_name": "洁洁", "sec_uid": "x"},
                    },
                    {
                        "conversation_short_id": "22",
                        "conversation_type": 2,
                        "conversation_core_info": {"name": "家庭群"},
                    },
                ]
            }
        }
        rows = harvest_api_conversations(payload)
        by_name = {item["name"]: item for item in rows}
        self.assertEqual(by_name["洁洁"]["kind"], "friend")
        self.assertEqual(by_name["家庭群"]["kind"], "group")

    def test_streak_html_is_the_only_spark_source(self):
        wangjie = """
        <div data-e2e="conversation-item" class="conversationConversationItemwrapper">
          <div class="conversationConversationItemtitle">王洁</div>
          <div class="commonStreakstreakContainer">
            <img class="commonStreakicon" src="https://lf3-static.bytednsdoc.com/obj/eden-cn/flame_icon/couple/normal_couple.png" alt="">
            <div class="commonStreaknormalText"> 711 </div>
          </div>
        </div>
        """
        no_flame = """
        <div data-e2e="conversation-item" class="conversationConversationItemwrapper">
          <div class="conversationConversationItemtitle">郑州阿杰电脑的粉丝1群</div>
          <div class="ConversationItemTagNextToTitletimeStr">52分钟前</div>
        </div>
        """
        igniting = """
        <div class="commonStreakstreakContainer">点燃中 1/3</div>
        """
        self.assertEqual(spark_from_streak_html(wangjie), 711)
        self.assertEqual(spark_from_streak_text(" 711 "), 711)
        self.assertIsNone(spark_from_streak_html(no_flame))
        self.assertIsNone(spark_from_streak_html(igniting))
        self.assertIsNone(spark_from_streak_text("点燃中 1/3"))

    def test_extract_js_uses_douyin_streak_dom(self):
        from webui.chat_list import EXTRACT_JS
        self.assertIn("commonStreaknormalText", EXTRACT_JS)
        self.assertIn("flame_icon", EXTRACT_JS)
        self.assertIn("conversationConversationItemtitle", EXTRACT_JS)
        self.assertIn('data-e2e="conversation-item"', EXTRACT_JS)

    def test_harvest_ignores_nested_message_numbers(self):
        payload = {
            "name": "郑州阿杰电脑的粉丝1群",
            "conversation_type": 2,
            "messages": [{"text": "50", "streak": 8}],
        }
        rows = harvest_api_conversations(payload)
        by_name = {item["name"]: item for item in rows}
        self.assertEqual(by_name["郑州阿杰电脑的粉丝1群"]["kind"], "group")
        self.assertIsNone(by_name["郑州阿杰电脑的粉丝1群"]["spark_days"])


if __name__ == "__main__":
    unittest.main()
