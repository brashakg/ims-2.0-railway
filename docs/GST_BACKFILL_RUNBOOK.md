# GST Backfill Runbook — uncategorized products → FRAME / 5%

**Date:** 2026-05-28
**Audit kind:** `gst_backfill_2026_05_28`
**Script:** [`backend/scripts/backfill_uncategorized_to_frame.py`](../backend/scripts/backfill_uncategorized_to_frame.py)

## Why

QA found a product with a **blank category** ("Fastrack P357BK1") being billed at
**18% GST** at POS. For an optical chain the dominant rate is **5%**
(frames / spectacle lenses / corrective specs / contact lenses under GST 2.0),
so an uncategorized product billing at 18% over-charges the customer and is a
compliance risk.

Three fixes shipped together:

1. **Fallback rate** — `backend/api/services/gst_rates.py` default GST fallback
   changed `18% → 5%` (optical-dominant). Safety net only.
2. **Block save** — `backend/api/routers/products.py` create **and** update now
   reject a blank/null/missing category with **HTTP 422**. No new product can be
   persisted without a valid category.
3. **Backfill** (this runbook) — existing rows that already have a blank
   category are set to `category="FRAME"`, `gst_rate=5`, `hsn_code="9003"`.

## What the script does

- Finds products where `category` is **missing, null, empty, or whitespace-only**.
- Sets them to `FRAME` / `5.0` / `9003`.
- Writes one `audit_log` row per changed product (kind `gst_backfill_2026_05_28`)
  capturing the **prior** `category` / `gst_rate` / `hsn_code` — fully reversible.
- **Dry-run by default.** Writes nothing unless you pass `--apply`.
- **Idempotent.** After a backfill the rows are `FRAME`, so they no longer match
  the blank query — a second `--apply` is a no-op (`Updated 0`).
- **Fail-loud.** If MongoDB is unreachable it prints an error and exits non-zero.

## Commands

Run from the **repo root**. The venv lives at the repo root
(`.venv/Scripts/python.exe`). Use `railway run` so the live MongoDB connection
string (`MONGODB_URL` / `MONGO_URL`) is injected into the subprocess — never
copy the secret yourself.

### 1. Dry run (default — shows count + sample, writes nothing)

```bash
railway run .venv\Scripts\python.exe backend/scripts/backfill_uncategorized_to_frame.py
```

Review the printed count and the sample list of products that **would** change.

### 2. Apply (writes the changes + audit trail)

```bash
railway run .venv\Scripts\python.exe backend/scripts/backfill_uncategorized_to_frame.py --apply
```

### 3. Verify (re-run the dry run — should report 0)

```bash
railway run .venv\Scripts\python.exe backend/scripts/backfill_uncategorized_to_frame.py
```

Expected: `Uncategorized products found: 0` → idempotent, nothing left to do.

## Audit trail / rollback

Every changed product has an `audit_log` document:

```js
db.audit_log.find({ kind: "gst_backfill_2026_05_28" })
```

Each row carries `prior` (the original `category` / `gst_rate` / `hsn_code`) and
`new`. To roll a single product back, re-apply its `prior` values by hand using
its `product_id`.

## Notes

- This is a **one-time** correction. With the block-save guard in place no new
  uncategorized rows can appear, so the script should only ever find rows on the
  first run.
- The script does **not** touch already-categorized products, stock, orders, or
  any POS billing path.
