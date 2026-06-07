# Owner decisions to lock before hand-off

10 business decisions, ordered by how many of the 52 features each unblocks. Each has a
**recommended** option. Avinash confirms or overrides; answers go into `DECISIONS.md`
(authoritative for both worker sessions). Only #1, #2 and the #10 frequency-cap actually
gate the very first (Phase-0) build batch; the rest gate later phases but are cleared now
so nothing stalls.

| # | Decision | Unblocks | Recommendation | Answer |
|---|---|---|---|---|
| 1 | MSG91 DLT template approval + `DISPATCH_MODE=live` (owner action) | #46,#41,#47,#40,#45,#42,#51,#52 | Start now, utility templates first (ORDER_READY, RX_EXPIRY) | _pending_ |
| 2 | Settings hierarchy (E2 engine) | #10,#14,#28,#34,#41,#46,#48,#49 + all flags | global → entity → store override | _pending_ |
| 3 | Tally ledger names per tender (E5) | #16,#22,#23,#27,#17,#20,#25 | Share Tally chart-of-accounts (with accountant) | _pending_ |
| 4 | Approval/PIN model (E4) | #17,#25,#26,#27,#38,#44 | Per-approver PIN, 10-min validity | _pending_ |
| 5 | Money-integrity merge + GST invoice FY-serial | Phase 2 (#16/#22/#23/#27), POS-promo, GST compliance | Merge now + approve FY-serial change | _pending_ |
| 6 | Refund/discount ₹ tiers + original-tender | #27,#26,#22-A,#17 | auto <₹500 / admin >₹2,000 / superadmin >₹10,000; refund to original tender | _pending_ |
| 7 | Serial scope + return-mismatch policy (#6) | #6 (+#15) | 7 luxury brands + soft-flag (P1 task) | _pending_ |
| 8 | Cash-variance tiers + reconciliation cadence (#23/#16) | #23,#16 | ₹0/₹100/₹500, once-daily | _pending_ |
| 9 | Liquidation/promo floors + stacking (#10/#11/#12) | #10,#11,#12 | liq 40% of MRP, promo ceiling 30%, no further staff discount on liquidation | _pending_ |
| 10 | Walkout block mode + message frequency cap (#45/#46) | #45,#46 + all E6 consumers | soft-block walkout + cap 3 msgs / 30 days | _pending_ |

See `00_CHAIR_ROADMAP.md` for full rationale, options, and the phased plan.
