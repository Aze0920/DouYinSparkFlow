import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webui import session_store as store
from webui.chat_list import (
    clean_avatar_url,
    fresh_spark_days,
    harvest_api_conversations,
    is_plausible_spark,
    merge_conversations,
    parse_spark_days,
    parse_spark_near_name,
    spark_from_streak_html,
    spark_from_streak_text,
    spark_from_title_row,
    update_spark_snapshot,
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

    def test_merge_keeps_old_spark_if_dom_misses_it(self):
        rows = merge_conversations(
            [{"name": "家乐", "kind": "friend", "spark_days": 26, "avatar": ""}],
            [{"name": "家乐", "kind": "friend", "spark_days": None, "avatar": ""}],
        )
        self.assertEqual(rows[0]["spark_days"], 26)

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
        fake_eight = """
        <div data-e2e="conversation-item" class="conversationConversationItemwrapper">
          <div class="conversationConversationItemtitleWrapper">
            <div class="conversationConversationItemtitle">彩虹糖</div>
            <div class="ConversationItemTagNextToTitlewrapper">
              <div class="ConversationItemTagNextToTitleleft">
                <div class="ConversationItemTagNextToTitletimeStr">8分钟前</div>
              </div>
            </div>
          </div>
          <div class="ConversationItemDescwrapper">
            <pre>连续互相关心 8 天 [分享视频]</pre>
          </div>
        </div>
        """
        self.assertIsNone(spark_from_streak_html(fake_eight))
        self.assertIsNone(spark_from_title_row("彩虹糖", "彩虹糖 8分钟前"))
        no_img = """
        <div data-e2e="conversation-item" class="conversationConversationItemwrapper">
          <div class="conversationConversationItemtitleWrapper">
            <div class="conversationConversationItemtitle">家乐</div>
            <div class="commonStreakstreakContainer">
              <div class="commonStreaknormalText">26</div>
            </div>
          </div>
        </div>
        """
        self.assertEqual(spark_from_streak_html(no_img), 26)

    def test_orange_friend_flame_from_title_row(self):
        from webui.chat_list import spark_from_title_row
        self.assertEqual(spark_from_title_row("凯凯", "凯凯 111 18:26"), 111)
        self.assertEqual(spark_from_title_row("静静", "静静 333 16:53"), 333)
        self.assertEqual(spark_from_title_row("花开富贵", "花开富贵 38 18:08"), 38)
        self.assertIsNone(spark_from_title_row("开奶瓶本开", "开奶瓶本开 18:26"))
        kaikai = """
        <div data-e2e="conversation-item" class="conversationConversationItemwrapper">
          <div class="conversationConversationItemtitleWrapper">
            <div class="conversationConversationItemtitle">凯凯</div>
            <div class="ConversationItemTagNextToTitlewrapper">
              <div class="ConversationItemTagNextToTitleleft">
                <img src="https://lf3-static.bytednsdoc.com/obj/eden-cn/tp_upfbvk/ljhwZthlaukjlkulzlp/flame_icon/friend/normal.png" alt="">
                <span>111</span>
              </div>
              <div class="ConversationItemTagNextToTitleleft">
                <div class="ConversationItemTagNextToTitletimeStr">18:26</div>
              </div>
            </div>
          </div>
          <div class="ConversationItemDescwrapper ConversationItemDeschintAndTimeWrapper">
            <pre class="ConversationItemHinttextBox">[分享视频] 50</pre>
          </div>
        </div>
        """
        jingjing = """
        <div data-e2e="conversation-item" class="conversationConversationItemwrapper">
          <div class="conversationConversationItemtitleWrapper">
            <div class="conversationConversationItemtitle">静静</div>
            <div class="ConversationItemTagNextToTitlewrapper">
              <div class="ConversationItemTagNextToTitleleft">
                <img src="https://lf3-static.bytednsdoc.com/obj/eden-cn/tp_upfbvk/ljhwZthlaukjlkulzlp/flame_icon/friend/normal.png" alt="">
                <span style="color: rgb(255, 140, 60);">333</span>
              </div>
              <div class="ConversationItemTagNextToTitleleft">
                <div class="ConversationItemTagNextToTitletimeStr">16:53</div>
              </div>
            </div>
          </div>
          <div class="ConversationItemDescwrapper">
            <pre>1</pre>
          </div>
        </div>
        """
        self.assertEqual(spark_from_streak_html(kaikai), 111)
        self.assertEqual(spark_from_streak_html(jingjing), 333)

    def test_extract_js_uses_douyin_streak_dom(self):
        from webui.chat_list import EXTRACT_JS
        self.assertIn("commonStreaknormalText", EXTRACT_JS)
        self.assertIn("flame_icon", EXTRACT_JS)
        self.assertIn("conversationConversationItemtitle", EXTRACT_JS)
        self.assertIn('data-e2e="conversation-item"', EXTRACT_JS)
        self.assertIn("aweme-avatar", EXTRACT_JS)
        self.assertIn("fromStreakText", EXTRACT_JS)
        self.assertIn("hasSparkWidget", EXTRACT_JS)
        self.assertNotIn("hasFlame", EXTRACT_JS)
        self.assertIn("titleWrapper", EXTRACT_JS)

    def test_avatar_url_is_https_only(self):
        self.assertEqual(clean_avatar_url("data:image/png;base64,xxxx"), "")
        self.assertEqual(clean_avatar_url("javascript:alert(1)"), "")
        url = clean_avatar_url("https://p3.douyinpic.com/img/aweme-avatar/tos-cn.webp")
        self.assertTrue(url.startswith("https://"))

    def test_merge_keeps_avatar(self):
        rows = merge_conversations(
            [{"name": "王洁", "kind": "friend", "spark_days": 711, "avatar": "https://p3.douyinpic.com/a.webp"}],
        )
        self.assertEqual(rows[0]["avatar"], "https://p3.douyinpic.com/a.webp")

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


class SparkSnapshotTests(unittest.TestCase):
    """续火花任务跑完要把新天数写回快照，账号列表才不会一直空着。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(store, "SESSION_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self):
        return (store.load_chats("acc1") or {}).get("items") or []

    def test_fills_in_days_for_an_account_that_never_had_any(self):
        """挂了好几天却一直不显示天数的号，就是快照里从来没写进过火花。"""
        store.save_chats("acc1", [{"name": "Darling", "kind": "friend", "spark_days": None, "avatar": "u"}])
        self.assertEqual(update_spark_snapshot("acc1", [{"name": "Darling", "spark_days": 12}]), 1)
        row = self.rows()[0]
        self.assertEqual(row["spark_days"], 12)
        self.assertEqual(row["avatar"], "u", "刷新天数不该把头像冲掉")

    def test_lower_reading_overwrites_stale_high_value(self):
        """火花断了会从头数起，取较大值的话旧天数就永远赖着不走。"""
        store.save_chats("acc1", [{"name": "帅帅", "kind": "friend", "spark_days": 84}])
        update_spark_snapshot("acc1", [{"name": "帅帅", "spark_days": 1}])
        self.assertEqual(self.rows()[0]["spark_days"], 1)

    def test_keeps_rows_it_did_not_see_this_run(self):
        store.save_chats(
            "acc1",
            [
                {"name": "帅帅", "kind": "friend", "spark_days": 84},
                {"name": "凯凯", "kind": "friend", "spark_days": 112},
            ],
            "https://me.png",
        )
        update_spark_snapshot("acc1", [{"name": "帅帅", "spark_days": 85}])
        by_name = {row["name"]: row for row in self.rows()}
        self.assertEqual(by_name["帅帅"]["spark_days"], 85)
        self.assertEqual(by_name["凯凯"]["spark_days"], 112, "这次没扫到的人要保持原样")
        self.assertEqual((store.load_chats("acc1") or {}).get("self_avatar"), "https://me.png")

    def test_appends_names_missing_from_snapshot(self):
        store.save_chats("acc1", [{"name": "帅帅", "kind": "friend", "spark_days": 84}])
        update_spark_snapshot("acc1", [{"name": "新朋友", "kind": "group", "spark_days": 3}])
        by_name = {row["name"]: row for row in self.rows()}
        self.assertEqual(by_name["新朋友"]["spark_days"], 3)
        self.assertEqual(by_name["新朋友"]["kind"], "group")

    def test_junk_readings_never_clear_existing_days(self):
        store.save_chats("acc1", [{"name": "帅帅", "kind": "friend", "spark_days": 84}])
        junk = [
            {"name": "帅帅", "spark_days": None},
            {"name": "", "spark_days": 5},
            {"name": "小明", "spark_days": 2025},
            {"name": "小红", "spark_days": "abc"},
        ]
        self.assertEqual(update_spark_snapshot("acc1", junk), 0)
        self.assertEqual(self.rows()[0]["spark_days"], 84, "没读到就别动，不能把已有天数抹空")

    def test_no_account_id_is_a_noop(self):
        self.assertEqual(update_spark_snapshot("", [{"name": "帅帅", "spark_days": 9}]), 0)

    def test_account_list_prefers_snapshot_over_saved_days(self):
        store.save_chats("acc1", [{"name": "帅帅", "kind": "friend", "spark_days": 85}])
        got = fresh_spark_days({"unique_id": "acc1", "target_sparks": {"帅帅": 84, "凯凯": 112}})
        self.assertEqual(got, {"帅帅": 85, "凯凯": 112}, "快照里没有的人要保留账号上存的天数")

    def test_account_list_falls_back_when_no_snapshot(self):
        self.assertEqual(
            fresh_spark_days({"unique_id": "nope", "target_sparks": {"帅帅": 84}}),
            {"帅帅": 84},
        )
        self.assertEqual(fresh_spark_days({"target_sparks": {"帅帅": 84}}), {"帅帅": 84})

    def test_account_list_ignores_rows_without_a_real_spark(self):
        store.save_chats(
            "acc1",
            [
                {"name": "帅帅", "kind": "friend", "spark_days": None},
                {"name": "小明", "kind": "friend", "spark_days": 2025},
            ],
        )
        self.assertEqual(
            fresh_spark_days({"unique_id": "acc1", "target_sparks": {"帅帅": 84}}),
            {"帅帅": 84},
        )


if __name__ == "__main__":
    unittest.main()
