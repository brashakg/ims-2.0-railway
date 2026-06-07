"""Orchestrator resolution for E1 T13/item-6/DoD-5: cancel unified money_accounts SoR;
money_guard = service over per-type single-doc collections. ASCII-only."""
import os
ROOT = r"c:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway-1\docs\roadmap"

RES = """> ## [ORCHESTRATOR RESOLUTION 2026-06-07] T13 / item-6 / DoD-5 -- CANCELLED (not deferred)
> The unified `money_accounts` system-of-record + cross-collection dual-write (packet Delta item-6,
> test T13, DoD-5) is **CANCELLED**. `money_guard` is a SERVICE that operates per account-type on
> that type's OWN collection via single-document `find_one_and_update`. NO unified SoR, NO dual-write,
> NO replica set, NO transactions -- ever.
> - Existing types (LOYALTY / STORE_CREDIT / GIFT_VOUCHER) use their existing collections via the
>   Phase-A shims (SHIPPED in PR #563).
> - New types are added by their owning feature, each as its OWN dedicated single-doc collection:
>   PETTY_CASH -> `petty_cash_floats` (#17) ; FAMILY_WALLET -> `family_wallets` (#49) ;
>   CONSIGNMENT -> `consignment_accounts` (#3). Until then they correctly return reason="unavailable".
> - E1 Phase-A Definition of Done = the 3 existing types behaviour-preserving (DONE). The test session
>   must NOT bounce E1 for missing T13/item-6/DoD-5 -- they are cancelled.
> - A cross-type unified ledger VIEW, if ever needed, is a read-side aggregation (no transactions).

---

"""

# 1) prepend resolution to the E1 packet
e1 = os.path.join(ROOT, "features", "E1.md")
with open(e1, "r", encoding="utf-8") as f:
    body = f.read()
if "ORCHESTRATOR RESOLUTION 2026-06-07" not in body[:1200]:
    with open(e1, "w", encoding="utf-8") as f:
        f.write(RES + body)
    print("E1.md: resolution prepended")
else:
    print("E1.md: already has resolution")

# 2) append a resolution section to CORRECTIONS.md
corr = os.path.join(ROOT, "CORRECTIONS.md")
SECTION = """

---

## R1 (resolution) -- money_guard = per-type single-doc collections (closes P0-1 / E1 T13)

Orchestrator call 2026-06-07 after E1 (PR #563): the unified `money_accounts` SoR is **CANCELLED**,
not deferred. `money_guard` operates per account-type on that type's own collection via single-document
`find_one_and_update` (no transactions, no replica set, ever). New balance types are added by their
owning feature as a dedicated single-doc collection and registered in `money_guard` ACCOUNT_TYPES:
- **#17 petty cash** -> `petty_cash_floats` (one doc per store).
- **#49 family wallet** -> `family_wallets` (one doc per household; pool redeem still OTP-gated to the primary member).
- **#3 consignment** -> `consignment_accounts` (one doc per vendor/agreement).
When you build #17 / #49 / #3: add the collection + register the type in money_guard; do NOT build a
unified SoR or any cross-collection write. This supersedes P0-1's "Phase B/C DO-NOT-BUILD until replica
set" -- there is no Phase B/C to build.
"""
with open(corr, "r", encoding="utf-8") as f:
    cbody = f.read()
if "R1 (resolution)" not in cbody:
    with open(corr, "a", encoding="utf-8") as f:
        f.write(SECTION)
    print("CORRECTIONS.md: R1 appended")
else:
    print("CORRECTIONS.md: already has R1")
