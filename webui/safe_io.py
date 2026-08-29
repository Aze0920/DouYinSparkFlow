"""落盘要么整份成功，要么维持原样。

直接 write_text 是「先清空再写」：写到一半断电、进程被杀、磁盘满，
留下的就是半个文件。配置里存着所有账号和 Cookie，半个文件等于全丢。
先写同目录临时文件再 os.replace，替换在同一分区上是原子的。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_text(path, text: str, encoding: str = "utf-8") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())  # 落到盘上再替换，否则崩溃后拿到的是空文件
        os.replace(tmp, path)
        tmp = ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return path


def write_json(path, data, indent: int | None = 2) -> Path:
    return write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def commit(tmp, path) -> Path:
    """把别人（比如 Playwright）写好的临时文件原子地挪到正式位置。"""
    tmp, path = Path(tmp), Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, path)
    return path
