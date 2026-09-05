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
python -m playwright install-deps chromium || true
export WEB_PORT="$PORT"
export HEADLESS=true

# 装过开机自启以后，不要再前台 / nohup 起一份，否则重启后会抢端口。
if command -v systemctl >/dev/null 2>&1 \
   && systemctl is-enabled --quiet douyin-sparkflow 2>/dev/null; then
  echo "已安装开机自启，交给 systemd 启动"
  systemctl start douyin-sparkflow
  systemctl --no-pager --lines=15 status douyin-sparkflow || true
  exit 0
fi

python -m webui.app
