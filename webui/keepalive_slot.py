"""保活占用浏览器的忙闲槽。

工作线程跑 Playwright，看门狗线程盯超时。前台要扫码时发 abort，
用 Event.wait 等空闲，不在请求线程里 sleep 轮询，也不用 Lock.locked() 猜忙闲。
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path

from utils.logger import setup_logger

logger = setup_logger("app", "DEBUG")

HARD_SEC = 90
PREEMPT_SEC = 6
_CHROME_MARKS = ("chrome-headless-shell", "playwright/driver", "ms-playwright")


def _proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except Exception:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace")


def _child_pids(pid: int) -> list[int]:
    kids: list[int] = []
    try:
        for task in Path(f"/proc/{pid}/task").iterdir():
            children = task / "children"
            if not children.is_file():
                continue
            kids.extend(int(x) for x in children.read_text().split() if x.isdigit())
    except Exception:
        pass
    return list(dict.fromkeys(kids))


def _descendant_pids(pid: int) -> list[int]:
    out: list[int] = []
    stack = _child_pids(pid)
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        stack.extend(_child_pids(cur))
    return out


def kill_owned_chromium() -> int:
    """只杀本进程拉起的 Playwright / headless chrome。"""
    if os.name != "posix":
        return 0
    killed = 0
    for pid in _descendant_pids(os.getpid()):
        cmd = _proc_cmdline(pid)
        if not any(mark in cmd for mark in _CHROME_MARKS):
            continue
        try:
            os.kill(pid, 9)
            killed += 1
        except OSError:
            continue
    return killed


class KeepaliveSlot:
    def __init__(
        self,
        killer: Callable[[], int] | None = None,
        hard_sec: float = HARD_SEC,
        preempt_sec: float = PREEMPT_SEC,
    ):
        self._killer = killer or kill_owned_chromium
        self.hard_sec = hard_sec
        self.preempt_sec = preempt_sec
        self._gate = threading.Lock()
        self.idle = threading.Event()
        self.idle.set()
        self.abort = threading.Event()
        self._epoch = 0
        self._uid = ""

    @property
    def uid(self) -> str:
        return self._uid

    def busy(self) -> bool:
        return not self.idle.is_set()

    def try_start(self, uid: str, work: Callable[[str], None]) -> bool:
        """立刻返回。已经在跑就 False；否则拉起工作线程和看门狗线程。"""
        with self._gate:
            if self.busy():
                return False
            self._epoch += 1
            epoch = self._epoch
            self._uid = uid
            self.abort.clear()
            self.idle.clear()
        threading.Thread(
            target=self._run, args=(uid, work), daemon=True, name="spark-keepalive"
        ).start()
        threading.Thread(
            target=self._watch, args=(uid, epoch), daemon=True, name="spark-keepalive-watch"
        ).start()
        return True

    def _run(self, uid: str, work: Callable[[str], None]) -> None:
        try:
            work(uid)
        except Exception:
            logger.exception("保活执行失败 unique_id=%s", uid)
        finally:
            self._uid = ""
            self.idle.set()

    def _watch(self, uid: str, epoch: int) -> None:
        if self.idle.wait(self.hard_sec):
            return
        if self._epoch != epoch or self.idle.is_set():
            return
        logger.warning("保活超时 %ss unique_id=%s，看门狗关掉浏览器", self.hard_sec, uid)
        self.abort.set()
        if self._epoch != epoch:
            return
        self._killer()
        self.idle.wait(8)

    def preempt(self, reason: str) -> bool:
        """前台要开浏览器。发 abort，杀卡住的 chrome，等空闲。"""
        if self.idle.is_set():
            return True
        epoch = self._epoch
        logger.warning("用户要%s，保活必须让路 unique_id=%s", reason, self._uid or "-")
        self.abort.set()
        self._killer()
        if not self.idle.wait(self.preempt_sec):
            return False
        # 作废还在跑的看门狗，避免它回头把扫码新开的浏览器杀掉
        if self._epoch == epoch:
            self._epoch = epoch + 1
        return True
