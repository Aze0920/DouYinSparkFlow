import os
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("AUTH_SECRET", "test-auth-secret-key-32chars!!")

from webui import invite as invite_mod
from webui import users as users_mod
from webui import notify as notify_mod
from webui.cards import public_card
from webui.invite import (
    apply_invite_register,
    can_invite,
    complete_invite_on_bind,
    preview_invite,
    save_invite_settings,
    set_user_invite_enabled,
)
from webui.notify import set_user_wxpusher
from webui.users import (
    account_limit,
    extend_user,
    find_user,
    is_expired,
    is_permanent,
    is_protected_username,
    make_token,
    make_user,
    now_utc,
    parse_days,
    parse_max_accounts,
    parse_token,
    public_user,
    save_users,
    to_iso,
    touch_login,
    _hash_password,
    _legacy_hash_password,
)


class QuotaTests(unittest.TestCase):
    def test_zero_days_means_unlimited(self):
        self.assertEqual(parse_days(0), 0)
        self.assertEqual(parse_days("0"), 0)
        user = make_user("alice", "pass1234", role="user", days=0, max_accounts=3)
        self.assertTrue(is_permanent(user))
        self.assertIsNone(user.get("expires_at"))
        self.assertEqual(account_limit(user), 3)

    def test_zero_accounts_means_unlimited(self):
        self.assertEqual(parse_max_accounts(0), 0)
        user = make_user("bob", "pass1234", role="user", days=7, max_accounts=0)
        self.assertFalse(is_permanent(user))
        self.assertEqual(account_limit(user), 0)
        self.assertTrue(user.get("expires_at"))

    def test_admin_always_unlimited(self):
        user = make_user("admin", "admin", role="admin", days=1, max_accounts=1)
        self.assertTrue(is_permanent(user))
        self.assertEqual(account_limit(user), 0)
        self.assertTrue(is_protected_username("admin"))
        self.assertTrue(is_protected_username("Admin"))
        self.assertFalse(is_protected_username("alice"))

    def test_card_public_labels(self):
        card = public_card({"code": "DSF-TEST", "days": 0, "max_accounts": 0, "note": "x"})
        self.assertEqual(card["days"], 0)
        self.assertEqual(card["max_accounts"], 0)
        self.assertEqual(card["days_label"], "不限")
        self.assertEqual(card["max_accounts_label"], "账号不限")

    def test_extend_zero_makes_timed_user_permanent(self):
        user = make_user("bob", "pass1234", role="user", days=7, max_accounts=1)
        self.assertFalse(is_permanent(user))
        extend_user(user, 0)
        self.assertTrue(is_permanent(user))
        self.assertIsNone(user.get("expires_at"))
        self.assertEqual(public_user(user)["expires_label"], "永久")
        self.assertEqual(public_user(user)["remain_label"], "永久")

    def test_extend_does_not_downgrade_permanent(self):
        user = make_user("forever", "pass1234", role="user", days=0, max_accounts=1)
        self.assertTrue(is_permanent(user))
        extend_user(user, 5)
        self.assertTrue(is_permanent(user))
        self.assertIsNone(user.get("expires_at"))


class AuthTokenTests(unittest.TestCase):
    def test_token_tamper_rejected(self):
        token = make_token("admin")
        sig, name = token.split(":", 1)
        bad = ("0" * len(sig)) + ":" + name
        self.assertIsNone(parse_token(bad))
        self.assertIsNone(parse_token("not-a-token"))

    def test_password_hash_differs_from_legacy(self):
        self.assertNotEqual(_hash_password("admin", "admin"), _legacy_hash_password("admin", "admin"))

    def test_touch_login_shows_in_public_user(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(users_mod, "USERS_FILE", Path(tmp.name) / "users.json"):
            save_users([make_user("alice", "pass1234", role="user", days=7, max_accounts=1)])
            before = public_user(find_user("alice"))
            self.assertEqual(before["last_login_label"], "-")
            self.assertEqual(before["last_login_ip"], "-")
            touch_login("alice", "203.0.113.9")
            after = public_user(find_user("alice"))
            self.assertNotEqual(after["last_login_label"], "-")
            self.assertEqual(after["last_login_ip"], "203.0.113.9")


class InviteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.users_patch = patch.object(users_mod, "USERS_FILE", root / "users.json")
        self.invite_patch = patch.object(invite_mod, "INVITE_FILE", root / "invite.json")
        self.notify_patch = patch.object(notify_mod, "NOTIFY_FILE", root / "notify.json")
        self.users_patch.start()
        self.invite_patch.start()
        self.notify_patch.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.users_patch.stop)
        self.addCleanup(self.invite_patch.stop)
        self.addCleanup(self.notify_patch.stop)
        save_users([make_user("host", "pass1234", role="user", days=7, max_accounts=1)])
        save_invite_settings({"enabled": True, "inviter_days": 3, "invitee_days": 2})
        self.host_code = set_user_invite_enabled("host", True)["code"]

    def test_invite_register_rewards_after_wechat_bind(self):
        guest = apply_invite_register("guest", "pass1234", self.host_code)
        self.assertEqual(guest.get("invited_by"), "host")
        self.assertTrue(guest.get("invite_pending"))
        self.assertTrue(is_expired(guest))
        host_before = find_user("host")
        exp_before = host_before.get("expires_at")
        rec = invite_mod.load_invite()["records"][0]
        self.assertEqual(rec.get("status"), "pending")
        complete_invite_on_bind("guest")
        guest = find_user("guest")
        self.assertFalse(guest.get("invite_pending"))
        self.assertTrue(guest.get("invite_rewarded"))
        self.assertFalse(is_expired(guest))
        host = find_user("host")
        self.assertGreater(host.get("expires_at") or "", exp_before or "")
        rec = invite_mod.load_invite()["records"][0]
        self.assertEqual(rec.get("status"), "rewarded")
        self.assertEqual(rec.get("inviter_days"), 3)
        self.assertEqual(rec.get("invitee_days"), 2)

    def test_permanent_inviter_does_not_gain_days(self):
        save_users([make_user("host", "pass1234", role="user", days=0, max_accounts=1)])
        guest = apply_invite_register("guest", "pass1234", self.host_code)
        self.assertTrue(is_expired(guest))
        complete_invite_on_bind("guest")
        guest = find_user("guest")
        self.assertFalse(is_permanent(guest))
        host = find_user("host")
        self.assertTrue(is_permanent(host))
        self.assertIsNone(host.get("expires_at"))
        rec = invite_mod.load_invite()["records"][0]
        self.assertTrue(rec.get("inviter_already_permanent"))
        self.assertIsNone(rec.get("inviter_days"))
        self.assertEqual(rec.get("invitee_days"), 2)

    def test_timed_inviter_zero_reward_becomes_permanent(self):
        save_invite_settings({"inviter_days": 0, "invitee_days": 2})
        apply_invite_register("guest4", "pass1234", self.host_code)
        host = find_user("host")
        self.assertFalse(is_permanent(host))
        complete_invite_on_bind("guest4")
        host = find_user("host")
        self.assertTrue(is_permanent(host))
        rec = invite_mod.load_invite()["records"][0]
        self.assertFalse(rec.get("inviter_already_permanent"))
        self.assertEqual(rec.get("inviter_days"), 0)

    def test_wxpusher_uid_only_one_account(self):
        save_users([
            make_user("host", "pass1234", role="user", days=7, max_accounts=1),
            make_user("other", "pass1234", role="user", days=7, max_accounts=1),
        ])
        set_user_wxpusher("host", "UID-ONE")
        with self.assertRaises(ValueError) as ctx:
            set_user_wxpusher("other", "UID-ONE")
        self.assertIn("已绑定", str(ctx.exception))
        set_user_wxpusher("host", "UID-ONE")

    def test_expired_member_cannot_invite(self):
        users = users_mod.load_users()
        for item in users:
            if item.get("username") == "host":
                item["permanent"] = False
                item["expires_at"] = to_iso(now_utc() - timedelta(minutes=1))
        save_users(users)
        host = find_user("host")
        self.assertTrue(is_expired(host))
        self.assertFalse(can_invite(host))
        preview = preview_invite(self.host_code)
        self.assertFalse(preview["valid"])
        with self.assertRaises(ValueError):
            apply_invite_register("guest2", "pass1234", self.host_code)
        with self.assertRaises(ValueError):
            set_user_invite_enabled("host", True)

    def test_user_can_turn_off_invite_without_changing_link(self):
        set_user_invite_enabled("host", False)
        preview = preview_invite(self.host_code)
        self.assertFalse(preview["valid"])
        with self.assertRaises(ValueError):
            apply_invite_register("guest3", "pass1234", self.host_code)
        again = set_user_invite_enabled("host", True)
        self.assertEqual(again["code"], self.host_code)
        preview = preview_invite(self.host_code)
        self.assertTrue(preview["valid"])

    def test_admin_off_blocks_even_if_user_on(self):
        save_invite_settings({"enabled": False})
        host = find_user("host")
        self.assertFalse(can_invite(host))
        preview = preview_invite(self.host_code)
        self.assertFalse(preview["valid"])
        with self.assertRaises(ValueError) as ctx:
            apply_invite_register("guest-admin-off", "pass1234", self.host_code)
        self.assertIn("未开启", str(ctx.exception))
        save_invite_settings({"enabled": True})
        preview = preview_invite(self.host_code)
        self.assertTrue(preview["valid"])
        self.assertTrue(can_invite(find_user("host")))


if __name__ == "__main__":
    unittest.main()
