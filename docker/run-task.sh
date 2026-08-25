#!/bin/bash
set -euo pipefail

source /etc/douyin-spark-flow.env

echo "[docker] $(date '+%Y-%m-%d %H:%M:%S') skip legacy cron; per-account times are handled by web UI"
exit 0
