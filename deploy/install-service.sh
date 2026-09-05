#!/usr/bin/env bash
# 把控制台交给 systemd：现在立刻拉起，机器重启后也会自己起来。
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请用 root 跑：sudo bash deploy/install-service.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="${APP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
UNIT_SRC="$SCRIPT_DIR/douyin-sparkflow.service"
UNIT_DST="/etc/systemd/system/douyin-sparkflow.service"
PORT="${WEB_PORT:-8787}"

if [[ ! -f "$APP_DIR/webui/app.py" ]]; then
  echo "找不到项目：${APP_DIR}" >&2
  exit 1
fi

APP_USER="${APP_USER:-$(stat -c '%U' "$APP_DIR")}"
APP_GROUP="${APP_GROUP:-$(stat -c '%G' "$APP_DIR")}"

mkdir -p "$APP_DIR/config" "$APP_DIR/logs"
if [[ ! -f "$APP_DIR/config/.env" && -f "$APP_DIR/.env.example" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/config/.env"
fi

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  echo "虚拟环境不存在，先创建并安装依赖……"
  python3 -m venv "$APP_DIR/.venv"
  # shellcheck disable=SC1091
  source "$APP_DIR/.venv/bin/activate"
  pip install -r "$APP_DIR/requirements.txt"
  python -m playwright install chromium || true
  python -m playwright install-deps chromium || true
fi

# 清掉以前 nohup / 手动前台留下的进程，避免 8787 被占着
if systemctl list-unit-files | grep -q '^douyin-sparkflow.service'; then
  systemctl stop douyin-sparkflow.service 2>/dev/null || true
fi
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
else
  pkill -f '[p]ython -m webui.app' 2>/dev/null || true
fi
sleep 1

sed \
  -e "s|__APP_DIR__|${APP_DIR}|g" \
  -e "s|__APP_USER__|${APP_USER}|g" \
  -e "s|__APP_GROUP__|${APP_GROUP}|g" \
  "$UNIT_SRC" > "$UNIT_DST"

systemctl daemon-reload
systemctl enable --now douyin-sparkflow.service

echo
systemctl --no-pager --lines=20 status douyin-sparkflow.service || true
echo
echo "开机自启已打开。以后用这些命令："
echo "  systemctl status  douyin-sparkflow"
echo "  systemctl restart douyin-sparkflow"
echo "  journalctl -u douyin-sparkflow -n 80 --no-pager"
echo
echo "网页：http://服务器IP:${PORT}"
