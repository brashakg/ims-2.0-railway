"""
POLICY rows: /vendor-portal, /vendor-returns, /vendor-rma, /rtv-debit-notes, /vendors.

Moved VERBATIM from the flat ``api/services/rbac_policy.py`` lines 6608-7108.
These rows are load-bearing DATA. Their ORDER is preserved (``policy_for``
prefers the most specific match, and a row under a module widens that
module's capability grant-union). Do not reorder, reword or re-group them;
a new route needs a new row here or CI fails the coverage lock.
"""

from __future__ import annotations

from typing import Dict, List

ROWS: List[Dict[str, object]] = [
    # --- /api/v1/vendor-portal ---
    {
        "method": "GET",
        "path": "/api/v1/vendor-portal/{token_id}/jobs",
        "allowed": "PUBLIC",
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-portal/{token_id}/jobs/{job_id}",
        "allowed": "PUBLIC",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-portal/{token_id}/jobs/{job_id}/status",
        "allowed": "PUBLIC",
    },
    # --- /api/v1/vendor-returns ---
    {"method": "GET", "path": "/api/v1/vendor-returns", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/vendor-returns", "allowed": "AUTHENTICATED"},
    {"method": "GET", "path": "/api/v1/vendor-returns/", "allowed": "AUTHENTICATED"},
    {"method": "POST", "path": "/api/v1/vendor-returns/", "allowed": "AUTHENTICATED"},
    {
        "method": "GET",
        "path": "/api/v1/vendor-returns/{return_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/vendor-returns/{return_id}/status",
        "allowed": "AUTHENTICATED",
    },
    # --- /api/v1/vendor-rma (N4 Vendor RMA + credit-note reconciliation) ---
    # An RMA + its vendor credit note are financial instruments against a
    # vendor; create + every lifecycle transition is gated to the same vendor/AP
    # role set vendor_returns hardened to (SUPERADMIN implicit via require_roles).
    # GET list/detail are AUTHENTICATED but store-scoped per object in the
    # handler (validate_store_access / resolve_store_scope), so a cashier can
    # read but never authorize an RMA or record a credit.
    {
        "method": "GET",
        "path": "/api/v1/vendor-rma",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-rma/",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/vendor-rma/{rma_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/authorize",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/dispatch",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/credit-note",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/reject",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/vendor-rma/{rma_id}/close",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/rtv-debit-notes (F20 GST debit note ON TOP of an RTV) ---
    # The GST-compliant debit-note DOCUMENT issued to a vendor when goods are
    # returned. Issuing + Tally export are gated to the same vendor/AP role set
    # vendor_returns / vendor_rma use (a cashier can NEVER issue a debit note).
    # GET list/detail/print are AUTHENTICATED but store-scoped per object in the
    # handler (validate_store_access / resolve_store_scope).
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes/",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "POST",
        "path": "/api/v1/rtv-debit-notes/issue",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes/{debit_note_id}",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes/{debit_note_id}/print",
        "allowed": "AUTHENTICATED",
        "store_scoped": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/rtv-debit-notes/{debit_note_id}/tally",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        "store_scoped": True,
    },
    # --- /api/v1/vendors ---
    {"method": "GET", "path": "/api/v1/vendors", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/vendors",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {"method": "GET", "path": "/api/v1/vendors/", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/vendors/",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/ap-aging",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {"method": "GET", "path": "/api/v1/vendors/grn", "allowed": "AUTHENTICATED"},
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # Procurement Phase 2: one-shot express receive for a CLEAN delivery
    # (create + accept + invoice-draft preview + accountant task, server-side).
    # Same gate as creating/accepting a GRN -- ALL receiving roles (owner
    # decision); every receiving control (attachment gate, store boundary,
    # PO receivable) is enforced inside via the shared create/accept impls.
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn/express",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/grn/{grn_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn/{grn_id}/accept",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn/{grn_id}/void",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn/{grn_id}/escalate",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # P1/S2: vendor-first goods-receipt cockpit (open POs + worklists for the
    # receiving screen). Same gate as receiving -- the receiving roles.
    {
        "method": "GET",
        "path": "/api/v1/vendors/goods-receipt/cockpit",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # P1/S3: the ops user uploads the mandatory goods-receipt document (vendor
    # invoice/challan) here BEFORE creating the GRN. Same gate as creating the
    # GRN itself -- the receiving roles.
    {
        "method": "POST",
        "path": "/api/v1/vendors/grn/upload-doc",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # P1/S3: stream the attached goods-receipt document (accountant recon links
    # here). Store-scoped object access inside the handler; the role gate is the
    # receiving + accounting roles.
    {
        "method": "GET",
        "path": "/api/v1/vendors/grn/{grn_id}/document",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # Purchase Invoices (first-class AP+ITC; books the payable + ITC ledger).
    # Create/from-grn/book AND reads are accounting actions -> ACCOUNTANT/ADMIN.
    # (SUPERADMIN auto-passes via require_roles.) F1: reads expose supplier bill /
    # AP / GST-ITC / 3-way-match data, so they are no longer AUTHENTICATED.
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/from-grn/{grn_id}",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # F9: consolidate N Delivery Challans into a draft bulk invoice (accounting
    # action -> ACCOUNTANT/ADMIN, same gate as from-grn).
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/from-dcs",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # F9: stored DC bulk-tally detail (accounting read -> ACCOUNTANT/ADMIN).
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/dc-match",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # Phase 2: 3-way-match config + per-invoice match detail + exception override.
    # F1: config read (accounting policy) + match-detail read are now ACCOUNTANT/
    # ADMIN, same as the config write + exception override (SUPERADMIN auto-passes).
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/config",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "PUT",
        "path": "/api/v1/vendors/purchase-invoices/config",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/match",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # Ruling 15: the invoice gate refuses an incomplete product, and the
    # accountant holds no products:write. This raises the cataloguing task for
    # them -- same accounting gate as the rest of the bill screen.
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/request-cataloguing",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/approve-exception",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # F19: landed-cost capture / preview / one-way allocation. All three are
    # accounting actions on the bill's cost basis -> ACCOUNTANT/ADMIN (same
    # gate as from-grn / dc-match; SUPERADMIN auto-passes via require_roles).
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/landed-costs",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/landed-costs/preview",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/allocate-landed-costs",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # S6: Accountant reconciliation ticks (inline recon sub-doc on vendor_bills).
    # Both write and read are accounting actions -> ACCOUNTANT/ADMIN.
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/recon",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-invoices/{invoice_id}/recon",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # S6: Accountant console worklists (stock-yet-to-receive, vendor returns,
    # pending scheme + return CNs). ACCOUNTANT/ADMIN read-only.
    {
        "method": "GET",
        "path": "/api/v1/vendors/recon/worklists",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    # P4: tick a scheme/rebate credit note as physically received (clears it from
    # the pending-scheme-CN worklist). ACCOUNTANT/ADMIN.
    {
        "method": "POST",
        "path": "/api/v1/vendors/recon/credit-notes/{credit_note_number}/mark-received",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-orders",
        "allowed": "AUTHENTICATED",
    },
    # Last-paid price lookup for the PO / Buy-Desk form (vendor roles; the
    # endpoint additionally store-scopes each PO it reads).
    {
        "method": "GET",
        "path": "/api/v1/vendors/last-cost",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders/from-forecast",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-orders/{po_id}",
        "allowed": "AUTHENTICATED",
    },
    # PO lifecycle timeline (read-only; the endpoint store-scopes the PO like
    # get_po). Any authenticated user, same as reading the PO itself.
    {
        "method": "GET",
        "path": "/api/v1/vendors/purchase-orders/{po_id}/timeline",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders/{po_id}/cancel",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders/{po_id}/send",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # F8 PO-vs-GRN variance: dismiss a variance/backorder line with a mandatory
    # justification (single-doc PO $push + one audit row). An accounting-style
    # decision -> ACCOUNTANT/ADMIN only (SUPERADMIN auto-passes).
    {
        "method": "POST",
        "path": "/api/v1/vendors/purchase-orders/{po_id}/dismiss-variance",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/sku-alias-lookup",
        "allowed": "AUTHENTICATED",
    },
    # F8 PO-vs-GRN variance report (read-only). Open/partial PO lines whose
    # received qty trails the order, with open qty + aging enum. Visible to the
    # AP pair plus the managers who chase late deliveries.
    {
        "method": "GET",
        "path": "/api/v1/vendors/variance-report",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "PUT",
        "path": "/api/v1/vendors/{vendor_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/bills",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/bills",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/debit-notes",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/debit-notes",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/ledger",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/payments",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/payments",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/portal-token",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/vendors/{vendor_id}/portal-token/{token_id}",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/portal-tokens",
        "allowed": ["ADMIN", "SUPERADMIN"],
    },
    # INV-13: vendor performance scoring + purchase-history analytics
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/performance",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/purchase-history",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/{vendor_id}/sku-aliases",
        "allowed": "AUTHENTICATED",
    },
    {
        "method": "POST",
        "path": "/api/v1/vendors/{vendor_id}/sku-aliases",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    {
        "method": "DELETE",
        "path": "/api/v1/vendors/{vendor_id}/sku-aliases/{alias_id}",
        "allowed": ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    },
    # FIN-11: TDS threshold status + quarterly 26Q/27EQ export
    {
        "method": "GET",
        "path": "/api/v1/vendors/tds/threshold-status",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
    {
        "method": "GET",
        "path": "/api/v1/vendors/tds/26q-export",
        "allowed": ["ACCOUNTANT", "ADMIN"],
    },
]
