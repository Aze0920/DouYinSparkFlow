#!/bin/bash
set -euo pipefail

CONFIG_ENV_PATH="/app/.env"

if [[ ! -f "$CONFIG_ENV_PATH" ]]; then
  if [[ -f /app/config/.env ]]; then
    CONFIG_ENV_PATH="/app/config/.env"
  elif [[ -f /app/.env.example ]]; then
    mkdir -p /app/config
    cp /app/.env.example /app/config/.env
    CONFIG_ENV_PATH="/app/config/.env"
  else
    echo "Config file not found: $CONFIG_ENV_PATH" >&2
    exit 1
  fi
fi
export CONFIG_ENV_PATH

python - <<'PY'
import os
import shlex
from dotenv import dotenv_values

config_env_path = os.environ.get("CONFIG_ENV_PATH", "/app/.env")
file_vars = {k: v for k, v in dotenv_values(config_env_path).items() if v is not None}
merged_vars = dict(os.environ)
merged_vars.update(file_vars)

with open('/etc/douyin-spark-flow.env', 'w', encoding='utf-8') as f:
    for key, value in merged_vars.items():
        f.write(f'export {key}={shlex.quote(value)}\n')

with open('/tmp/douyin-spark-flow.cron', 'w', encoding='utf-8') as f:
    f.write(file_vars.get('CRON_SCHEDULE', os.environ.get('CRON_SCHEDULE', '')))

with open('/tmp/douyin-spark-flow.tz', 'w', encoding='utf-8') as f:
    f.write(file_vars.get('TZ', os.environ.get('TZ', 'UTC')))
PY

CRON_HOUR="$(python - <<'PY'
import os
from dotenv import dotenv_values
values = dotenv_values(os.environ.get("CONFIG_ENV_PATH", "/app/.env"))
print(values.get('CRON_HOUR', '9'))
PY
)"
CRON_MINUTE="$(python - <<'PY'
import os
from dotenv import dotenv_values
values = dotenv_values(os.environ.get("CONFIG_ENV_PATH", "/app/.env"))
print(values.get('CRON_MINUTE', '0'))
PY
)"
CRON_SECOND="$(python - <<'PY'
import os
from dotenv import dotenv_values
values = dotenv_values(os.environ.get("CONFIG_ENV_PATH", "/app/.env"))
print(values.get('CRON_SECOND', '0'))
PY
)"
TZ="$(cat /tmp/douyin-spark-flow.tz)"
export TZ

if [[ -z "$CRON_HOUR" || -z "$CRON_MINUTE" || -z "$CRON_SECOND" ]]; then
  echo "CRON_HOUR, CRON_MINUTE and CRON_SECOND are required." >&2
  exit 1
fi

CRON_SCHEDULE="${CRON_MINUTE} ${CRON_HOUR} * * *"

cat > /etc/cron.d/douyin-spark-flow <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
${CRON_SCHEDULE} root /app/docker/run-task.sh >> /proc/1/fd/1 2>> /proc/1/fd/2
EOF

chmod 0644 /etc/cron.d/douyin-spark-flow

echo "[docker] timezone: ${TZ:-UTC}"
echo "[docker] cron schedule: ${CRON_SCHEDULE} (+${CRON_SECOND}s)"
echo "[docker] starting web ui on :8787"
python -m uvicorn webui.app:app --host 0.0.0.0 --port 8787 --timeout-keep-alive 15 &
echo "[docker] container started, waiting for scheduled runs"

exec cron -f
