import os
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("AUTH_SECRET", "test-auth-secret-key-32chars!!")

from webui import notify as notify_mod
from webui import users as users_mod
from webui.invite import apply_invite_register, complete_invite_on_bind, save_invite_settings, set_user_invite_enabled
from webui import invite as invite_mod
from webui.notify import (
    broadcast_to_bound,
    default_notify,
    load_notify,
    notify_invite_rewards,
    notify_recharge_success,
    render_wxpusher_html,
    save_notify,
    tick_expire_reminders,
)
from webui.users import find_user, make_user, now_utc, save_users, to_iso


class NotifyEventsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.users_patch = patch.object(users_mod, "USERS_FILE", root / "users.json")
        self.notify_patch = patch.object(notify_mod, "NOTIFY_FILE", root / "notify.json")
        self.invite_patch = patch.object(invite_mod, "INVITE_FILE", root / "invite.json")
        self.users_patch.start()
        self.notify_patch.start()
        self.invite_patch.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.users_patch.stop)
        self.addCleanup(self.notify_patch.stop)
        self.addCleanup(self.invite_patch.stop)
        save_notify(
            {
                "wxpusher": {"enabled": True, "app_token": "AT_test"},
                "events": {
                    "task_done": True,
                    "task_fail": True,
                    "cookie_offline": True,
                    "expire_soon": True,
                    "recharge": True,
                    "invite_reward": True,
                },
            }
        )

    def _bound_user(self, name, hours, uid="UID_" + "x"):
        user = make_user(name, "pass1234", role="user", days=7, max_accounts=1)
        user["expires_at"] = to_iso(now_utc() + timedelta(hours=hours))
        user["permanent"] = False
        user["wxpusher_uid"] = uid
        return user

    def test_default_events_include_new_keys(self):
        events = default_notify()["events"]
        for key in ("expire_soon", "recharge", "invite_reward"):
            self.assertTrue(events[key])
        loaded = load_notify()["events"]
        for key in ("expire_soon", "recharge", "invite_reward"):
            self.assertTrue(loaded[key])

    def test_expire_24h_once_then_12h_once(self):
        save_users([self._bound_user("alice", 20, "UID_A")])
        with patch.object(notify_mod, "notify_event") as mocked:
            first = tick_expire_reminders()
            self.assertEqual(first["sent"], 1)
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(mocked.call_args[0][0], "expire_soon")
            self.assertIn("24 小时", mocked.call_args[0][2])
            alice = find_user("alice")
            self.assertTrue(alice.get("expire_notice_24h"))
            self.assertFalse(alice.get("expire_notice_12h"))
            tick_expire_reminders()
            self.assertEqual(mocked.call_count, 1)

        save_users([self._bound_user("bob", 10, "UID_B")])
        with patch.object(notify_mod, "notify_event") as mocked:
            result = tick_expire_reminders()
            self.assertEqual(result["sent"], 1)
            self.assertIn("12 小时", mocked.call_args[0][2])
            bob = find_user("bob")
            self.assertTrue(bob.get("expire_notice_12h"))
            tick_expire_reminders()
            self.assertEqual(mocked.call_count, 1)

    def test_expire_skips_unbound_permanent_and_far_away(self):
        far = self._bound_user("far", 48, "UID_F")
        unbound = self._bound_user("none", 10, "")
        unbound["wxpusher_uid"] = ""
        admin = make_user("admin", "pass1234", role="admin")
        save_users([far, unbound, admin])
        with patch.object(notify_mod, "notify_event") as mocked:
            result = tick_expire_reminders()
            self.assertEqual(result["sent"], 0)
            mocked.assert_not_called()

    def test_expire_resets_after_new_expiry(self):
        user = self._bound_user("alice", 20, "UID_A")
        save_users([user])
        with patch.object(notify_mod, "notify_event"):
            tick_expire_reminders()
        alice = find_user("alice")
        self.assertTrue(alice.get("expire_notice_24h"))
        alice["expires_at"] = to_iso(now_utc() + timedelta(days=10))
        save_users([alice])
        with patch.object(notify_mod, "notify_event") as mocked:
            result = tick_expire_reminders()
            self.assertEqual(result["sent"], 0)
            mocked.assert_not_called()
            again = find_user("alice")
            self.assertFalse(again.get("expire_notice_24h"))
            self.assertFalse(again.get("expire_notice_12h"))

    def test_broadcast_only_bound_uids(self):
        bound = self._bound_user("alice", 48, "UID_A")
        other = self._bound_user("bob", 48, "UID_B")
        skip = make_user("carol", "pass1234", role="user", days=7)
        skip["wxpusher_uid"] = ""
        save_users([bound, other, skip])
        with patch.object(notify_mod, "send_wxpusher") as mocked:
            result = broadcast_to_bound("公告", "今晚维护")
            self.assertEqual(result["sent"], 2)
            mocked.assert_called_once()
            uids = mocked.call_args.kwargs.get("uids") or mocked.call_args[1][0]
            self.assertEqual(sorted(uids), ["UID_A", "UID_B"])
        with self.assertRaises(RuntimeError):
            broadcast_to_bound("公告", "")

    def test_broadcast_empty_when_nobody_bound(self):
        save_users([make_user("carol", "pass1234", role="user", days=7)])
        with self.assertRaises(RuntimeError):
            broadcast_to_bound("公告", "今晚维护")

    def test_recharge_and_invite_notify_user_events(self):
        user = self._bound_user("alice", 72, "UID_A")
        save_users([user])
        with patch.object(notify_mod, "notify_event") as mocked:
            notify_recharge_success("alice", {"days": 7, "max_accounts": 2}, user)
            self.assertEqual(mocked.call_args[0][0], "recharge")
            self.assertEqual(mocked.call_args[0][1], "充值成功")
            self.assertIn("7 天", mocked.call_args[0][2])
            notify_invite_rewards(
                "alice",
                "host",
                invitee_days=2,
                awarded_inviter_days=3,
                invitee=user,
            )
            kinds = [call.args[0] for call in mocked.call_args_list]
            self.assertIn("invite_reward", kinds)

    def test_invite_bind_sends_reward_notify(self):
        save_users([make_user("host", "pass1234", role="user", days=7, max_accounts=1)])
        save_invite_settings({"enabled": True, "inviter_days": 3, "invitee_days": 2})
        code = set_user_invite_enabled("host", True)["code"]
        apply_invite_register("guest", "pass1234", code)
        with patch.object(notify_mod, "notify_event") as mocked:
            complete_invite_on_bind("guest")
            kinds = [call.args[0] for call in mocked.call_args_list]
            self.assertEqual(kinds.count("invite_reward"), 2)
            titles = [call.args[1] for call in mocked.call_args_list]
            self.assertIn("邀请奖励已到账", titles)
            self.assertIn("邀请成功", titles)

    def test_wxpusher_html_card_and_escape(self):
        card = render_wxpusher_html(
            "邀请成功",
            "好友 test 已绑定微信。你是永久会员，未再加时长",
            kind="invite_reward",
            rows=[
                {"label": "好友", "value": "test"},
                {"label": "你的奖励", "value": "永久会员，未再加时长"},
            ],
            footer="邀请成功以好友绑定微信为准",
        )
        self.assertIn("已完成", card)
        self.assertIn("好友", card)
        self.assertIn("test", card)
        self.assertIn("永久会员，未再加时长", card)
        self.assertIn("border-radius", card)
        self.assertIn("data-darkmode-color", card)
        self.assertNotIn("邀请成功\n好友", card)
        escaped = render_wxpusher_html("测试", "<script>alert(1)</script>")
        self.assertIn("&lt;script&gt;", escaped)
        self.assertNotIn("<script>alert", escaped)
        code = render_wxpusher_html(
            "改密验证码",
            "",
            kind="password_code",
            copy_text="123456",
            rows=[{"label": "有效期", "value": "10 分钟内有效"}],
        )
        self.assertIn('data-clipboard-text="123456"', code)
        self.assertIn("安全验证", code)
        self.assertIn('"contentType": 2', (Path(__file__).resolve().parents[1] / "webui" / "notify.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
