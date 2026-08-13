"""
IMS 2.0 - Shopify DELETE-payload shape guard  (ONE classifier, both handlers)
=============================================================================
THE single answer to "may this payload be acted on as a Shopify *delete*?",
shared by shopify_order_delete (which VOIDs an IMS order) and
shopify_customer_delete (which flags an IMS customer for data erasure).

WHY THIS EXISTS
---------------
The Shopify topic is read from the UNSIGNED X-Shopify-Topic header, while the
body is what the HMAC signs. So a captured, validly-signed `orders/create` body
-- whose top-level `id` IS a real, live order id -- can be re-delivered with
`X-Shopify-Topic: orders/delete` and will route straight into the delete
handler, which voids that order. Same shape for customers/create ->
customers/delete, which flags a real buyer for data erasure.

Fingerprint dedupe does not stop this: the dedupe scope is per topic, so the
relabelled body lands in its own bucket and reads as a brand-new delivery.

PR #966 closed the mirror-image (CONSTRUCTIVE) direction -- a relabelled body
booking a phantom order -- with one shared positive shape classifier
(shopify_ingest.order_payload_refusal). This module is the DESTRUCTIVE
direction, and lives in its own module for the same reason: BOTH delete
handlers import ONE definition, so the two can never drift apart. Guarding only
orders/delete would recreate this repo's most-repeated defect -- a correct rule
applied to one place and not its twin.

WHAT A GENUINE SHOPIFY DELETE PAYLOAD LOOKS LIKE  (documented, not assumed)
--------------------------------------------------------------------------
Taken VERBATIM from shopify.dev's Admin REST webhook reference (identical in
2026-04 / 2026-07 / 2026-10), because guessing this wrong in the strict
direction silently breaks a real flow:

    orders/delete      {"id": 820982911946154508}

    customers/delete   {"id": 706405506930370084,
                        "tax_exemptions": [],
                        "admin_graphql_api_id":
                            "gid://shopify/Customer/706405506930370084"}

A delete webhook is near-empty because the resource is gone -- there is no
resource content left to carry. That makes a POSITIVE minimality assertion
possible: the payload may carry the id plus pure delivery metadata, and NOTHING
ELSE. Any resource-content field -- line_items, financial_status, order_number,
email, addresses -- proves the body is some OTHER resource wearing a delete
label.

`tax_exemptions` is the ONE exception, and it is an exception on the CUSTOMER
kind only (see _KIND_EXTRA_ALLOWED_KEYS). It is admitted not because it is
harmless in the abstract but because Shopify's real customers/delete body
carries it, and refusing it would refuse EVERY genuine erasure request. It
costs nothing: every customers/create and customers/update body that carries
tax_exemptions also carries email / first_name / state / addresses, so the
minimality rule still refuses those on the OTHER keys.

The minimality rule is the LOAD-BEARING one. It does not depend on the
create-marker lists below having anticipated the right field names: an
attacker's only material is captured *signed* bodies, and no real Shopify
CREATE or UPDATE body is an id plus delivery metadata.

WHAT THIS GUARD DOES **NOT** CLOSE  (stated plainly, not glossed)
-----------------------------------------------------------------
Every Shopify *delete* body has the same near-empty shape -- products/delete,
collections/delete and draft_orders/delete are all literally `{"id": N}`. A
shape classifier therefore CANNOT tell orders/delete apart from products/delete;
relabelling one delete topic as another still passes here. What stops that from
doing damage is the handler's own lookup: the id must already exist in IMS as a
`shopify_order_id` / `shopify_customer_id`, and Shopify ids are minted per
resource type, so a product id matching a live order row would be coincidence.
That residual is small but real, and this module does not pretend otherwise.

FAILURE DIRECTION -- DELIBERATELY THE LOUD ONE
----------------------------------------------
Too strict refuses a genuine delete: an order deleted in Shopify keeps showing
as live in IMS, or an erasure request is not flagged. That is VISIBLE (a
WARNING naming the exact unexpected keys) and fully recoverable -- the merchant
can void the order in IMS by hand and the webhook can be resent.

Too loose lets the replay through: a real customer's order is voided, or a real
customer is flagged for erasure, with no signal that anything is wrong.

Silent-and-wrong is worse than loud-and-refused, so this guard is strict --
the same call PR #966 made. The allowed-key sets may only ever gain a key that
either carries NO resource content (an identifier or a timestamp) or is proven
to appear in Shopify's DOCUMENTED delete body for that kind; anything else
quietly converts this positive assertion back into a guess.

PUBLIC API:
    delete_payload_refusal(payload, *, kind) -> Optional[str]
    unexpected_delete_keys(payload, *, kind) -> list[str]
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional

# Kinds this guard classifies.
KIND_ORDER = "order"
KIND_CUSTOMER = "customer"

# Keys ANY genuine delete payload may carry. Every entry is pure identity or
# delivery metadata -- none of them carries resource content, so a body built
# only from these keys says nothing more than "this record".
# DO NOT add a content-bearing field here (see the module docstring).
_DELETE_BASE_ALLOWED_KEYS = frozenset(
    {
        "id",
        "admin_graphql_api_id",
        "shop_id",
        "shop_domain",
        "deleted_at",
        "created_at",
        "updated_at",
    }
)

# Per-kind additions, admitted ONLY on the evidence of Shopify's documented
# delete body for that kind. customers/delete really does ship an (empty)
# tax_exemptions array; without this the guard would refuse every genuine
# GDPR/DPDP erasure request -- silent non-compliance dressed up as security.
_KIND_EXTRA_ALLOWED_KEYS: Dict[str, FrozenSet[str]] = {
    KIND_ORDER: frozenset(),
    KIND_CUSTOMER: frozenset({"tax_exemptions"}),
}

# Precise-diagnosis rules. These are NOT what stops the attack -- the
# minimality rule below is -- but they name the offending shape exactly, which
# is what an on-call reader needs from a log line. Mirrors the checkout-marker
# rules in shopify_ingest.order_payload_refusal.
#
# NOTE: `tax_exemptions` is deliberately ABSENT from the customer markers. It is
# on the genuine customers/delete body, so listing it here would refuse every
# real erasure. The create bodies that carry it are caught on their other keys.
_ORDER_CREATE_MARKERS = (
    "line_items",
    "financial_status",
    "fulfillment_status",
    "order_status_url",
    "order_number",
    "checkout_token",
    "cart_token",
    "total_price",
    "current_total_price",
    "subtotal_price",
    "total_tax",
    "payment_gateway_names",
    "billing_address",
    "shipping_address",
)
_CUSTOMER_CREATE_MARKERS = (
    "email",
    "phone",
    "first_name",
    "last_name",
    "addresses",
    "default_address",
    "orders_count",
    "total_spent",
    "accepts_marketing",
    "email_marketing_consent",
    "sms_marketing_consent",
    "verified_email",
    "tax_exempt",
    "multipass_identifier",
    "currency",
    "last_order_id",
    "last_order_name",
    "tags",
    "note",
    "state",
)

# The parent-reference key per kind: a payload naming a parent is a CHILD
# resource (refund / fulfillment / transaction), never a delete of the parent.
_PARENT_KEY = {KIND_ORDER: "order_id", KIND_CUSTOMER: "customer_id"}

_CREATE_MARKERS = {
    KIND_ORDER: _ORDER_CREATE_MARKERS,
    KIND_CUSTOMER: _CUSTOMER_CREATE_MARKERS,
}


def _allowed_keys(kind: str) -> FrozenSet[str]:
    """Allowed top-level keys for `kind`. An UNKNOWN kind gets the strictest
    set (base only) -- an unrecognised caller must never widen the door."""
    return _DELETE_BASE_ALLOWED_KEYS | _KIND_EXTRA_ALLOWED_KEYS.get(
        kind, frozenset()
    )


def unexpected_delete_keys(payload: Dict[str, Any], *, kind: str) -> List[str]:
    """The top-level keys on `payload` that a genuine `kind` delete body cannot
    carry.

    Pure. Sorted, so log lines are stable and greppable.
    """
    if not isinstance(payload, dict):
        return []
    allowed = _allowed_keys(kind)
    return sorted(str(k) for k in payload if str(k) not in allowed)


def delete_payload_refusal(
    payload: Dict[str, Any], *, kind: str
) -> Optional[str]:
    """Return a refusal reason when `payload` must NOT be acted on as a delete.

    Returns None when the payload is delete-shaped and may proceed.

    `kind` must be KIND_ORDER or KIND_CUSTOMER. It selects the parent key, the
    create-marker list, and the per-kind allowed-key margin; an unrecognised
    kind is refused outright rather than silently classified under the base
    rules, so a typo at a future third door cannot ship quietly. The
    load-bearing minimality rule is otherwise identical for both kinds, which is
    the whole point of one shared helper.

    Reasons are stable strings; callers surface them verbatim:
      "unknown_delete_kind"    -- caller bug: kind is not a kind we classify
      "child_resource_payload" -- names a parent (a refund / fulfillment / ...)
      "create_shaped_payload"  -- carries create-payload content fields
      "not_delete_shaped"      -- carries any other content field at all

    An EMPTY payload is not refused here: it has no content to be wrong about,
    and the caller's existing "no id -> skipped" branch already handles it.
    """
    payload = payload if isinstance(payload, dict) else {}

    # 0. Caller sanity. Fails CLOSED (both handlers turn a refusal into a
    #    no-write skip), and loudly, rather than degrading to base-only rules.
    if kind not in _KIND_EXTRA_ALLOWED_KEYS:
        return "unknown_delete_kind"

    # 1. Explicit parent reference -> this payload's `id` is a CHILD id, so
    #    resolving the delete target from it would hit the wrong record (or,
    #    via the old `or payload.get(parent)` fallback, exactly the right one
    #    for entirely the wrong reason).
    parent_key = _PARENT_KEY.get(kind)
    if parent_key and str(payload.get(parent_key) or "").strip():
        return "child_resource_payload"

    # 2. Create-shaped content -> precise diagnosis for the log. A body that
    #    dodges every marker in the list is still refused by rule 3; these
    #    rules must never be treated as the thing holding the door.
    for marker in _CREATE_MARKERS.get(kind, ()):
        if marker in payload:
            return "create_shaped_payload"

    # 3. POSITIVE MINIMALITY ASSERTION -- the LOAD-BEARING rule. A genuine
    #    delete body is an id and delivery metadata, full stop. Anything else
    #    on it proves this is a different resource wearing a delete label.
    if unexpected_delete_keys(payload, kind=kind):
        return "not_delete_shaped"

    return None
