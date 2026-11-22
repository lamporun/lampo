#!/usr/bin/env bash
# Sends a message to the operator's Telegram (if configured) and to the system log.
# Usage: alert.sh "message"
set -u
msg="[Lampo] $*"
logger -t lampo "$msg" 2>/dev/null || true
# shellcheck disable=SC1091
[ -r /etc/lampo/alert.env ] && . /etc/lampo/alert.env
if [ -n "${TELEGRAM_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  curl -fsS --max-time 15 -o /dev/null \
    "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${msg}" || true
fi
