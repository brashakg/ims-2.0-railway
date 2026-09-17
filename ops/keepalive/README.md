# keepalive-ping (Railway cron service)

Wakes the sleeping backend just before each scheduled job so the job actually runs.

Why: the backend keeps Railway's "sleep when inactive" ON to save money (owner ruling
2026-09-17). A sleeping process cannot run its in-process scheduler, so timed work
(live Shopify sync 01:00 + 09:00 IST, PIXEL 02:00, parity 03:00, EOD 22:00, Tally 23:00)
would silently not happen unless something else woke the app first. Order webhooks
from Shopify do wake it, but on a quiet night nothing would.

How: this folder is its own Railway service (source = this repo, root directory
`ops/keepalive`) with a cron schedule and restart policy NEVER. On each run it pings
`/health` six times, 90 s apart, then exits. Cost: a few container-minutes a day.

Railway service settings come from `/railway.json` at the repo root (config-as-code), so
nothing has to be clicked in the dashboard: Dockerfile `ops/keepalive/Dockerfile`, cron
`24 3,16,17,19,20,21 * * *` UTC (6 minutes before each IST slot), restart policy NEVER.
The service is created with `railway add --service keepalive-ping --repo brashakg/ims-2.0-railway`
and its root directory is left at the repo root on purpose. The backend service reads
`backend/` for its own config and is not affected by the root file.
- Variables (optional): `PING_URL` (default https://api.uniparallel.com/health),
  `PING_HITS` (6), `PING_GAP_SECONDS` (90)

If the live-sync slots are changed in Settings > Shopify live sync, update the cron.
