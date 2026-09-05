import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("AUTH_SECRET", "test-auth-secret-key-32chars!!")

from webui.run_preview import RunPreview


JPEG = b"\xff\xd8\xff\xdb" + b"x" * 48


class FakeShot:
    def __init__(self, blob=JPEG):
        self.blob = blob
        self.calls = 0

    def screenshot(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return self.blob


class RunPreviewTests(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.store = RunPreview(Path(self.box.name))

    def test_begin_then_snapshot(self):
        self.store.begin(["dy1", "dy2"])
        data = self.store.snapshot()
        self.assertEqual(data["phase"], "starting")
        self.assertEqual(data["unique_ids"], ["dy1", "dy2"])
        self.assertFalse(data["has_image"])

    def test_publish_without_page_keeps_caption(self):
        self.store.begin(["dy1"])
        self.store.publish(None, account="王洁", friend="凯凯", caption="正在给 凯凯 发消息", phase="sending")
        data = self.store.snapshot()
        self.assertEqual(data["account"], "王洁")
        self.assertEqual(data["friend"], "凯凯")
        self.assertEqual(data["caption"], "正在给 凯凯 发消息")
        self.assertEqual(data["recent"], [])

    def test_screenshot_is_written(self):
        page = FakeShot()
        self.store.publish(page, account="王洁", caption="已打开会话列表", phase="list", force=True)
        self.assertEqual(page.calls, 1)
        self.assertEqual(page.kwargs.get("type"), "jpeg")
        self.assertTrue(self.store.image_path.is_file())
        self.assertEqual(self.store.image_bytes()[:3], b"\xff\xd8\xff")
        self.assertTrue(self.store.snapshot()["has_image"])

    def test_throttle_skips_second_shot_unless_forced(self):
        page = FakeShot()
        self.store.publish(page, caption="一", force=True)
        self.store.publish(page, caption="二")
        self.assertEqual(page.calls, 1)
        self.store.publish(page, caption="三", force=True)
        self.assertEqual(page.calls, 2)

    def test_ok_and_fail_append_recent(self):
        self.store.publish(
            None,
            friend="儿子",
            result="ok",
            before="旧的",
            after="洞庭有归客",
        )
        self.store.publish(
            None,
            friend="凯凯",
            result="fail",
            before="めまぐるしい",
            after="",
        )
        recent = self.store.snapshot()["recent"]
        self.assertEqual([row["friend"] for row in recent], ["儿子", "凯凯"])
        self.assertEqual(recent[1]["after"], "")

    def test_finish_keeps_last_frame(self):
        page = FakeShot()
        self.store.publish(page, caption="核对中", force=True)
        self.store.finish("续火花已结束")
        data = self.store.snapshot()
        self.assertEqual(data["phase"], "done")
        self.assertEqual(data["caption"], "续火花已结束")
        self.assertTrue(data["has_image"])

    def test_broken_screenshot_does_not_raise(self):
        class Boom:
            def screenshot(self, **kwargs):
                raise RuntimeError("page closed")

        self.store.publish(Boom(), caption="还能写字", phase="opening", force=True)
        self.assertEqual(self.store.snapshot()["caption"], "还能写字")


class PreviewVisibleTests(unittest.TestCase):
    def test_only_admin_can_see(self):
        from webui.app import _preview_visible

        self.assertTrue(_preview_visible({"role": "admin", "username": "root"}, {}))
        self.assertFalse(_preview_visible({"role": "user", "username": "王洁"}, {"unique_id": "dy1"}))
        self.assertFalse(_preview_visible(None, {}))


if __name__ == "__main__":
    unittest.main()
