#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PORT="${WEB_PORT:-8787}"
echo "启动 DouYinSparkFlow 控制台: http://0.0.0.0:${PORT}"
echo "默认密码: sparkflow"

mkdir -p config logs
if [[ ! -f config/.env ]]; then
  cp .env.example config/.env
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium || true
export WEB_PORT="$PORT"
export HEADLESS=true
python -m webui.app
