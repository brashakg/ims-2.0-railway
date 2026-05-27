# Print template content overrides (v2-3)

The 6 in-use print templates carry per-entity content overrides so the owner
can edit signatory names, declarations, drug licence numbers, NCAHP UID, etc.
without a code change. Defaults are sensible CGST/NCAHP-compliant placeholders;
overrides win where set.

## Editable templates

| Template key      | Doc                  | Header style | Notes |
|-------------------|----------------------|--------------|-------|
| `tax_invoice`     | Tax Invoice (A4)     | `LegalHeader`  | Rule 46 CGST. Customer-facing. |
| `thermal_receipt` | Thermal Receipt (80mm) | Compact statutory | Rule 46 CGST, compact for thermal. |
| `rx_card`         | Prescription Card (A5) | `LegalHeader` | NCAHP Act 2021. Adds NCAHP UID + DMC reg. |
| `job_card`        | Lens Job Card (A5)   | `StaffHeader` | Internal. No GSTIN/CIN on the printed doc. |
| `grn`             | Goods Receipt Note (A4) | `LegalHeader` | Vendor-facing. Retain per Rule 56. |
| `z_report`        | Day-end Z-Report (A4) | `StaffHeader` | Internal SOP-FIN-02 reconciliation. |

The other ~20 print templates (Purchase Order, Delivery Challan, Credit Note,
etc.) are **not** wired to the editor in this slice — they continue to render
their pre-existing layouts. The shared helpers (`buildLegalHeader`,
`buildStaffHeader`, `amountInWords`, `hsnTaxSummary`) are in place so adding
them later is mechanical.

## Editable fields

All fields are optional. Leave a field blank to fall back to the default. To
return a saved override to defaults entirely, delete the override row from
the editor (Revert button).

| Field                    | Default                                              | Templates that use it |
|--------------------------|------------------------------------------------------|-----------------------|
| `header_subtitle`        | empty                                                | all (sits under the entity name) |
| `declaration_text`       | per-doc canonical CGST text (see below)              | `tax_invoice`, `thermal_receipt`, `rx_card`, `grn`, `z_report` |
| `signatory_name`         | empty                                                | all customer-facing + GRN + Z-report |
| `signatory_designation`  | `Authorised Signatory`                               | all customer-facing + GRN |
| `drug_licence_no`        | empty                                                | all (D&C Rules — stores dispensing contact lenses) |
| `ncahp_uid`              | empty                                                | `rx_card` only (mandatory since 2024) |
| `dmc_reg`                | empty                                                | `rx_card` only (State Medical Council reg) |
| `footer_terms`           | empty                                                | all (free-text payment / warranty notes) |
| `logo_url`               | entity's logo                                        | all |
| `retention_years`        | `7` per CGST Rule 56                                 | all |
| `reverse_charge_default` | `false`                                              | `tax_invoice`, `grn` (rarely true) |

## Default declaration text (overrides allowed)

- **Tax Invoice**: "We declare that this invoice shows the actual price of the
  goods described and that all particulars are true and correct."
- **Thermal Receipt**: "Thank you for your purchase. Goods once sold are
  governed by our return policy displayed in-store."
- **GRN**: "Goods inspected and received in good condition unless variance /
  remarks recorded against any line."
- **Rx Card**: "This prescription is valid for use with any registered
  optician. The practitioner has not received any consideration for prescribing
  a particular brand of frame, lens, or contact lens."
- **Z-Report**: "Day-end totals are verified against physical cash and tender
  splits. Variances over the policy threshold require manager sign-off."

The editor surfaces a **warning banner** on the `declaration_text` field
because changing this text is a statutory compliance change with the same risk
profile as editing the HSN/GST master — review the wording with the
accountant before saving.

## Default statutory footer (not editable)

The footer line that names the rule reference + retention period is generated
from the doc type and the (editable) `retention_years` field. Examples:

- Tax Invoice: `Issued under Sec. 31 CGST Act 2017 r/w Rule 46. Retain for 7
  years per CGST Rule 56.`
- GRN: `Goods Receipt Note - internal control document. Retain for 7 years per
  CGST Rule 56.`
- Z-Report: `Day-end cash reconciliation (SOP-FIN-02). Retain for 7 years per
  CGST Rule 56.`

## Where the data lives

Mongo collection `print_template_overrides`. Shape:

```json
{
  "override_id": "uuid",
  "entity_id": "ent-bv-001",
  "template_key": "tax_invoice",
  "fields": {
    "signatory_name": "...",
    "signatory_designation": "Director",
    "declaration_text": "...",
    ...
  },
  "created_at": "...",
  "updated_at": "...",
  "created_by": "username",
  "updated_by": "username"
}
```

Unique on `(entity_id, template_key)` — a single editable row per template per
entity.

## API

| Method | Path                                            | Role    | Purpose |
|--------|-------------------------------------------------|---------|---------|
| GET    | `/api/v1/print-overrides?entity_id=...`         | any auth | list all overrides for an entity |
| GET    | `/api/v1/print-overrides/{entity_id}/{template_key}` | any auth | fetch single (empty envelope when missing) |
| PUT    | `/api/v1/print-overrides/{entity_id}/{template_key}` | SUPERADMIN/ADMIN | upsert |
| DELETE | `/api/v1/print-overrides/{entity_id}/{template_key}` | SUPERADMIN/ADMIN | revert to defaults |
| GET    | `/api/v1/print-overrides/_meta/templates`       | any auth | template + field catalog (for the editor UI) |

Reads are open to any authenticated user because the production print
renderer needs to resolve overrides without elevating the user's role.
Writes are ADMIN-tier — entity-level content edits fan out across every
print of that template billed under the entity, same blast radius as HSN/GST
master edits.

## How the renderer resolves overrides

`backend/api/services/print_legal.py::LegalHeader` and `StaffHeader` accept
an optional `overrides` dict. The frontend twin in
`frontend/src/components/print/legalPrimitives.tsx::buildLegalHeader` and
`buildStaffHeader` does the same. Resolution order:

1. Caller-supplied `overrides` dict (typically loaded from the API at render
   time, keyed on `entity_id` + `template_key`).
2. Sensible CGST/NCAHP defaults (e.g. `signatory_designation` = "Authorised
   Signatory", `retention_years` = 7).

Empty strings are **ignored** — they don't override defaults. To blank a
field, the owner clicks the Revert button (which deletes the override row
and falls back to defaults).

## Where it's NOT applied

These intentionally do not consume the editor's overrides yet:

- **Backend HTML / PDF renderers** for invoices (PR scope is the React
  templates; the backend HTML render path can be wired in a future PR by
  importing `print_legal.LegalHeader(..., overrides=fetch_overrides(entity_id,
  template_key))`).
- **The other ~20 print templates** beyond the 6 in-use (PO, Delivery Challan,
  Credit/Debit Note, etc.). Their layouts still render from code. Wiring is
  mechanical when needed.

## Why this design

- **Entity-scope, not store-scope.** A store can carry orders billed under
  either of the two live entities (Better Vision Pvt Ltd, WizOpt). The
  signatory + drug licence + NCAHP UID belong to the entity, not the outlet.
- **No mock identities.** Defaults are CGST-canonical text — never real names
  or numbers from the design system. The owner edits real values via the UI.
- **Statutory aesthetic on prints, BV brand only in the editor.** Per the
  council session: customer-facing statutory docs soften brand and dial up
  trust. The editor drawer uses BV red because it's an admin UI, but the
  preview pane (and the production prints) stay strictly ink-on-white,
  bordered, ALL-CAPS, sans-only.
