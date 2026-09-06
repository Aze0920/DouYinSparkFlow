import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webui import keepalive as ka
from webui.keepalive_slot import KeepaliveSlot, kill_owned_chromium

HOUR = 3600.0


class KeepaliveScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = patch.object(ka, "STATE_FILE", Path(self.tmp.name) / "keepalive.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.now = 1_000_000.0

    def test_scheduled_between_11_and_13_hours_after_task(self):
        for _ in range(60):
            ka.STATE_FILE.unlink(missing_ok=True)
            ka.schedule_after_task(["dy1"], now=self.now)
            gap = ka.snapshot("dy1")["next_at"] - self.now
            self.assertGreaterEqual(gap, 11 * HOUR)
            self.assertLessEqual(gap, 13 * HOUR)

    def test_not_due_before_window_and_due_after(self):
        ka.schedule_after_task(["dy1"], now=self.now)
        self.assertEqual(ka.due_ids(now=self.now + 10 * HOUR), [])
        self.assertEqual(ka.due_ids(now=self.now + 14 * HOUR), ["dy1"])

    def test_running_task_reschedules_from_that_moment(self):
        """又跑了一次续火花，计时要从新的完成时刻重新算。"""
        ka.schedule_after_task(["dy1"], now=self.now)
        first = ka.snapshot("dy1")["next_at"]
        ka.schedule_after_task(["dy1"], now=self.now + 6 * HOUR)
        self.assertGreater(ka.snapshot("dy1")["next_at"], first)

    def test_mark_checked_records_result_and_pushes_next(self):
        ka.schedule_after_task(["dy1"], now=self.now)
        later = self.now + 12 * HOUR
        ka.mark_checked("dy1", False, "Cookie 无效", now=later)
        item = ka.snapshot("dy1")
        self.assertFalse(item["last_ok"])
        self.assertEqual(item["last_message"], "Cookie 无效")
        self.assertGreaterEqual(item["next_at"] - later, 11 * HOUR)
        self.assertEqual(ka.due_ids(now=later + HOUR), [], "刚查过的账号不该立刻再排队")

    def test_ensure_scheduled_covers_accounts_that_never_ran(self):
        """从没跑过续火花的账号最容易过期，必须也排上保活。"""
        added = ka.ensure_scheduled(["dy1", "dy2"], now=self.now)
        self.assertEqual(sorted(added), ["dy1", "dy2"])
        self.assertEqual(ka.due_ids(now=self.now + 14 * HOUR), ["dy1", "dy2"])

    def test_ensure_scheduled_never_overwrites_existing(self):
        ka.schedule_after_task(["dy1"], now=self.now)
        planned = ka.snapshot("dy1")["next_at"]
        self.assertEqual(ka.ensure_scheduled(["dy1"], now=self.now + HOUR), [])
        self.assertEqual(ka.snapshot("dy1")["next_at"], planned)

    def test_forget_removes_deleted_account(self):
        ka.schedule_after_task(["dy1"], now=self.now)
        ka.forget("dy1")
        self.assertEqual(ka.snapshot("dy1"), {})
        self.assertEqual(ka.due_ids(now=self.now + 99 * HOUR), [])

    def test_ignores_blank_ids(self):
        self.assertEqual(ka.schedule_after_task([""], now=self.now), {})
        self.assertEqual(ka.ensure_scheduled([None, "  "], now=self.now), [])
        self.assertEqual(ka.due_ids(now=self.now + 99 * HOUR), [])

    def test_corrupt_state_file_does_not_crash(self):
        ka.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ka.STATE_FILE.write_text("{ not json", encoding="utf-8")
        self.assertEqual(ka.due_ids(now=self.now), [])
        ka.schedule_after_task(["dy1"], now=self.now)
        self.assertEqual(ka.due_ids(now=self.now + 14 * HOUR), ["dy1"])


class KeepaliveSlotTests(unittest.TestCase):
    def test_try_start_returns_immediately(self):
        gate = threading.Event()
        slot = KeepaliveSlot(killer=lambda: 0, hard_sec=30)
        def work(_uid):
            gate.wait(3)
        started = time.time()
        self.assertTrue(slot.try_start("dy1", work))
        self.assertLess(time.time() - started, 0.2)
        self.assertTrue(slot.busy())
        self.assertFalse(slot.try_start("dy2", work))
        slot.abort.set()
        gate.set()
        self.assertTrue(slot.idle.wait(2))

    def test_preempt_unblocks_via_event_not_sleep_poll(self):
        killed = []
        slot = KeepaliveSlot(killer=lambda: killed.append(1) or 1, hard_sec=30, preempt_sec=1)
        started = threading.Event()
        def work(_uid):
            started.set()
            slot.abort.wait(5)
        slot.try_start("dy1", work)
        self.assertTrue(started.wait(1))
        t0 = time.time()
        self.assertTrue(slot.preempt("扫码登录"))
        self.assertLess(time.time() - t0, 1.5)
        self.assertTrue(slot.abort.is_set())
        self.assertTrue(killed)
        self.assertFalse(slot.busy())

    def test_watchdog_times_out_and_kills(self):
        killed = []
        slot = KeepaliveSlot(killer=lambda: killed.append(1) or 1, hard_sec=0.2, preempt_sec=1)
        def work(_uid):
            slot.abort.wait(5)
        slot.try_start("dy1", work)
        self.assertTrue(slot.idle.wait(2))
        self.assertTrue(killed)
        self.assertTrue(slot.abort.is_set())

    def test_watchdog_does_not_kill_after_preempt_epoch_bump(self):
        """扫码已经开了新浏览器后，迟到的看门狗不能再杀一次。"""
        killed = []
        slot = KeepaliveSlot(killer=lambda: killed.append(1) or 1, hard_sec=0.4, preempt_sec=1)
        started = threading.Event()
        def work(_uid):
            started.set()
            slot.abort.wait(5)
        slot.try_start("dy1", work)
        self.assertTrue(started.wait(1))
        self.assertTrue(slot.preempt("扫码登录"))
        time.sleep(0.6)
        self.assertEqual(killed, [1])

    def test_idle_preempt_is_noop(self):
        slot = KeepaliveSlot(killer=lambda: 1)
        self.assertTrue(slot.preempt("扫码登录"))

    def test_kill_owned_chromium_is_safe_when_idle(self):
        self.assertGreaterEqual(kill_owned_chromium(), 0)


if __name__ == "__main__":
    unittest.main()
