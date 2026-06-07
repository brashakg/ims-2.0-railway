# IMS 2.0 Enterprise Roadmap — LOCKED DECISIONS

> **Authoritative.** This file is law for the build session and the test session. If code
> conflicts with this file, the code is wrong. Owner = Avinash (non-technical). Last locked:
> 2026-06-07. Source analysis: `_analysis/` (chair roadmap, 52 design packets, recon, raw
> per-feature decisions). Real-business spec: `C:\Users\avina\OneDrive\Desktop\Excels\IMS_SOURCE_EXCELS_REFERENCE.md`.

---

## 1. Execution rules (how the 3 sessions work)

- **Roles:** orchestrator session owns plan/packets/sequencing; **build** session implements; **test** session QAs. Workers coordinate ONLY via this repo (file-based board), never via chat context.
- **Branching:** each worker works on its own branch; never commit directly to `main` from two sessions at once. Board (`EXECUTION_BOARD.md`) is the single source of truth for who-owns-what.
- **Merge:** auto-merge a PR once the test session + CI are green. No per-merge owner sign-off.
- **POS safety:** every POS/checkout change ships behind an **off-by-default feature flag**. Test session verifies on a staging cart; **orchestrator flips the flag on per store** when green. Do NOT ask the owner per POS change.
- **Order:** quick-wins first, then by dependency. Maximum parallel agents. Council-style deliberation for architecture.
- **INTENT-FIDELITY GATE (hard):** an existing router/table/page does NOT mean a feature is done. Every packet states **Current behavior → Intended behavior (full intent) → Delta to build**. Acceptance tests assert the *intended behavior*, so a half-wired shell FAILS QA and bounces back. Reuse = graft the intent onto the foundation, never ship a hollow shell.
- **Constraints:** no emojis in Python (cp1252); light-only restrained/executive UI (neutral + single accent, colour only for meaning); IST tz, FY Apr-Mar, paisa-exact; **every legacy Excel colour-flag becomes an explicit status enum — never replicate cell colour.**

---

## 2. Owner-decided — cross-cutting (the 10)

| # | Decision | Locked answer |
|---|---|---|
| 1 | MSG91 / comms go-live | **UPDATE 2026-06-07: WhatsApp Business DISABLED by Meta** (healthcare/commerce policy) -- owner appealing. **SMS (DLT) is the fallback channel and works.** Build comms behind flag; message-SEND features are DEFERRED (see STATUS.md COMMS CHANNEL DIRECTIVE); `DISPATCH_MODE=live` only after WhatsApp restored OR SMS templates approved. |
| 2 | Settings hierarchy (E2) | **global → entity → store** override order. |
| 3 | Tally ledger names (E5) | **Use IMS defaults now**; accountant remaps in Tally. (Owner may supply real chart-of-accounts later to upgrade.) |
| 4 | Approval/PIN model (E4) | **Per-approver PIN** (real audit trail), **60-minute** request validity. |
| 5 | Money-integrity + GST invoice | **Merge `claude/fix-money-integrity` now** + move invoice numbering to **consecutive serial per financial year**. |
| 6 | Refund/discount tiers | **Configurable in Settings**; default **auto < ₹500 / admin > ₹2,000 / superadmin > ₹10,000**; refunds **always to original tender**. |
| 7 | Serial tracking scope | **Opt-in per SKU**; on return-serial mismatch **hard-block the refund** until manager PIN override. |
| 8 | Cash-variance + reconciliation | **Per-store thresholds** (global default ₹0/₹100/₹500, once-daily), editable per store via E2. |
| 9 | Price floor | **cost_price + 10% floor, per category** (system blocks below-floor even when discounts stack); promo ceiling default 30%; **no further staff discount on liquidation items**. ⚠️ NOT yet enforced: `orders.py:1335` is currently cost+0%; upgrading to cost+10%-per-category (reading `pricing.cost_floor_pct` via E2) is an explicit board item (EXECUTION_BOARD Phase-2). |
| 10 | Walkout + message cap | Walkout = **soft-block** (banner + compliance score, never blocks POS); **max 3 automated messages / customer / 30 days** across all rules. |

---

## 3. Owner-decided — strategic (the 12)

| Area | Locked answer |
|---|---|
| Lab routing (#2) | **Disposable barcoded job cards** (one per job); no reusable-tray master. |
| Optometrist dashboard (#24) | Optometrists see **test count + conversion rate only; revenue hidden**. Managers see revenue. |
| Sale credit (#45/#50) | Walkout/handover conversion credit **split 50/50** between logging associate and closing associate. |
| NBA list (#39) | **15 cards/day**, **reserve top 2 slots for VIPs**. |
| Promo stacking (#11) | **Exclusive** — only the single best promo fires by default (per-campaign can opt into stacking). |
| Family wallet (#49) | Household tier applies to **any member, anywhere** (anchor need not be present); **max 7 members**; **pool redemption requires OTP to the primary member's mobile**. |
| Inventory balancing (#1) | **All brands included** (incl. luxury); **90-day** idle threshold for transfer suggestions. |
| Blind stock count (#15) | **Transparent** ("blind count in progress" shown) + **soft-lock** (items flagged 'under audit', cashier warned, sale not blocked). |
| Commission basis (#31) | *(Superseded by §4 incentive model — PUNE scorecard+slab is the model of record.)* **#31 clawback is an SC negative adjustment folded into the locked snapshot; there is NO standalone commission engine. SC is the sole payroll incentive feed.** Base earning measure where still referenced: % of sale value, LUXURY+PREMIUM only. |
| SPIFF approval (#30) | **Auto-approve**; shown as a named "SPIFF Bonus" line on the payslip. *(F30's manager-approval owner-question is CLOSED — auto-approve is locked.)* SPIFF is built as an **SC Kicker**, not a parallel feed. |
| Own-use allowance (#32) | **Tiered by role, family allowed** (immediate family); rupee amounts in Settings (default ₹3k staff / ₹8k manager / ₹15k admin). |
| Vendor RTV (#20) | **Lock ALL** that vendor's outstanding bills until full credit received; ADMIN override available. |

---

## 4. Owner-decided — source-workbook (Excel reference)

| Topic | Locked answer |
|---|---|
| SOP.xlsx identity | **External best-practice template** ("Specs Bunker", Siliguri). **Adopt its structure/workflows; do NOT migrate its data.** |
| Incentive model of record | **Adopt the PUNE INCENTIVE engine chain-wide**: daily 9-component /100 scorecard → eligibility tiers (<70=0%, 70-79=60%, 80-94=80%, 95-100=100%) → monthly sales-growth slab pool × per-staff weightage (+ manager bonus). **PRODUCT INCENTIVE (premium Zeiss/Safilo attach) folds in as a Kicker.** This is the official incentive engine; the simple per-line commission (§3) is secondary/superseded. |
| POS advance+on-delivery split | **SKIPPED for now — do NOT change the POS payment-capture model.** This skip does **NOT** block #16/#22/#23 — they read the EXISTING `order.payments[]` and add only derived/reconciliation records (E5). Only the phase-split (ADVANCE/ON_DELIVERY) + per-EDC capture is deferred. Revisit before finance daily-reconciliation (#16); model documented in Excel ref §I.6. |
| Contact-lens GST | **5%** (same as frames/optical lenses). |
| Footfall capture | **Manual only** — staff enter walk-in counts; conversion % auto-computes from walk-ins vs walkouts. |
| Colour-flag migration | Every legacy sheet colour (green=done, red=problem, amber=pending, yellow=header) becomes an **explicit status enum field** in IMS. |

---

## 5. Net-new features added from the workbooks (fold into roadmap)

These were under-specified or missing in the original 52; they jump to Tier-1/2:
- **N1. Walkout / Lost-sale CRM** — real 30-field schema + 2-stage follow-up (FU1/FU2) + result pipeline (WON/CONVERTED/LOST/DUE/NEGATIVE) + "FU Due Today" call-list wired to Tasks/WhatsApp. *(grounds #45)*
- **N2. Staff Scorecard + Slab-Incentive engine** — the §4 PUNE model; distinct from Payroll. *(grounds #30/#33/#34)*
- **N3. Footfall + Conversion %** — manual walk-in capture per staff/day. *(grounds #24)*
- **N4. Vendor RMA / Credit-Note module** — unify lens returns (Essilor/Zeiss/Alifnoor/GKB/Vinod), Luxottica warranty (notification/material/courier), Zeiss replacement (SOP Credit Note Tracking): patient-linked, reason taxonomy, **courier tracking**, CN recovery. *(grounds #14/#20)*
- **N5. Unified Product Master** — category-conditional required fields + SKU-prefix rule (`PREFIX+BRAND+MODEL+COLORCODE+SIZE`), superset PIM attributes. *(foundation; affects #6/#10/#36)*
- **N6. Base-Bank replenishment** — `Required = Base Bank − In Hand`; planogram slot count + readers/CL power grids + CL colour axis. *(grounds #1/#7/#15)*
- **N7. CL Purchase-Order generator** — SPH/CYL/AXIS/Qty PO to vendor. *(net-new)*
- **N8. Owner "Survival" cash-flow** — fixed-cost vs payable vs income, min-pay scenario. *(extends Finance/#16/#25)*
- **N9. Courier/logistics tracking** — on inter-store transfers (#1/#38) and RMA (N4).
- **N10. "2nd pair 50%" promo tracker** — first-pair↔second-pair redemption. *(under #11)*

---

## 6. Owner DATA / actions still needed (by phase — not blocking Phase 0)

| Needed before | Owner provides |
|---|---|
| Comms tier (Phase 0/1) | Approve MSG91 DLT templates (utility first) — **in motion** |
| #25 Journal entries (Phase 1) | Which expense heads you post manually in Tally |
| #17 Petty cash (Phase 1-2) | Opening petty-cash float + low-balance alert per store |
| #16 Reconciliation (Phase 2) | Which bank(s) per entity (statement format) |
| #28 IP attendance | Whether each store has a static IP |
| #36 Shopify (Phase 5) | Exact luxury brands to hard-lock offline + which stores do pickup |
| Optional upgrade | Real Tally ledger names per tender |

---

## 7. All other per-feature questions

The ~240 remaining fine-grained questions are **defaulted to the recommended option** in `_analysis/_raw_owner_decisions.md` and surfaced in Settings (editable anytime). Owner may override any default via Settings or by editing this file. Strategic later-phase items (consignment damage/settlement, B2B tiers, Shopify specifics) will be re-confirmed as their phase approaches.
