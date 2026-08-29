"""落盘不能留下半个文件，也不能把上一份好数据换成坏数据。

配置文件里存着全部账号和 Cookie，快照文件就是账号的登录态。
这两样只要被写坏一次，用户那边就是「号全没了」和「全部掉线」。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webui import safe_io, session_store

try:
    from webui import envfile
except ImportError:  # 没装 python-dotenv 的环境（比如只跑纯逻辑测试时）
    envfile = None


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = Path(self.box.name)

    def test_writes_content_and_leaves_no_litter(self):
        target = self.dir / "a.json"
        safe_io.write_json(target, {"k": "值"})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"k": "值"})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["a.json"])

    def test_creates_missing_parent(self):
        target = self.dir / "deep" / "b.txt"
        safe_io.write_text(target, "hi")
        self.assertEqual(target.read_text(encoding="utf-8"), "hi")

    def test_crash_mid_write_keeps_the_previous_file(self):
        target = self.dir / "c.json"
        safe_io.write_json(target, {"good": 1})
        with patch.object(safe_io.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                safe_io.write_json(target, {"bad": 2})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"good": 1})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["c.json"], "临时文件要清掉")


@unittest.skipUnless(envfile, "没装 python-dotenv，跳过 .env 落盘测试")
class EnvFileTests(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.path = Path(self.box.name) / "config" / ".env"
        old = os.environ.get("CONFIG_ENV_FILE")
        os.environ["CONFIG_ENV_FILE"] = str(self.path)
        self.addCleanup(lambda: os.environ.__setitem__("CONFIG_ENV_FILE", old) if old else os.environ.pop("CONFIG_ENV_FILE", None))

    def tasks(self):
        return envfile.read_tasks(envfile.load_env())

    def test_round_trips_accounts(self):
        envfile.write_env({"TASKS": [{"unique_id": "a1", "username": "王洁", "region": "410700"}]})
        self.assertEqual(self.tasks()[0]["region"], "410700")

    def test_keeps_a_backup_of_the_previous_version(self):
        envfile.write_env({"TASKS": [{"unique_id": "a1"}]})
        envfile.write_env({"TASKS": [{"unique_id": "a2"}]})
        backup = self.path.with_name(self.path.name + ".bak")
        self.assertIn("a1", backup.read_text(encoding="utf-8"))
        self.assertEqual(self.tasks()[0]["unique_id"], "a2")

    def test_failed_write_does_not_destroy_the_config(self):
        """以前是直接覆盖写：写到一半挂掉，账号和 Cookie 一起没。"""
        envfile.write_env({"TASKS": [{"unique_id": "a1"}], "COOKIES_A1": "[{}]"})
        with patch.object(safe_io.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                envfile.write_env({"TASKS": [{"unique_id": "a2"}]})
        env = envfile.load_env()
        self.assertEqual(envfile.read_tasks(env)[0]["unique_id"], "a1")
        self.assertEqual(env.get("COOKIES_A1"), "[{}]")


class SaveStateTests(unittest.TestCase):
    """快照写坏 = 这个号当场掉线，所以宁可保留旧快照也不能写半个。"""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        patcher = patch.object(session_store, "SESSION_DIR", Path(self.box.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.good = json.dumps({"cookies": [{"name": "sessionid", "value": "x" * 40}]})

    class Ctx:
        def __init__(self, payload=None, boom=False):
            self.payload = payload
            self.boom = boom

        def storage_state(self, path):
            if self.boom:
                raise RuntimeError("浏览器已经关了")
            Path(path).write_text(self.payload, encoding="utf-8")

    def test_saves_a_full_snapshot(self):
        self.assertTrue(session_store.save_state(self.Ctx(self.good), "a1"))
        self.assertEqual(session_store.state_path("a1").read_text(encoding="utf-8"), self.good)

    def test_failure_keeps_the_old_snapshot(self):
        session_store.save_state(self.Ctx(self.good), "a1")
        self.assertFalse(session_store.save_state(self.Ctx(boom=True), "a1"))
        self.assertEqual(session_store.state_path("a1").read_text(encoding="utf-8"), self.good)

    def test_truncated_snapshot_is_rejected(self):
        session_store.save_state(self.Ctx(self.good), "a1")
        self.assertFalse(session_store.save_state(self.Ctx("{}"), "a1"))
        self.assertEqual(session_store.state_path("a1").read_text(encoding="utf-8"), self.good)

    def test_no_temp_file_left_behind(self):
        session_store.save_state(self.Ctx(self.good), "a1")
        session_store.save_state(self.Ctx(boom=True), "a1")
        names = sorted(p.name for p in Path(self.box.name).iterdir())
        self.assertEqual(names, ["a1.state.json"])


if __name__ == "__main__":
    unittest.main()
