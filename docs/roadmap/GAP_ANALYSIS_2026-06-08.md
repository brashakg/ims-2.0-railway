# GAP_ANALYSIS — IMS 2.0 Enterprise Roadmap (Consolidated, Chair)

> Produced 2026-06-08 by a 5-auditor + chair fan-out (roadmap internal consistency · coverage vs
> business intent · packet grounding vs live code · dependency/sequencing · DECISIONS/SYSTEM_INTENT
> fidelity). Each claim re-grounded against live code + the roadmap docs; duplicates merged,
> conflicts resolved with the Chair's call noted. Severity re-ranked by build-blast-radius.

Precedence reminder (correct, keep): **DECISIONS > CORRECTIONS > ENGINES.md / engines/* / packets.**

Tally: **2 P0**, **11 P1**, plus P2/P3 hygiene. None block the program; all are mechanical doc fixes
the orchestrator applies before promoting items.

---

## 1. Health verdict (1 paragraph)

The roadmap is in good health and the board is safe to keep running. The adversarial
HARDENING/CORRECTIONS pass already caught and neutralized every *dangerous* engine-level intent
violation (E1 cross-collection atomic dual-write, E3 unit-level hash-chain, #30/#31 SPIFF
double-feed, cost+10% floor ownership), and no claimable board item violates an absolute
SYSTEM_INTENT pricing/GST/audit law. The defect that remains is **structural, not logical**:
corrections were folded into the `features/*.md` packets (the board's TODO source) but were **not
back-ported into the lower-precedence `engines/*.md` contract files** — so HARDENING's own
GO-condition 2 ("per-engine contracts reconciled") was only *partially* met, and 5 of 8 engine
contracts still literally instruct the dead/dangerous text. Layered on top are **two live Phase-0
packets that contradict themselves** (E1's body still orders the cancelled `money_accounts`; F21's
body still says "extend the enum" against its own "free string" banner), one **new false grounding
claim** (F35 asserts a `_FINANCE_ROLES` gate that does not exist), and a cluster of
**fold-before-TODO divergences** in later-phase packets (E4/SC/E5). Every issue is a mechanical doc
edit; the fix is to make the lower layer self-consistent with the higher layer the build session is
*supposed* to read but predictably won't.

---

## 2. THE SYSTEMIC FIX — packet-vs-CORRECTIONS/phasing drift

**Root cause (agreed by 4 of 5 auditors):** the build session opens the *lowest-precedence* file
(the packet body, or worse, the un-folded `engines/*.md`), not the correction. The precedence rule
is correct on paper but defeated in practice because **the wrong text is duplicated into the layer
that is actually read**. A self-contradicting document is worse than a wrong one: a build agent
picks whichever half it read.

**The rule the orchestrator applies at every BACKLOG -> TODO promotion (add to PROTOCOL §11 as the
"single-source-of-truth fold gate"):**

> **No item enters TODO until its packet is the SINGLE consistent source for that item — internally
> and against CORRECTIONS — with zero live pointer to an un-folded engine contract.** Concretely,
> before flipping any row to TODO the orchestrator MUST, in the packet:
> 1. **Grep the packet against its own banner.** If the ORCHESTRATOR-RESOLUTION / BINDING-CORRECTIONS
>    banner cancels or renames a thing (a collection, an enum, a function, a multiplier), **delete
>    every body occurrence that still orders the old thing** — Delta steps, Data-model blocks,
>    indexes, acceptance tests, and DoD lines included. A banner that says "X is cancelled" with a
>    body that builds X is a BLOCK condition (PROTOCOL §11), not a TODO.
> 2. **Fold the matching CORRECTIONS entry into the body** (not just cite it): replace dead names
>    with the locked names — `append_audit_entry` -> `AuditRepository.create`;
>    `resolve_setting`/`resolve_settings` -> `get_policy`/`get_policies`; `money_accounts` SoR ->
>    per-type single-doc collection (R1); `1.4@14%` -> `1.1@14%`; `QUARANTINE` -> the canonical
>    `QUARANTINED` free string (no enum).
> 3. **Quarantine cross-phase consumer tests.** Any acceptance test that asserts behavior of a
>    consumer in a *later* phase is relabeled `DEFERRED — cross-phase, NOT gating this item` and
>    **struck from the packet's merge-gating DoD checklist**. An engine's DoD gates only on the
>    engine's own tests.
> 4. **Kill the dangling engine pointer.** Either delete `engines/<id>.md` and repoint
>    `ENGINES.md:5` at `features/<id>.md`, or stamp the engine file with a
>    `SUPERSEDED-BY-CORRECTIONS — DO NOT BUILD FROM THIS FILE` banner + back-port the locked names.
>    **No build session may open `engines/*.md` while it still carries dead text.**

**Why this is the fix and not more banners:** banners already exist and were ignored *within the same
file*. The rule forces the lower layer to physically stop containing the wrong instruction, so there
is no half for an agent to pick. This is the exact failure that produced the E1/#563 QA
reconciliation ("orchestrator to reconcile packet T13/item-6/DoD-5 wording") — bake the reconciliation
into the promotion gate so it never recurs.

---

## 3. TRUE coverage gaps (business features) + the phase each belongs in

**Confidence note (Chair):** the "coverage" auditor returned a degenerate/placeholder finding — a
single run-on list with no per-item grounding. I treat it as **LOW confidence** and do not promote
any single recon code to a board gap on its strength. `_analysis/_appendix_recon.md` confirms several
codes already map to roadmap items (POS-2=EMI, INV-7=vendor SKU aliases, INV-9=demand-forecast PO,
CRM-2=CL auto-refill — several flagged "REUSE, live"), so **the coverage list is contaminated with
already-built / already-mapped items** and cannot be trusted as a true-gap list.

Two genuine structural coverage gaps survive cross-checking:

| Gap | Why it's real | Phase |
|---|---|---|
| **No coverage-reconciliation index** mapping recon codes -> roadmap ID -> built/partial/missing. | The board has no way to prove a recon feature isn't silently dropped between phases. | Pre-flight / Phase 0 (orchestrator doc) |
| **cost+10%/category price floor (DECISIONS §9) has a board row but NO packet.** | `EXECUTION_BOARD.md` line-81 is a bare "NEW" row, no F-file; F41 only NOTEs it, F35 disclaims it. A LOCKED absolute pricing law is unowned/unwritten. | Phase 2 (needs the packet written) |

The coverage auditor's other codes (FIN/POS/INV/CLI/CRM/BVI/RPT) should be **run through a proper
recon-vs-board reconciliation** (one orchestrator pass) before any is added to the board — do not
promote them as gaps on this audit alone.

---

## 4. False / stale packet grounding claims to correct (packet -> real code)

| # | Packet claim | Reality (verified) | Sev | Fix |
|---|---|---|---|---|
| G1 | **F35:74** — gross_margin/cogs "already behind `_FINANCE_ROLES`" | **FALSE — `_FINANCE_ROLES` does not exist.** `get_pnl` (finance.py:629) is gated only by `get_current_user` + `_scope_store` (store, NOT role). `gross_margin/cogs/net_margin` reach **every authenticated role** today (SALES_CASHIER, OPTOMETRIST included). | **P1** | Rewrite F35:74; the packet MUST ADD the role gate (mask cost for any role not in COST_VISIBLE_ROLES, exclude AREA_MANAGER per DECISIONS §9), not just add a carve-out. Add test: `SALES_CASHIER GET /finance/pnl` has `gross_margin`/`cogs` absent. **Most likely to ship a hollow shell.** |
| G2 | **SC.md:304/307** worked-example pool figures | Math inconsistency (T3 pool ₹24,937 vs T4 "pool=₹24,310"), NOT the multiplier bug. **1.1@14% is CORRECT** (incentive_settings_repository.py:62-68). | P2 | Pick ONE canonical `(last_year, this_year, avg_disc)` tuple; recompute both tests from it. |
| G3 | **F40:15** crm.py:857 `_determine_lifecycle_phase()` | def at **crm.py:830**; :857 is the VIP branch inside it. Behavior correct. | P3 | F40:15 -> "crm.py:830 (VIP branch at :857)". |
| G4 | PM:13 catalog.py:174 / SC:19 repo:63-68 / E3 inventory.py:2147 | Off-by-1-3 line drifts (HEARING_AID at catalog.py:172; table at :62; :2147 is a collection handle not the `$match`). Substance holds. | P3 | Tighten line refs; cite rollups by function name. |
| G5 | F21 "POS catalog AVAILABLE filter already excludes it" | True via the **allowlist** nature of `find_available` + `_on_hand_by_product` (4-value allowlist), NOT an explicit QUARANTINE exclusion. Conclusion correct; phrasing under-describes. | P3 | Note both filters are allowlists; exclusion works because QUARANTINED is in neither. |
| G6 (positive) | F35 `_build_store_ledger` margin / E2 cost+0% / F32-F41 "already satisfied" | **Correctly neutralized** in current packets. No regression — keep. | — | No action. |

---

## 5. Dependency / sequencing corrections + clean build order

- **D1 [P1] — STATUS.md still lists E1 as claimable (STALE).** E1 is DONE (PR #563); E2 is IN BUILD.
  Two of the seven listed are not claimable. Fix STATUS.md to the genuinely-claimable set.
- **D2 [P1] — #34 and E6 depend on E2, which is only on the unmerged `feat/E2-settings-matrix`
  branch (not on `main`).** `get_policy` is unavailable on main. Split TODO into "claimable now
  (#35, #40, #21)" vs "BLOCKED-on-E2-merge (#34, E6)" — keep them in TODO with a loud note, not
  BACKLOG.
- **D3 [P3, conflict resolved] — "F34 and F40 share one absent orders index" is FALSE.** F34 is the
  **sole** owner of the new `{created_at,status,store_id}` orders index; F40 adds NO orders index
  (it adds `vip_churn_snapshots {store_id,scanned_at}` and states the orders index is already
  present). No collision. Downgrade the auditor's P1 -> P3 (note F34 remains the single owner).

**Recommended clean build order (Phase 0 -> 1), dependency-aware:**
1. **Now, parallel (no deps, on `main`):** #35 (after G1 fix), #40, #21 (after F21 P0 fix, on its E3-shim).
2. **Merge `feat/E2-settings-matrix` to main** (unblocks the rest). Verify E2's CORRECTIONS P1 tests
   (secret-encrypt via `_encrypt_value`, explicit `cache.delete`, **luxury-cap LOWER-only invariant
   test** — see I2, entity-missing->global).
3. **After E2 on main:** #34, E6.
4. **Pre-flight, parallel:** merge `claude/fix-money-integrity` + GST FY-serial; owner approves MSG91
   DLT (WhatsApp DISABLED per STATUS COMMS DIRECTIVE — message-send features build DARK only).
5. **Phase 1 (fold CORRECTIONS into each packet first):** E3 (drop hash-chain/dual-write), E4 (kill
   `_DEFAULT_TIERS`/`resolve_setting`), PM, SC (delegate to E2 `get_policy`). **Author
   `features/E5.md`** (does not exist) from corrected names before any Phase-2 item claims it.

---

## 6. DECISIONS / SYSTEM_INTENT violations + intent-gaps

- **I1 [P1] — cost+10%/category price floor (DECISIONS §9) has a board row but NO packet.** Write the
  packet before promoting line-81: read `pricing.cost_floor_pct` via E2 `get_policy(scope)` (default
  10), apply per-category at orders.py:1338 **AFTER all discounts/vouchers/cart-discount stack**,
  paisa-exact. Tests mirror E1.md:275 (unit_price=cost×1.05 -> blocked) **AND a SALES_CASHIER test
  proving the floor reads raw server-side cost, never the F35-masked DTO** (the two features share
  `cost_price` with opposite intents — cross-reference F35:224 so the floor is never wired through
  `mask_cost`).
- **I2 [P1, highest E2 risk] — E2 luxury-cap "LOWER-only, brand caps are NOT E2 keys" must gate the
  E2 PR.** A store raising a Cartier 2% cap is FORBIDDEN; if the `set_policy` validator for
  `pricing.category_caps.*` is omitted, E2 silently allows it. The E2 branch MUST ship a test:
  `set_policy('pricing.category_caps.LUXURY', > code constant)` REJECTED; lower accepted; NO registry
  key for per-brand caps. _(Status: the IN-BUILD E2 engine already implements `_luxury_guard` against
  the `pricing_caps` code constant; the gating TEST is in the E2 test plan as T10.)_
- **I3 [P2] — Autonomous AI DB-writes (F34 ORACLE milestones_fired/bell; F40 vip_churn_risk subdocs)
  not reconciled vs SYSTEM_INTENT §8 "AI CANNOT execute changes."** Add one clause to F34/F40 (+ §8)
  classifying "AI writing derived analytics it computed + queuing an in-app advisory bell" as
  **advisory output, NOT a §8 change/approve/block/override** — no customer-facing
  balance/price/order/approval is touched; the only customer-message path (F40 WINBACK_WHATSAPP)
  writes a PENDING row gated by DISPATCH_MODE.
- **I4 [P3] — `_analysis/OWNER_DECISIONS.md:14` says "10-min PIN validity"** vs the LOCKED **60-min**
  (DECISIONS §4). A build agent reading the analysis layer would expire approvals 6× too fast.
  Annotate "SUPERSEDED -> 60-min".
- **I5 [P3] — F39 invents a ₹15k-single-order VIP-slot gate surfaced as "DECISIONS §3 LOCKED"** — but
  §3 only locks "15 cards/day, reserve top 2 VIP slots". Mark the ₹15k threshold E2-tunable (not
  LOCKED); distinguish from the ₹1L-LTV lifecycle VIP; flag for owner confirmation.
- **I6 [P2] — #49 family-wallet LOCKED controls split across DECISIONS §3 + CORRECTIONS R1 + ENGINES
  OTP contract with no consolidated packet.** At promotion (Phase 3) author F49 with the reconciled
  model in one place: `family_wallets` single-doc collection (R1), OTP via E6 to primary member's
  mobile only (route via **SMS** — WhatsApp disabled), `loyalty.pool_max_members` default 7 via E2,
  hold carries only `otp_id`.

---

## 7. PER-DOC ACTION LIST (exact file -> change)

### LIVE / Phase-0 — fix NOW

- **`features/E1.md`** — **[P0]** Body contradicts its banner. Delete/rewrite item-6 (line 74),
  Delta step-3 (106), the Data-model `money_accounts` block + indexes (133-172), test **T13**
  (271-272) -> assert `credit('PETTY_CASH', store, X)` returns `reason="unavailable"`, and DoD-5
  (308). New types are per-feature dedicated single-doc collections (CORRECTIONS R1). _(E1 already
  shipped this way in #563; this closes the packet to match.)_
- **`features/F21.md`** — **[P0]** (a) lines 30/44/70 "new/extend **enum** value" -> "set
  `stock_units.status` to the FREE STRING value (no enum / `$jsonSchema` change)"; (b) the written
  value `QUARANTINE` (32×) vs the canonical `QUARANTINED` (board/CORRECTIONS/banner) — **replace
  every literal with `QUARANTINED`** so the writer, the 5 on-hand rollup exclusions (inventory.py
  107/2147/2274/2366/2731), the transfers-reject guard, and the tests all key off the identical
  string. Per PROTOCOL §11 this contradiction should have BLOCKED promotion.
- **`features/F35.md`** — **[P1]** Correct line 74 (`_FINANCE_ROLES` does not exist; `/pnl` is
  store-scoped only). Add the real role-gate delta + the `SALES_CASHIER cost absent` test (G1).
- **`STATUS.md`** — **[P1]** Remove E1 (DONE) and E2 (IN BUILD) from the claimable set (D1/D2).
- **`EXECUTION_BOARD.md`** — **[P1]** Split TODO claimable-now vs BLOCKED-on-E2-merge (#34, E6);
  add a `Packet` column to BACKLOG tables; keep the Phase-2 cost-floor row as the §9 owner (I1);
  note F34 is the sole owner of the new orders index (D3).
- **`features/E2.md` + branch** — **[P1]** Merge gates on the luxury-cap LOWER-only test + no
  per-brand registry key (I2) + secret-encrypt / explicit-cache-delete / entity-missing->global.
- **`features/F34.md` + `features/F40.md`** — **[P2]** Add the §8 advisory-AI carve-out (I3); F40
  fix line ref 857->830 (G3).

### Lower-precedence engine contracts — back-port BEFORE any build opens them (HARDENING GO-2, unmet)

**Chair's call:** prefer **(a) delete `engines/{E1,E3,E4,E5,SC}.md` and repoint `ENGINES.md:5` at
`features/*.md`**. If kept, each gets a `SUPERSEDED-BY-CORRECTIONS — DO NOT BUILD` banner + back-ports:
- **`engines/E1.md`** [P1] — delete dual-write clause (:175) + `money_accounts` SoR + Phase B/C
  (38-43, 85-118, 159-161, 169, 195); add per-type-collection (R1).
- **`engines/E3.md`** [P1] — `append_audit_entry` -> `AuditRepository.create` (17,54,92,135); drop
  unit hash-chain + `stock_audit` dual-write (118,165); monotonic `event_seq` (CORRECTIONS P0-2).
- **`engines/E5.md`** [P1] — `append_audit_entry` -> `AuditRepository.create` (21,123). **This is the
  ONLY E5 source** (no `features/E5.md`) — author the feature packet from corrected names.
- **`engines/SC.md`** [P1] — multiplier `1.4@14%` (:12, :160) -> **1.1@14%** (matches `features/SC.md`
  + seeded table); SUPERSEDED banner.
- **`engines/E4.md`** [P1] — `resolve_setting` -> `get_policy`/`get_policies` (174,224); remove
  `_DEFAULT_TIERS` as engine-owned (E2 registry default IS the fallback).

### Later-phase packets — fold BEFORE promoting BACKLOG -> TODO

- **`features/E4.md`** [P1] — `resolve_setting` -> `get_policy(refund.tier.*)` (136); remove
  `_DEFAULT_TIERS` (84,136,329,378); `required_tier` reads from E2.
- **`features/SC.md`** [P1] — rewrite `resolve_settings` (53,153,322,340,352) to delegate precedence
  to E2 `get_policy` (keep `incentive_settings` as override store, but do NOT re-walk stores in a 4th
  merge); fix worked-example math (G2) + line refs (G4).
- **Cross-phase consumer tests** [P1] — relabel + strike from merge-gating DoD: `features/E1.md`
  **T14** (cross-phase cost-floor); `features/E2.md` **test-4** (orders.py reject below cost×1.10 —
  Phase-2 consumer) and **test-5** ("verifies E4 reads E2" — E4 unbuilt) -> "consumer integration
  (runs when consumer ships)" + strike from E2 DoD (318-319). E2 DoD gates only on
  resolver+RBAC+secret+audit+schema-UI.

### New docs to author

- **`features/E5.md`** [P1] — does not exist; author from `engines/E5.md`'s corrected sink +
  STORE_CREDIT-tender-deferral + operates-on-existing-`order.payments[]`-only, before #16/#23/#27.
- **Phase-2 cost-floor packet** (DECISIONS §9) [P1] — write before promoting line-81 (I1).
- **`features/F49.md`** (Phase 3) [P2] — reconciled family-wallet model (I6).
- **Coverage-reconciliation index** [P2/LOW-conf] — one pass mapping recon/Excel codes -> roadmap ID
  -> built/partial/missing (§3).

### Analysis-layer hygiene

- **`_analysis/OWNER_DECISIONS.md:14`** [P3] — annotate "10-min PIN" -> "SUPERSEDED -> 60-min" (I4).
- **`features/F39.md`** [P3] — mark the ₹15k VIP-slot gate E2-tunable, distinguish from ₹1L
  lifecycle VIP (I5).

---

## Prioritized next-actions (do in this order)

1. **[P0]** Fix `features/E1.md` body to match its banner.
2. **[P0]** Fix `features/F21.md` — free-string (no enum) + canonical `QUARANTINED` everywhere; or BLOCK F21.
3. **[P1]** Fix `features/F35.md:74` + add the cost-absent acceptance test.
4. **[P1]** Update `STATUS.md`/`EXECUTION_BOARD.md`: E1 DONE, E2 IN BUILD; split TODO.
5. **[P1]** Gate the IN-BUILD E2 PR on the luxury-cap LOWER-only test (I2).
6. **[P1]** Back-port locked names into (or delete + repoint) `engines/{E1,E3,E4,E5,SC}.md`.
7. **[P1]** Pre-fold E4/SC packets + quarantine cross-phase consumer tests out of merge-gating DoDs.
8. **[P1]** Author `features/E5.md` + the Phase-2 cost-floor packet from corrected sources.
9. **[P2/P3]** AI §8 carve-out, F49 note, line-ref tightening, OWNER_DECISIONS annotation, F39 note,
   SC math, coverage-reconciliation pass.

**Adopt the §2 single-source-of-truth fold gate into PROTOCOL §11 so the whole class of drift stops
recurring.**
