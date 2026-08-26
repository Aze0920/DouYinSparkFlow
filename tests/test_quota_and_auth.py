import os
import unittest

os.environ.setdefault("AUTH_SECRET", "test-auth-secret-key-32chars!!")

from webui.users import (
    account_limit,
    is_permanent,
    is_protected_username,
    make_token,
    make_user,
    parse_days,
    parse_max_accounts,
    parse_token,
    _hash_password,
    _legacy_hash_password,
)
from webui.cards import public_card


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


class AuthTokenTests(unittest.TestCase):
    def test_token_tamper_rejected(self):
        token = make_token("admin")
        sig, name = token.split(":", 1)
        bad = ("0" * len(sig)) + ":" + name
        self.assertIsNone(parse_token(bad))
        self.assertIsNone(parse_token("not-a-token"))

    def test_password_hash_differs_from_legacy(self):
        self.assertNotEqual(_hash_password("admin", "admin"), _legacy_hash_password("admin", "admin"))


if __name__ == "__main__":
    unittest.main()
