import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from webui import announcement as ann


class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "announcement.json"
        self.patch = patch.object(ann, "ANNOUNCEMENT_FILE", self.file)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_default_when_missing(self):
        data = ann.load_announcement()
        self.assertFalse(data["enabled"])
        self.assertEqual(data["content"], "")
        self.assertFalse(ann.announcement_active(data))

    def test_save_and_public_view(self):
        ann.save_announcement({"enabled": True, "title": "维护通知", "content": "今晚 0 点维护"})
        pub = ann.public_announcement()
        self.assertTrue(pub["active"])
        self.assertEqual(pub["title"], "维护通知")
        self.assertEqual(pub["content"], "今晚 0 点维护")
        self.assertGreater(pub["version"], 0)

    def test_enabled_but_empty_is_not_active(self):
        ann.save_announcement({"enabled": True, "content": ""})
        pub = ann.public_announcement()
        self.assertTrue(pub["enabled"])
        self.assertFalse(pub["active"])
        # 未激活时不把标题/正文泄露给弹窗
        self.assertEqual(pub["content"], "")

    def test_disabled_hides_content_from_public(self):
        ann.save_announcement({"enabled": False, "title": "旧公告", "content": "旧内容"})
        pub = ann.public_announcement()
        self.assertFalse(pub["active"])
        self.assertEqual(pub["title"], "")
        self.assertEqual(pub["content"], "")
        # 管理员视图仍能看到已保存的草稿
        adm = ann.admin_announcement()
        self.assertEqual(adm["content"], "旧内容")

    def test_version_stable_when_content_unchanged(self):
        first = ann.save_announcement({"enabled": True, "title": "A", "content": "hello"})
        v1 = first["version"]
        time.sleep(1.1)
        # 再次保存同样的内容，version 不应变化（否则会把用户的「今日不再提醒」清空）
        again = ann.save_announcement({"enabled": True, "title": "A", "content": "hello"})
        self.assertEqual(again["version"], v1)

    def test_version_bumps_when_content_changes(self):
        first = ann.save_announcement({"enabled": True, "content": "hello"})
        v1 = first["version"]
        time.sleep(1.1)
        second = ann.save_announcement({"enabled": True, "content": "world"})
        self.assertGreater(second["version"], v1)

    def test_content_length_capped(self):
        ann.save_announcement({"enabled": True, "content": "x" * 5000})
        self.assertEqual(len(ann.load_announcement()["content"]), ann.MAX_CONTENT)


if __name__ == "__main__":
    unittest.main()
