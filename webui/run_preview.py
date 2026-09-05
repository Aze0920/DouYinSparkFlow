"""续火花实时预览：任务进程往磁盘丢截图和核对摘要，控制台去读。

任务跑在单独的 Python 进程里，网页进程看不到 Playwright 页面。
这个模块是中间那一层：一边原子落盘，一边给 /api/run/preview 吐当前帧。
截图失败绝不能影响发送。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from utils.logger import LOG_FILE
from webui import safe_io

SHOT_GAP = 0.7
RECENT_KEEP = 12


def _default_dir() -> Path:
    return Path(LOG_FILE).parent


def _write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


class RunPreview:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else _default_dir()
        self.state_path = self.dir / "run-live.json"
        self.image_path = self.dir / "run-live.jpg"
        self._lock = threading.Lock()
        self._last_shot = 0.0

    def begin(self, unique_ids=None) -> dict:
        ids = [str(x).strip() for x in (unique_ids or []) if str(x).strip()]
        state = {
            "ts": time.time(),
            "phase": "starting",
            "caption": "正在打开抖音私信页",
            "account": "",
            "unique_id": ids[0] if ids else "",
            "unique_ids": ids,
            "friend": "",
            "before": "",
            "after": "",
            "result": "",
            "recent": [],
            "has_image": self.image_path.is_file(),
        }
        with self._lock:
            self._write_state(state)
        return state

    def finish(self, caption: str = "续火花已结束") -> dict:
        with self._lock:
            state = self._read_state()
            state["phase"] = "done"
            state["caption"] = caption or "续火花已结束"
            state["ts"] = time.time()
            state["has_image"] = self.image_path.is_file()
            self._write_state(state)
        return state

    def publish(self, page=None, **meta) -> dict:
        """更新当前帧。page 可以没有；有就尽量截一张。"""
        with self._lock:
            state = self._read_state()
            account = str(meta.get("account") or state.get("account") or "").strip()
            unique_id = str(meta.get("unique_id") or state.get("unique_id") or "").strip()
            friend = str(meta.get("friend") or "").strip()
            phase = str(meta.get("phase") or state.get("phase") or "").strip()
            caption = str(meta.get("caption") or "").strip()
            before = str(meta.get("before") or "")
            after = str(meta.get("after") or "")
            result = str(meta.get("result") or "")
            force = bool(meta.get("force"))
            extra_ids = [str(x).strip() for x in (meta.get("unique_ids") or []) if str(x).strip()]

            if account:
                state["account"] = account
            if unique_id:
                state["unique_id"] = unique_id
            if extra_ids:
                state["unique_ids"] = extra_ids
            if friend:
                state["friend"] = friend
            if phase:
                state["phase"] = phase
            if caption:
                state["caption"] = caption
            if "before" in meta:
                state["before"] = before
            if "after" in meta:
                state["after"] = after
            if result:
                state["result"] = result
            if result in {"ok", "fail"} and friend:
                recent = [row for row in (state.get("recent") or []) if isinstance(row, dict)]
                recent.append(
                    {
                        "friend": friend,
                        "account": account or state.get("account") or "",
                        "result": result,
                        "before": before[:80],
                        "after": after[:80],
                    }
                )
                state["recent"] = recent[-RECENT_KEEP:]

            now = time.time()
            if page is not None and (force or now - self._last_shot >= SHOT_GAP):
                if self._shot(page):
                    self._last_shot = now
            state["ts"] = now
            state["has_image"] = self.image_path.is_file()
            self._write_state(state)
            return dict(state)

    def snapshot(self) -> dict:
        with self._lock:
            state = self._read_state()
            state["has_image"] = self.image_path.is_file()
            return state

    def image_bytes(self) -> bytes:
        with self._lock:
            if not self.image_path.is_file():
                return b""
            try:
                return self.image_path.read_bytes()
            except OSError:
                return b""

    def _shot(self, page) -> bool:
        try:
            raw = page.screenshot(type="jpeg", quality=52, full_page=False, timeout=2500)
        except TypeError:
            try:
                raw = page.screenshot()
            except Exception:
                return False
        except Exception:
            return False
        if not raw:
            return False
        try:
            _write_bytes(self.image_path, raw)
            return True
        except Exception:
            return False

    def _read_state(self) -> dict:
        empty = {
            "ts": 0,
            "phase": "",
            "caption": "",
            "account": "",
            "unique_id": "",
            "unique_ids": [],
            "friend": "",
            "before": "",
            "after": "",
            "result": "",
            "recent": [],
            "has_image": False,
        }
        if not self.state_path.is_file():
            return empty
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return empty
        return data if isinstance(data, dict) else empty

    def _write_state(self, state: dict) -> None:
        safe_io.write_json(self.state_path, state, indent=None)


_store: RunPreview | None = None
_store_lock = threading.Lock()


def default_store() -> RunPreview:
    global _store
    with _store_lock:
        if _store is None:
            _store = RunPreview()
        return _store


def begin_run(unique_ids=None) -> dict:
    return default_store().begin(unique_ids)


def finish_run(caption: str = "续火花已结束") -> dict:
    return default_store().finish(caption)


def publish(page=None, **meta) -> dict:
    return default_store().publish(page, **meta)


def snapshot() -> dict:
    return default_store().snapshot()


def image_bytes() -> bytes:
    return default_store().image_bytes()
