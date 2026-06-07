# Feature #50: Clinical-to-Retail Digital Handover
META: effort=M days=5 risk=LOW roi=4 quickwin=yes deps=none phase=3

## Existing overlap
IMS already has the full substrate for this feature:

1. **handoffs.py** (`backend/api/routers/handoffs.py`) — generic file-routing system (title, description, file blob, recipient assignment, responses: approved/denied/accepted, dismiss/keep/snooze, TTL + GridFS cleanup). This is architecturally identical to what Clinical Handover needs; it just lacks clinical-specific fields and a dedicated trigger.

2. **Prescriptions** (`backend/api/routers/prescriptions.py`) — `PrescriptionCreate`, auto-minted on `complete_test()` in `clinical.py:847-935`. The prescription_id is available immediately after eye-test completion.

3. **In-app bell** (`backend/api/routers/notifications.py`) — `notifications` collection, per-user, with unread badge poll, snooze, mark-read. TASKMASTER and workshop already use this to ping staff. Handover notification can reuse this exact path.

4. **WhatsApp dispatch** (`backend/agents/providers.py`, `backend/api/services/notification_service.py`) — MEGAPHONE-ready send path; DISPATCH_MODE gated; quiet-hours aware.

5. **RBAC** (`backend/api/services/rbac_policy.py`) — OPTOMETRIST, SALES_STAFF, SALES_CASHIER, STORE_MANAGER roles all defined. Store-scoping (`validate_store_access`) already enforced.

6. **POS prescription pick** (`frontend/src/pages/pos/PrescriptionSelectModal`) — POS already has a two-step patient → Rx pick modal (shipped in PR #363). The handover payload can pre-populate this.

7. **Clinical findings + SOAP notes** (`clinical.py:104-250`) — structured clinical data (VA, IOP, diagnosis codes) is already stored on the eye test; the handover can include or exclude this.

**Assessment:** This is not greenfield. It is a thin clinical-context layer on top of the existing handoffs.py + notifications.py + prescriptions pipeline. No new architectural pattern is needed.

## Reuse (extend, don't rebuild)
- **`backend/api/routers/handoffs.py`** — extend `HandoffCreate` to accept `prescription_id`, `eye_test_id`, `product_recommendations[]`, `clinical_summary` (optional). Add a new `handoff_type='CLINICAL_RX'` discriminator. All existing routing, TTL, dismiss/keep/snooze, reshare logic reused as-is.
- **`backend/api/routers/clinical.py`** — add one new endpoint `POST /clinical/tests/{test_id}/send-to-floor` that mints the handoff doc (pulls the completed prescription, builds the payload, resolves eligible recipients for the store, writes to handoffs collection, fires in-app notification).
- **`backend/api/routers/notifications.py`** — reuse `notifications` collection write for the in-app bell ping; no schema change needed.
- **`backend/database/repositories/handoff_repository.py`** — reuse `find_inbox_for_user()` with an added `handoff_type` filter for the new clinical inbox view.
- **`frontend/src/pages/pos/PrescriptionSelectModal`** — extend to show a "New from doctor" badge when an unacknowledged CLINICAL_RX handoff exists for the current customer+patient.
- **`frontend/src/components/shell/`** — the topbar notification bell already polls unread count; no change needed for the ping to appear.

## Data model
No new collection. Add these fields to existing handoff docs (backwards-compatible, all optional):

```
handoffs collection — new fields on CLINICAL_RX type:
  handoff_type: str          # 'CLINICAL_RX' | 'GENERAL' (default GENERAL, preserves existing docs)
  prescription_id: str       # links to prescriptions collection
  eye_test_id: str           # links to eye_tests collection
  customer_id: str           # for POS pre-population
  patient_id: str            # which family member
  product_recommendations: [ # doctor's product suggestions (free-form, optometrist-entered)
    { category: str,         # e.g. 'Progressive Lens', 'Anti-Reflection Coating', 'Photochromic'
      brand_preference: str, # optional, e.g. 'Zeiss', 'Essilor'
      notes: str }           # free text
  ]
  clinical_summary: str      # optional brief note for sales (not the full Rx; Rx is fetched via prescription_id)
  acknowledged_by: str       # user_id of first sales staff to open it
  acknowledged_at: datetime
```

No new indexes needed; `handoffs` already has TTL on `expires_at` and is queried by recipient `user_id`.

## Backend

**`POST /api/v1/clinical/tests/{test_id}/send-to-floor`** (OPTOMETRIST, STORE_MANAGER, ADMIN, SUPERADMIN)
- Validates test status is COMPLETED and prescription exists (fetch by eye_test_id from prescriptions collection).
- Reads product_recommendations[] and clinical_summary from request body.
- Resolves recipient list: all active SALES_STAFF, SALES_CASHIER, STORE_MANAGER at the same store_id as the test (query users collection by role + store_ids contains test.store_id).
- Writes one handoff doc (handoff_type='CLINICAL_RX', prescription_id, eye_test_id, customer_id, patient_id, recipients[], TTL = owner-configured default, e.g. 4 hours).
- Writes one `notifications` bell entry per recipient (message: "New Rx ready for {patient_name} — {optometrist_name}").
- Optionally sends WhatsApp to store manager if DISPATCH_MODE=live and owner enables it (feature-flagged off by default).
- Returns handoff_id. Idempotent: if a CLINICAL_RX handoff for same (test_id) already exists and is within TTL, returns existing handoff_id (prevents duplicate sends on accidental double-tap).
- Audit: writes to `audit_logs` (action='CLINICAL_HANDOVER_SENT', entity_type='eye_test', entity_id=test_id).

**`PATCH /api/v1/handoffs/{handoff_id}/acknowledge`** (SALES_STAFF, SALES_CASHIER, STORE_MANAGER)
- Sets `acknowledged_by` + `acknowledged_at` on the handoff doc.
- Updates the recipient's status to 'accepted' (reuses existing recipient status field).
- Returns 200. Called by the frontend when sales staff taps "Open" on the handover card.

**`GET /api/v1/handoffs/clinical-inbox`** (SALES_STAFF, SALES_CASHIER, STORE_MANAGER)
- Thin wrapper over existing `find_inbox_for_user()` filtered to `handoff_type='CLINICAL_RX'`, not dismissed, not expired, store-scoped.
- Returns handoff list enriched with prescription summary (sph/cyl/axis/add per eye, expiry_date, lens_recommendation) fetched from prescriptions collection.
- No heavy join needed: prescription_id is on the handoff doc; one secondary read.

No changes to existing `/handoffs` endpoints — all existing file-handoff flows continue unchanged.

## Frontend

**Clinical side — extend `frontend/src/pages/clinical/ClinicalPage.tsx`**
- After test completion, show a "Send to Floor" button (only when test.status == 'COMPLETED' and a prescription exists).
- Tapping opens a lightweight drawer (restrained light-only, neutral palette):
  - Read-only Rx summary (sph/cyl/axis/add per eye, lens_recommendation from the minted prescription).
  - Product recommendations section: add up to 5 rows (category dropdown: Progressive / SV / Bifocal / AR Coating / Photochromic / Contact Lens / Sunglasses / Frame; brand text field; notes text field). Pre-populated from lens_recommendation on the prescription if present.
  - Clinical summary: single-line optional note for sales (e.g. "patient prefers lightweight frames").
  - "Send to Floor" confirm button — calls POST send-to-floor, shows success toast, disables button (idempotency).

**Retail side — new `frontend/src/components/handoffs/ClinicalHandoverCard.tsx`**
- Displayed in two places:
  1. **Hub / dashboard inbox** (extend existing handoffs inbox if one exists, or add a "Clinical Handovers" section to the Hub page). Shows patient name, optometrist name, time since sent, unacknowledged badge. Tapping expands to show full Rx + recommendations.
  2. **POS flow** — extend `PrescriptionSelectModal` to show a "From doctor today" highlighted entry at the top of the list when a CLINICAL_RX handoff exists for the selected customer (match on customer_id + patient_id, not expired, not dismissed). Badge: "Just sent by Dr. {name}".
- Card layout (restrained): white background, single left-accent border in `bv-red` only for the unacknowledged state. Shows: patient name + relation, Rx line ("R: -2.50 / -0.75 × 90  L: -2.00 sph"), lens recommendation, product recommendations as pill tags, clinical summary in muted text. "Acknowledge" button removes the accent border and marks acknowledged.

**Notification bell** — no change; the existing bell already surfaces the notification written by the backend.

## Business rules
- Handover TTL is fixed at 8 hours from creation (same working day). After TTL, the handoff expires via existing Mongo TTL index on `expires_at`. This is a business decision (see Owner Decisions).
- A handover can only be sent for a test with status = COMPLETED and a minted prescription. Attempting to send for an in-progress test → 422.
- Idempotency: one active (non-expired) CLINICAL_RX handoff per (test_id). If optometrist taps Send again within TTL, returns the existing handoff (no duplicate notifications).
- The Rx content in the handover is read-only (fetched live from prescriptions collection). The handover does NOT copy Rx data; it links by prescription_id. If the prescription is later edited (redone), the handover reflects the current version.
- product_recommendations are free-form; no catalog validation (optometrist may suggest brands not stocked). Sales staff sees them as advisory, not as cart items.
- clinical_summary must not contain PII beyond what the patient has already shared with the store (enforced by instruction, not code validation — this is an internal staff tool).
- Audit trail: every send-to-floor and acknowledge action is written to `audit_logs` (immutable).
- No financial transaction, no stock reservation, no order creation. Pure information handover. Zero accounting risk.

## RBAC
| Action | Roles |
|---|---|
| Send to floor (POST send-to-floor) | OPTOMETRIST, STORE_MANAGER, ADMIN, SUPERADMIN |
| View clinical inbox (GET clinical-inbox) | SALES_STAFF, SALES_CASHIER, STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN |
| Acknowledge handover | SALES_STAFF, SALES_CASHIER, STORE_MANAGER |
| Dismiss / snooze handover | SALES_STAFF, SALES_CASHIER, STORE_MANAGER (reuses existing handoff dismiss logic) |
| Reshare handover | STORE_MANAGER, ADMIN, SUPERADMIN |
| View in POS prescription picker | SALES_STAFF, SALES_CASHIER, STORE_MANAGER |
| ACCOUNTANT, WORKSHOP_STAFF, CASHIER | no access to clinical inbox |

Store-scoping is enforced: recipients are resolved to the same store_id as the eye test; a sales associate at Store B cannot see handovers from Store A.

## Integrations
- **In-app bell** (existing `notifications` collection) — primary channel. No new integration.
- **MSG91 WhatsApp** (optional, feature-flagged off by default) — store manager can be pinged on WhatsApp if owner enables it in Settings → Notifications. Reuses existing MEGAPHONE/send_notification path with DISPATCH_MODE gating. Message: "New Rx ready for {patient_name} at {store_name}. Open IMS to view."
- **No Shopify, Razorpay, Tally, or ONDC involvement.** Pure internal workflow.
- **JARVIS / ORACLE** — no agent involvement. This is a synchronous staff workflow, not an AI analysis task.

## Risk notes
- **No POS/money/accounting risk.** This feature does not touch orders, payments, stock, or any financial collection. Feature flag is not needed for safety — it is safe to ship without one.
- **IDOR risk on prescription_id**: the send-to-floor endpoint must validate that the test belongs to the calling user's store before building the handoff. Reuse existing `validate_store_access` pattern (already applied in prescriptions.py). Missing this check would allow an optometrist at Store A to expose another store's patient data.
- **TTL sizing**: if TTL is too short (e.g., 2 hours), a busy day means sales staff miss the handover. If too long, stale handovers clutter the inbox. Default 8 hours covers a full shift; configurable by owner.
- **Rx version drift**: if an optometrist redoes a prescription after sending the handover, the handover's prescription_id now points to the updated Rx. This is correct behavior (live link), but sales staff should be informed. Mitigation: show "Updated" badge if prescription.updated_at > handoff.created_at. Low complexity addition.
- **Grid FS blob cleanup**: the existing handoffs TTL + NEXUS hourly sweep handles file blob cleanup. CLINICAL_RX handoffs carry no file blob (prescription is a DB record, not a file), so there is nothing to clean up beyond the TTL expiry of the handoff doc itself.
- **Scale**: a busy 6-store day might generate 30-50 handovers. This is negligible load on the handoffs collection.

## Recommendation
**Build now — quick win.** 5 days, zero accounting risk, reuses 80% of existing handoffs + notifications infrastructure, directly addresses a conversion-boosting gap (sales staff walk in blind today). The ROI is immediate: salesperson knows the Rx and the doctor's product preference before saying hello to the patient, which shortens the consultation and increases attachment rate (lens upgrades, coatings).

## Owner decisions
- Q: What should the handover TTL be — how many hours before a "sent to floor" notification expires? | Why: Drives how long sales staff see the handover in their inbox; too short means missed on a busy day, too long clutters the next day's inbox. | Options: 4 hours (same half-day) / 8 hours (full shift, recommended default) / 24 hours (until next morning)

- Q: Should the store manager receive a WhatsApp ping when a handover is sent, or only an in-app bell? | Why: WhatsApp requires MSG91 to be live (DISPATCH_MODE=live) and adds a real send cost per handover; in-app bell is free and instant. | Options: In-app only (safe default, zero cost) / WhatsApp to manager only (adds cost, ensures manager is looped in even if not at desk) / WhatsApp to all sales staff (noisiest, highest cost)

- Q: Should product recommendations from the optometrist be free-text only, or should they be constrained to your actual lens catalog brands (Zeiss, Essilor, Hoya, etc.)? | Why: Catalog-constrained recommendations allow the system to later match to a stock SKU and show availability; free-text is faster to implement and requires no catalog maintenance. | Options: Free-text (ship now) / Catalog-constrained (richer but requires lens catalog to be complete first)

- Q: Do you want the sales associate to be able to mark a handover "completed" after the sale — i.e., confirm "I served this patient"? | Why: Adds a conversion-tracking loop (handovers sent vs handovers acted on vs orders created), but requires the frontend to link the handover to the resulting order_id. | Options: No tracking (simpler, ship faster) / Manual "mark served" by sales staff (lightweight) / Auto-link to order on checkout (richest, adds 1-2 days of work)

- Q: Should optometrists at all 6 stores get this feature at launch, or roll out to 1-2 pilot stores first? | Why: A pilot limits blast radius if staff workflow friction is discovered; full rollout maximizes conversion impact immediately. | Options: All stores on day 1 / Pilot 1-2 stores for 2 weeks first