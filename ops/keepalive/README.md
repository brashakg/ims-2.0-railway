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

Railway service settings (dashboard, once):
- Source: this repo. **Root Directory: `ops/keepalive`** - required. The cron schedule
  (`24 3,16,17,19,20,21 * * *` UTC, six minutes before each IST slot), restart policy NEVER and
  the Dockerfile path are then read from `ops/keepalive/railway.json`.
- NEVER put a `railway.json` at the repo root: Railway applies a root config to every service
  built from the repo. On 2026-09-17 a root copy made the backend service try to build this
  ping container and its deploy failed (the previous deployment kept serving).

If the live-sync slots are changed in Settings > Shopify live sync, update the cron.
