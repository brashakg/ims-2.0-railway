> ## [WARNING] BINDING -- READ `../CORRECTIONS.md` + DECISIONS.md sec 9 FIRST
> This is the owner of the EXECUTION_BOARD Phase-2 "NEW" row: *enforce cost+10%/category price floor on the sell-path via `pricing.cost_floor_pct` (E2)*. DECISIONS sec 9 LOCKED: the floor is `cost_price + 10%` (NOT the current cost+0% at `orders.py:1338`). BINDING: (1) enforce on the **EFFECTIVE post-discount** per-unit price (after per-line discount AND cart-level discount), not the pre-discount `unit_price` -- DECISIONS sec 9's intent is "never SELL below cost+10%", and the sell price is post-discount. (2) Read the floor pct from **E2 `get_policy('pricing.cost_floor_pct', scope)`** (registry default 10, global scope). (3) Read **raw server-side cost** (`_cost_by_pid` from `product_repo`), NEVER an F35-masked DTO -- when F35 cost-masking ships, the floor must keep reading the raw cost. (4) **Flag-gated OFF by default** (POS sell-path change, PROTOCOL sec 4) -- orchestrator flips per store after staging validation. (5) Preserve the existing **Rs 0 / 100%-discount exemption** (those lines are approval-gated, exempt from the floor).
> _Precedence: DECISIONS > CORRECTIONS > this packet. Authored 2026-06-08, grounded against orders.py:1230-1622._

---

# Packet COST-FLOOR: cost+10%/category sell-path price floor (Phase 2)

## Current behavior (file:line)

- **`orders.py:1338`** -- the cost floor today is `if _cost and _cost > 0 and _up > 1e-6 and _up < _cost - 1e-6:` -> raise 400. This is **cost + 0%** (sell at or above bare cost), and it checks the **pre-discount** `unit_price` `_up`, NOT the effective price after discounts.
- **`orders.py:1336-1337`** -- a `Rs 0` line (free / 100%-discount item) is **exempt** from the floor (it is gated by the C-4 approval requirement instead). This exemption must be preserved.
- **Discount stack (orders.py):** `_cost_by_pid`/`_offer`/`_ceiling` pre-fetched at `1230-1277` (raw `cost_price` snapshot from `product_repo`, persisted later as `cost_at_sale` at `1540`); per-line `unit_price` ceiling check at `1326-1334`; **cost-floor at `1338`** (currently pre-discount); per-line `discount_percent` applied at `1412-1413`; **cart-level discount applied at `1568-1622`**. So the current floor fires BEFORE both discount layers.
- **`pricing.cost_floor_pct`** already exists in the E2 registry (`policy_registry.py:101`, default `10.0`, `float`, global scope, SUPERADMIN write). `orders.py` does not read it yet.
- **No role-based cost masking exists today** (`finance.py` reads `cost_at_sale` without masking; F35 is the future packet that ADDS masking). The order-create path reads `_cost_by_pid` server-side -- inherently raw.

**Net:** the locked DECISIONS sec 9 floor (cost+10%, applied to the actual sell price) is NOT enforced; the live code is cost+0% on the pre-discount price, so a deep per-line or cart discount can still sell below cost.

## Intended behavior (full intent)

When the `cost-floor-enforcement` flag is ON for the store, POS order-create rejects any priced line whose **effective per-unit sell price** (after the full discount stack) is below `cost x (1 + pct/100)`, where `pct = get_policy("pricing.cost_floor_pct", scope)` (default 10):

1. Compute the line's **effective per-unit taxable price** AFTER per-line `discount_percent` AND the line's share of the cart-level discount: `eff_unit = line_final_taxable / quantity` (paise-exact; GST-exclusive, comparing like-for-like with the GST-exclusive `cost_price`).
2. Reject (HTTP 400) when `eff_unit < cost x (1 + pct/100) - 1e-6`, with a message naming the computed floor: `"Effective price Rs{eff_unit} for {name} is below the cost+{pct}% floor Rs{floor}. Contact a manager."`
3. **Exemptions preserved:** a `Rs 0` / 100%-discount line stays exempt (approval-gated). A line with no known cost (`_cost is None` / 0) is not floored (cannot evaluate).
4. **Raw cost only:** the floor reads `_cost_by_pid[pid]` (the raw `product_repo.cost_price` snapshot). It must NEVER read a masked/role-filtered cost DTO -- cross-reference F35: when F35 ships cost-masking on the READ side, the floor (a server-side WRITE-path guard) continues to read the raw catalog cost, not F35's masked response.
5. **Flag-gated, off by default.** New `feature_toggles` key `cost-floor-enforcement` (default `False`). When off, behavior is exactly today's cost+0% pre-discount check (zero regression). The orchestrator flips it per store after the test session validates on staging.
6. **Single global pct in v1** (matches the E2 registry key). Per-category floor overrides (`pricing.cost_floor_pct.<CATEGORY>`) are a future E2 registry extension, not built here.

## Delta to build

1. **`orders.py`** -- in the order-create handler, after the cart-discount stack (post-`1622`), add a second cost-floor pass over the computed line finals:
   - Resolve `pct = get_policy("pricing.cost_floor_pct", {"store_id": active_store}, default=10.0)` once.
   - Read `flag = feature_toggles.get("cost-floor-enforcement", False)` for the store.
   - If `flag`: for each priced line, `eff_unit = line_final_taxable / qty`; if `_cost > 0` and `eff_unit < _cost * (1 + pct/100) - 1e-6` and the line is not a Rs 0 / 100%-discount line -> raise 400.
   - Keep the existing `1338` cost+0% check as the floor when the flag is OFF (back-compat), OR have the new pass subsume it when ON. (Implementation note: simplest is to LEAVE `1338` as-is and ADD the flagged post-discount pass; when the flag is on, the stricter post-discount cost+10% dominates.)
2. **`settings.py:2279`** -- add `"cost-floor-enforcement": False` to `DEFAULT_FEATURE_TOGGLES`.
3. **No new E2 key** (pricing.cost_floor_pct already exists). No registry change.
4. **`orders.py` cost read** -- reuse the existing `_cost_by_pid` (raw). Do NOT introduce a masked-cost read.

**No FE change required** for enforcement (it's a server guard). Optionally surface the floor breach as a clear POS error toast (already handled by the 400 detail).

## Acceptance tests (INTENT-LEVEL)

1. **Floor enforced post-discount (flag ON).** Product cost Rs 100, `pricing.cost_floor_pct=10`, flag ON. A line priced Rs 150 with a 50% per-line discount (eff Rs 75 < Rs 110) -> **400**. The SAME line at 20% discount (eff Rs 120 >= Rs 110) -> accepted. (Proves the floor reads the post-discount effective price, not the pre-discount Rs 150.)
2. **Cart discount counts.** Two lines that each pass per-line but a 40% cart discount drags one below cost+10% -> the offending line 400s. (Proves cart-level allocation is included.)
3. **Knob is live.** Lower `pricing.cost_floor_pct` to 0 -> the Rs 75 line is accepted (proves orders.py reads E2, not a constant). Raise to 25 -> a Rs 120 line on a Rs 100 cost now 400s.
4. **Flag OFF = no regression.** With the flag OFF, behavior is today's cost+0% pre-discount check exactly; existing order-create tests pass unchanged.
5. **Raw cost, not masked.** A `SALES_CASHIER` placing the order still triggers the floor on the raw server-side cost even though that role cannot SEE cost in any read DTO (proves the guard reads `_cost_by_pid`, never an F35-masked value). Cross-check: when F35 lands, this test still passes.
6. **Rs 0 / 100%-discount exemption preserved.** A 100%-discount (Rs 0) approval-gated line is NOT floored.
7. **Paisa-exact + GST-exclusive.** The comparison uses GST-exclusive taxable per-unit vs GST-exclusive cost; a line exactly at cost+10% (eff == floor) is accepted (boundary).

## Effort + risk

~2 dev-days. **Risk: MEDIUM (POS sell-path).** Mitigated by the off-by-default flag (dark merge) + the post-discount allocation being read-only math on already-computed line finals. The one subtlety is the cart-discount per-line allocation -- reuse the same allocation the GST/taxable computation already does at `1568-1622` so the floor sees the identical effective price the customer is charged.

## Definition of done

- `cost-floor-enforcement` flag exists (default `False`); with it OFF, order-create is byte-identical to today (test 4).
- With it ON, the post-discount cost+10% floor enforces via `get_policy("pricing.cost_floor_pct", scope)` (tests 1-3).
- Floor reads raw `_cost_by_pid`, never a masked DTO; passes with a SALES_CASHIER actor (test 5) and remains correct after F35 ships.
- Rs 0 / 100%-discount exemption preserved (test 6); boundary accepted (test 7).
- No emoji in Python. `tsc -b && vite build` clean (no FE change). Backend smoke route count unchanged.
- **Merges DARK** (flag off); orchestrator flips per store after staging validation (PROTOCOL sec 4).
