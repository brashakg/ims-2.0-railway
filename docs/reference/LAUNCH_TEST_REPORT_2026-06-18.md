# IMS 2.0 — Pre-Launch QA + Hardening Report — 2026-06-18

**Verdict: CONDITIONAL GO.** No P0 code defects. Every confirmed P0/P1 found this campaign is fixed (PRs merged or merging on green CI). Launch is gated only on **owner foundation-data entry** + a couple of ops/env items below — not on code.

Target: backend `https://ims-20-railway-production.up.railway.app`, frontend `https://ims-2-0-railway.vercel.app`.
Method: parallel multi-agent QA cycles (live-API + read-only code audit) with adversarial re-verification before reporting; living detail in `LIVE_QA_2026-06-18.md`.

---

## 1. Scope tested (every module)
POS billing (incl. inter-state IGST, all tenders, EMI, lifecycle, discount caps), Clinical/Rx, Workshop + vendor-portal, Inventory + transfers, Purchase/GRN, Finance/GST/period-lock/GL, CRM/customers, Reports/Analytics (numbers reconciled), Tasks/SOP/escalation, Incentives, HR/Payroll (statutory math hand-verified), Settings, Integrations/webhooks, AI/Jarvis, plus auth/JWT, the full per-role write-matrix, statutory exports, data-integrity, performance, and the ~20 previously-untested routers.

## 2. Verified WORKING (high confidence)
- **POS billing**: GST-to-the-rupee, CGST/SGST vs IGST by state, role→category→luxury discount precedence, offer<MRP rule, split/EMI/credit/advance/voucher/loyalty tenders, idempotency, terminal-state guards, zero/100%-discount approval gate.
- **Security**: both prior P0s (forged-webhook ingestion; cross-customer Rx IDOR) re-verified FIXED. Auth/JWT hardened (alg-none rejected, store-switch no-escalation, login-enumeration blocked). Per-role write-matrix clean (INVESTOR fully blocked). Jarvis SUPERADMIN-only. Integration secrets Fernet-encrypted + masked. Audit log append-only/immutable. Vendor-portal token scope-locked + expiring + rate-limited + PII-redacted. All ~20 untested routers properly auth-gated (0 gaps).
- **Payroll**: EPF/EPS(15k)/ESI(21k gate)/PT/LWP math EXACT; Tally JV balanced; run idempotent; DRAFT→APPROVED→PAID controls.
- **Incentives**: payout double-lock race-safe; scorecard sums to 100; payout math reconciled.
- **Workshop**: QC gate server-enforced (can't reach READY without QC pass/waive).
- **Tasks/SOP**: SLA two-clock, role-ladder escalation, in-app bell, SOP completion — all correct.
- **Exports**: Tally JV/day-voucher balanced + XML-escaped; CSV formula-injection neutralized; PF-ECR caps.
- **Org module**: entity→GSTIN→store hierarchy + validation verified correct end-to-end.

## 3. Bugs FIXED this campaign (PRs)
| PR | Fix | Sev | Status |
|----|-----|-----|--------|
| #761 | CRM 360/lifecycle/prescriptions 500-for-all (datetime parse) | P1 | MERGED |
| #762 | GSTR-1 export dropped all credit notes (CDNR) + product HSN required | P1 | MERGED |
| #763 | Payroll-config cross-store PII IDOR + task-ownership bypass | P2 | MERGED |
| #764 | Analytics counted cancelled orders as revenue + inventory mislabel | P1 | MERGED |
| #765 | Customer dup accounts + loyalty double-earn race + online-orders cross-store leak | P1 | MERGED |
| #760 | Transfer damaged→quarantine, qty-cap, mismatch task, dead-stubs removed, forged-attachment block | 4×P1 | MERGED |
| #759 | POS Rx: impossible-power reject (all lines) + spectacle-lens Rx required (contacts exempt per owner) | P1 | merging |
| #767 | Payroll approve/lock now enforce period-lock | P1 | merging |
| #766 | f8 variance tests made clock-stable (test-only) | — | OPEN (owner asked: don't merge) |

## 4. DEFERRED (with reasons — none block a fresh-data launch)
- **Report-query performance at scale** (unbounded fetches): safe at fresh-launch low volume; aggregation refactor on financial reports is risky to rush — logged as post-launch hardening, not pretended-done.
- **CL-FEFO dispense + workshop pickup-name** (BUG-016/017): small follow-up after #759/#760 settle (both touch orders.py/inventory.py); CL volume low; pickup notify still sends (just generic name).
- **Error-message hygiene** (`str(e)` in ~6 routers), multi-entity inter-state RCM, trial-balance endpoint, patient mid-word search: low-blast / multi-entity-only / cosmetic.

## 5. OWNER ACTION ITEMS before flipping live
1. **Foundation data (the real gate):** create entities → per-state GSTINs → stores → vendors → products. (Org module verified correct; the app is empty by design after the prior wipe.)
2. **Ops:** confirm prod **MongoDB/GridFS** is healthy so GRN attachment upload works (the 503 seen in testing was correct fail-loud, not a code bug).
3. **Env:** set `CREDENTIAL_ENCRYPTION_KEY`; reset the default seed passwords; fix the `PAGESPEED` var; keep `DISPATCH_MODE` off until you want live WhatsApp/SMS.
4. **Decide:** merge #766 (f8 test stability) at your convenience; approve the small BUG-016/017 follow-up.

## 6. Test artifacts (cleanup pending YOUR go-ahead — not yet deleted)
12 `zz_test_*` users · 5 `ZZ_TEST_*` products · customer 9000000088 · 3 deactivated CRM test customers · ZZ_TEST payroll entity + 2 configs (deactivated) · 2 far-future incentive snapshots (harmless) · cancelled test orders. I will delete these on your word (kept available in case you want to re-verify any fix live first).
