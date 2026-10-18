# Product-import retirement for Claude review

Status: local implementation and verification complete; deliver together with
the preceding [A05 catalogue identity correction](CODEX-A05.md) in one draft PR
to main. Claude reviews and merges. No deployment or live-data work performed.

## Owner-approved boundary

Avinash confirmed: **"Only remove the import products part, if there is any
customer prescription or order import part etc let it stay"**, then clarified
**"Remove product imports as the one exception; leave every other screen and
workflow unchanged"**. Catalogue optimization is suggestions only.

The only visual change is removal of the existing Inventory > Stock **CSV
import** button, modal, file reader and API client. The stock table, filters,
export, manual Add product/editor, barcode controls, and all other screen
layouts remain unchanged. No cataloguing form fields or staff sequence changed.

## Removed product-only entry points

| Method | Path | Former purpose |
| --- | --- | --- |
| POST | `/api/v1/products/bulk-create` | CSV batch product creation |
| POST | `/api/v1/catalog/products/import` | Stage catalogue products in bulk |
| POST | `/api/v1/catalog-import/preview` | Parse vendor CSV/XLSX/PDF price list |
| POST | `/api/v1/catalog-import/commit` | Create/link imported product rows |
| POST | `/api/v1/admin/products/bulk-import` | Upload a product-import file |
| GET | `/api/v1/admin/products/bulk-import/{job_id}/file` | Download an import job file |

Those six routes, their policy rows, product-only schemas/helpers, and the
unused price-list router/parser were removed. No queued product-import worker
was found; the admin endpoint only stored a file/job record and instructed the
caller to submit rows separately. Historical stored files/jobs were not deleted.

`POST /api/v1/admin/techcherry/import` remains, but its request type accepts
**customers or orders only**. A request with type `products` returns 422 before
the handler opens a database. Product mapper/GTIN writer branches are removed;
customer and order mapper/upsert behavior and the SUPERADMIN gate remain.
The status endpoint now reports its supported customer/order collections.

## Preserved functionality and data

- Customer imports, customer deduplication, order imports/reimports, embedded
  prescription powers and their existing data-quality reporting.
- Prescription features and uploads, order history ingestion, and Shopify order
  processing; none of those application files changed.
- Opening-stock preview/commit and lens-stock matrix import: they add quantities
  for **existing** product identities/lens lines, not new catalogue products.
- GST and bank-statement imports, salary uploads, supplier bill/GRN attachments,
  task attachments, product photographs, and ordinary data exports.
- Previously imported product records and files; their read, review, edit,
  promotion and GTIN-ownership rules remain. No database migration/deletion.
- Manual product creation, templates, clones/variants, and SKU/GTIN guards.

## Verification

- **323 backend tests passed** in the selected retirement, TechCherry,
  catalogue identity, legacy review/promotion, owner-ruling, SKU, canonical
  create and RBAC suites (127.16 seconds).
- **16 catalogue-list cache tests passed** after removing only the obsolete
  product-import cache case; manual creation, edits and existing stock/cache
  behavior remain covered (137.48 seconds).
- New retirement tests prove the six routes are absent/reject requests, the
  TechCherry product request cannot touch the DB, customers/orders still
  insert/reimport/skip, embedded Rx survives, an existing product remains
  unchanged, and manual/operational import routes stay registered.
- Tests specific only to removed importers were removed. Existing legacy
  product review/promotion tests now seed historical records directly; shared
  identity-normalization assertions remain through the canonical service.
- CI-configured errors/fatal-only Pylint passed on every changed application
  file, including `main.py` and the policy tables. An initial stricter local
  command reported Pydantic FieldInfo false positives; rerunning the exact
  repository CI flags passed (no new suppression added to source).
- Backend import smoke: **1,314 routes**, six fewer than the 1,320 baseline.
- Modified frontend: forced TypeScript build and Vite production build passed.
  Vite retained the existing stylesheet comment warning around `text-*/border-*`;
  no stylesheet was edited. No live browser transaction was performed.

All backend execution used test mode and the isolated
`127.0.0.1:27099/ims_codex_product_imports` endpoint; database behavior in the
regressions used mongomock. No production MongoDB, Railway, Vercel, Shopify,
customer notification or live store transaction was used. The original
`C:/ims-final-audit` checkout was not modified.

## Suggested improvements only

An immediate single-flight guard shared by touch and keyboard saves could
prevent duplicate in-flight requests without changing the form. The existing
form currently disables buttons through React state after the handler starts;
review `quickadd/useQuickAddForm.ts` and `quickadd/SaveBar.tsx` before deciding.
This suggestion was **not implemented**.

Save + New, variant streaks, templates, live missing-field jump chips, category
auto-focus and a cached category registry already exist. Do not propose these
as missing features or change their layout without a new owner instruction.
