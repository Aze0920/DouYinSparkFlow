import json
import time
import unittest
from pathlib import Path

from webui import session_store


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self._old = session_store.SESSION_DIR
        self.tmp = Path(self._old.parent) / "_test_sessions"
        self.tmp.mkdir(parents=True, exist_ok=True)
        session_store.SESSION_DIR = self.tmp

    def tearDown(self):
        session_store.SESSION_DIR = self._old
        for path in self.tmp.glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def test_safe_id_strips_junk(self):
        self.assertEqual(session_store.safe_account_id("HQ7kiou"), "HQ7kiou")
        self.assertEqual(session_store.safe_account_id("../etc/passwd"), "etcpasswd")

    def test_chats_cache_roundtrip_and_expiry(self):
        session_store.save_chats("HQ7kiou", [{"name": "凯凯", "spark_days": 111}], "https://a")
        hit = session_store.load_chats("HQ7kiou", max_age=60)
        self.assertEqual(hit["items"][0]["name"], "凯凯")
        self.assertEqual(hit["self_avatar"], "https://a")
        path = session_store.chats_path("HQ7kiou")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["at"] = time.time() - 120
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(session_store.load_chats("HQ7kiou", max_age=30))
        session_store.clear_account_session("HQ7kiou")
        self.assertIsNone(session_store.load_chats("HQ7kiou", max_age=9999))

    def test_wait_chat_helper_exists(self):
        from webui.qr_login import wait_chat_access
        self.assertTrue(callable(wait_chat_access))


if __name__ == "__main__":
    unittest.main()
