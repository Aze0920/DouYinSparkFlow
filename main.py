# 尝试从 .env 文件加载环境变量
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
_candidates = []
if os.getenv("CONFIG_ENV_FILE"):
    _candidates.append(Path(os.getenv("CONFIG_ENV_FILE")))
_candidates.extend([ROOT / ".env", ROOT / "config" / ".env"])
for _path in _candidates:
    if _path and _path.is_file():
        load_dotenv(_path)
        break

from core.tasks import runTasks

runTasks()
