#!/bin/sh

set -eu

: "${YTDLP_UPDATE_SCHEDULE:=0 4 * * *}"

mkdir -p /etc/crontabs /app/logs
printf '%s %s\n' "$YTDLP_UPDATE_SCHEDULE" \
    'python3 -m pip install --no-cache-dir --upgrade yt-dlp >> /app/logs/yt-dlp-update.log 2>&1' \
    > /etc/crontabs/root

crond -c /etc/crontabs

exec "$@"