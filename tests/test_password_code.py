import os
import unittest
from unittest.mock import patch

os.environ.setdefault("AUTH_SECRET", "test-auth-secret-key-32chars!!")

from webui.users import (
    clear_password_code,
    consume_password_code,
    issue_password_code,
)


class PasswordCodeTests(unittest.TestCase):
    def setUp(self):
        clear_password_code("alice")

    def tearDown(self):
        clear_password_code("alice")

    def test_issue_and_consume(self):
        with patch("webui.users.secrets.randbelow", return_value=123456):
            code = issue_password_code("alice")
        self.assertEqual(code, "123456")
        consume_password_code("alice", "123456")
        with self.assertRaises(ValueError) as ctx:
            consume_password_code("alice", "123456")
        self.assertIn("请先发送", str(ctx.exception))

    def test_wrong_code(self):
        with patch("webui.users.secrets.randbelow", return_value=123456):
            issue_password_code("alice")
        with self.assertRaises(ValueError) as ctx:
            consume_password_code("alice", "000000")
        self.assertIn("不对", str(ctx.exception))

    def test_too_many_tries_clears_code(self):
        with patch("webui.users.secrets.randbelow", return_value=123456):
            issue_password_code("alice")
        for _ in range(4):
            with self.assertRaises(ValueError):
                consume_password_code("alice", "000000")
        with self.assertRaises(ValueError) as ctx:
            consume_password_code("alice", "000000")
        self.assertIn("过多", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            consume_password_code("alice", "123456")
        self.assertIn("请先发送", str(ctx.exception))

    def test_empty_code_rejected(self):
        with patch("webui.users.secrets.randbelow", return_value=123456):
            issue_password_code("alice")
        with self.assertRaises(ValueError):
            consume_password_code("alice", "")


if __name__ == "__main__":
    unittest.main()
