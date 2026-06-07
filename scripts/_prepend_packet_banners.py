"""Prepend binding-correction banners to the going-live Phase-0 packets. ASCII-only."""
import os
FEAT = r"c:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway-1\docs\roadmap\features"

BANNERS = {
"E1.md": "PHASE-A FACADE ONLY (CORRECTIONS P0-1). Do NOT build a `money_accounts` SoR, NO migration, NO new index, NO cross-collection dual-write -- multi-doc transactions do NOT exist on this standalone Mongo. Build a thin facade over the EXISTING vouchers/loyalty/store-credit collections, reusing redeem_voucher_atomic / try_debit / try_debit_store_credit. Phase B/C = DO-NOT-BUILD.",
"E2.md": "BINDING (CORRECTIONS P1): encrypt `secret` policy values per-key via `_encrypt_value`/`_decrypt_value` (NOT `_encrypt_config`); invalidate cache via explicit `cache.delete(key)` for each (key,scope_id) written (delete_pattern is a no-op without Redis); luxury brand caps may only be LOWERED and are NOT E2 keys; a store missing entity_id resolves to global, never raises.",
"F35.md": "BINDING (CORRECTIONS): `_build_store_ledger` does NOT return cost_price/margin -- drop that overlap claim; audit each named call-site that the field exists before wrapping. Masked roles get cost fields present-but-null (SALES_CASHIER null, ACCOUNTANT real). Pure-additive, no schema.",
"F34.md": "BINDING (CORRECTIONS): no `orders` compound index exists -- add `{created_at,status,store_id}` + server-side cache (net-new). 'No target set' fallback. SALES_STAFF sees only pct_complete; STORE_MANAGER sees mtd_revenue. Needs E2.",
"F21.md": "BINDING (CORRECTIONS P0-6): depends on the E3-shim (see ENGINES.md). `stock_units.status` is a FREE STRING (no enum change). You MUST exclude QUARANTINED from EVERY on-hand rollup: product_repository.find_available + inventory.py ~107/2147/2274/2366/2731. POS safety = find_available filtering AVAILABLE. Intent test: quarantine the only AVAILABLE unit -> POS sell returns 409.",
"F40.md": "NOTE (CORRECTIONS): honest packet, claims verified. Read-only watchlist, SUPERADMIN/ADMIN only. This is NOT a quick win (~M effort) -- scope accordingly.",
"E6.md": "BINDING (CORRECTIONS P1): `fu_due_today` -- follow_ups.type is a PURPOSE enum, not a channel; reconcile with the existing GET /due-today (follow_ups.py:326) and map type->channel + always create a staff task. Frequency cap = SOFT-ceiling (check-then-write is racy); test asserts soft-ceiling. OTP/transactional sends short-circuit quiet-hours + freq-cap + consent FIRST via a new is_transactional flag.",
}

HDR = "> ## [WARNING] BINDING CORRECTIONS -- READ `../CORRECTIONS.md` FIRST\n> %s\n> _Precedence: DECISIONS > CORRECTIONS > this packet. If this packet conflicts with CORRECTIONS, CORRECTIONS wins._\n\n---\n\n"

done = []
for fname, msg in BANNERS.items():
    p = os.path.join(FEAT, fname)
    if not os.path.exists(p):
        print("MISSING:", fname); continue
    with open(p, "r", encoding="utf-8") as fh:
        body = fh.read()
    if "BINDING CORRECTIONS" in body[:400]:
        print("already banded:", fname); continue
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(HDR % msg + body)
    done.append(fname)

print("banded:", done)
