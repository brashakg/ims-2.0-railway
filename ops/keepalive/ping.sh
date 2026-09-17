#!/bin/sh
# Keep the backend awake across a scheduled-job slot.
#
# The backend runs with Railway's "sleep when inactive" ON (owner ruling
# 2026-09-17: cheaper). A sleeping process cannot fire its in-process
# APScheduler jobs, so this job runs a few minutes BEFORE each IST slot and
# pings /health every 90 s for ~8 minutes. The first ping wakes the app; the
# rest keep it awake through the slot; then the app may sleep again.
#
# Slots (IST): 01:00 + 09:00 live Shopify sync, 02:00 PIXEL, 03:00 parity,
# 22:00 EOD, 23:00 Tally. Cron (UTC) on the Railway service:
#   24 3,16,17,19,20,21 * * *
# If the SUPERADMIN changes the live-sync slots in Settings, change the cron.
URL="${PING_URL:-https://api.uniparallel.com/health}"
HITS="${PING_HITS:-6}"
GAP="${PING_GAP_SECONDS:-90}"
i=1
while [ "$i" -le "$HITS" ]; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 45 "$URL" || echo "000")
  echo "$(date -u +%H:%M:%S) ping $i/$HITS -> $code"
  [ "$i" -lt "$HITS" ] && sleep "$GAP"
  i=$((i + 1))
done
exit 0
