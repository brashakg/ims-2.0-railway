"""Shopify push -- webhooks

Webhook subscription registration (Phase-6 cutover: Shopify
must call IMS): list/create/delete subscriptions, fail-soft.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._shared import MODE_LIVE, MODE_SIMULATED, _live_or_reason
from .transport import _graphql, _user_errors

# ===========================================================================
# WEBHOOK SUBSCRIPTION REGISTRATION (Phase-6 cutover: Shopify must call IMS)
# ===========================================================================
# Today orders flow Shopify -> BVI. At the baton cutover Shopify must instead
# POST signed webhooks at IMS's already-live receiver POST /api/v1/webhooks/
# shopify (routers/webhooks.py: HMAC-verified against the `integrations` doc's
# shopify webhook_secret, persisted to webhook_inbox, drained by NEXUS). This
# registrar creates the missing webhookSubscriptions via the Admin API.
#
# NOTE for verification: webhooks created via the Admin API are signed by
# Shopify with the CUSTOM APP's API SECRET KEY (the app whose access token we
# push with) -- NOT the "Notifications" shared secret shown in the Shopify
# admin UI. The owner must store that API secret key as `webhook_secret` on
# the shopify `integrations` config or every delivery will 401.

_WEBHOOK_SUBSCRIPTIONS_QUERY = """
query imsWebhookSubscriptions($first: Int!, $after: String) {
  webhookSubscriptions(first: $first, after: $after) {
    edges {
      node {
        id
        topic
        endpoint {
          __typename
          ... on WebhookHttpEndpoint { callbackUrl }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# The webhook subscription list is paginated: the custom app can accumulate >100
# subscriptions (BVI history + retries), so a single first:100 read would miss
# subs on later pages -> a sub already at IMS's URL would look 'missing' and a
# BVI-pointing conflict would go unsurfaced (finding #19). Walk every page (up to
# this fail-soft cap) before deciding create/skip/delete.
_WEBHOOK_PAGE_SIZE = 100
_WEBHOOK_MAX_PAGES = 10

_WEBHOOK_SUBSCRIPTION_CREATE = """
mutation imsWebhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
    webhookSubscription { id topic }
    userErrors { field message }
  }
}
"""

# Phase-6 cutover: delete a conflicting subscription (e.g. one still pointing at
# BVI) so Shopify stops double-delivering the same topic once IMS is registered.
_WEBHOOK_SUBSCRIPTION_DELETE = """
mutation imsWebhookSubscriptionDelete($id: ID!) {
  webhookSubscriptionDelete(id: $id) {
    deletedWebhookSubscriptionId
    userErrors { field message }
  }
}
"""

# The receiver route the subscriptions point at (mounted under /api/v1).
_WEBHOOK_RECEIVER_PATH = "/api/v1/webhooks/shopify"

# BVI-retirement cutover topic set: every Shopify webhook IMS must receive once
# BVI is retired -- the order lifecycle (count-once invoice + status sync),
# refunds (GST credit note + restock), fulfilments (shipped/tracking), and
# customers (CRM upsert). register_webhooks defaults to THIS set so a single
# apply=true registers everything the receiver now handles.
CUTOVER_WEBHOOK_TOPICS = [
    "orders/create",
    "orders/paid",
    "orders/updated",
    "orders/cancelled",
    "orders/fulfilled",
    "orders/partially_fulfilled",
    "refunds/create",
    "fulfillments/create",
    "fulfillments/update",
    "customers/create",
    "customers/update",
]


def _topic_enum(topic: str) -> str:
    """'orders/create' -> 'ORDERS_CREATE' (Shopify WebhookSubscriptionTopic).
    Already-enum input ('ORDERS_CREATE') passes through unchanged."""
    return str(topic or "").strip().replace("/", "_").replace(".", "_").upper()


async def delete_webhook_subscription(db, subscription_id: str) -> Dict[str, Any]:
    """Delete ONE Shopify webhookSubscription by gid (Phase-6 cutover: drop a
    conflicting subscription still pointing at BVI so a topic stops
    double-delivering). DARK by default -> SIMULATED, no network call; LIVE only
    behind the same three push gates. Fail-soft: returns a structured dict, never
    raises."""
    result: Dict[str, Any] = {
        "ok": True,
        "mode": MODE_SIMULATED,
        "id": subscription_id,
        "deleted": None,
        "errors": [],
        "reason": None,
    }
    if not subscription_id:
        result["ok"] = False
        result["errors"].append("no subscription id")
        return result

    live, reason = _live_or_reason(db)
    result["reason"] = reason
    result["mode"] = MODE_LIVE if live else MODE_SIMULATED
    if not live:
        result["note"] = "SIMULATED: push gates closed -- no Shopify call made"
        return result

    try:
        body = await _graphql(db, _WEBHOOK_SUBSCRIPTION_DELETE, {"id": subscription_id})
        err = _user_errors(body, "webhookSubscriptionDelete")
        if err:
            result["ok"] = False
            result["errors"].append(err)
            return result
        deleted = (
            (body.get("data") or {}).get("webhookSubscriptionDelete") or {}
        ).get("deletedWebhookSubscriptionId")
        result["deleted"] = deleted
        return result
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        result["ok"] = False
        result["errors"].append(str(e))
        return result


async def register_webhooks(
    db,
    callback_base_url: str,
    topics: Optional[List[str]] = None,
    apply: bool = False,
    delete_conflicts: bool = False,
) -> Dict[str, Any]:
    """Ensure Shopify webhookSubscriptions exist for `topics`, pointing at
    {callback_base_url}/api/v1/webhooks/shopify. Fail-soft: returns a structured
    dict, never raises.

    `topics` DEFAULTS to the full BVI-retirement cutover set
    (CUTOVER_WEBHOOK_TOPICS: order lifecycle + refunds/create +
    fulfillments/create,update + customers/create,update) so a single apply=true
    registers everything the receiver now handles.

    DRY-RUN by default (apply=False): reports what WOULD be registered. When
    the three push gates are LIVE the dry-run also QUERIES the existing
    subscriptions (a read); when DARK it makes NO network call at all.
    Mutations happen ONLY when apply=True AND the gates are LIVE, and only for
    topics not already subscribed at this exact callback URL (idempotent).

    `delete_conflicts` (default False): when True AND apply=True AND LIVE, also
    DELETE every surfaced conflict (a requested topic subscribed at a DIFFERENT
    callback URL -- e.g. still pointing at BVI) so the cutover leaves exactly one
    delivery per topic. Left False, conflicts are only SURFACED, never removed."""
    topic_enums = [
        _topic_enum(t) for t in (topics or CUTOVER_WEBHOOK_TOPICS) if _topic_enum(t)
    ]
    base = str(callback_base_url or "").strip().rstrip("/")
    callback_url = base + _WEBHOOK_RECEIVER_PATH

    live, reason = _live_or_reason(db)
    result: Dict[str, Any] = {
        "ok": True,
        "mode": MODE_LIVE if live else MODE_SIMULATED,
        "applied": False,
        "callback_url": callback_url,
        "topics": topic_enums,
        "existing": [],
        "already_registered": [],
        "missing": list(topic_enums),
        "conflicts": [],
        "created": [],
        "deleted_conflicts": [],
        "errors": [],
        "reason": reason,
    }
    if not base.startswith("https://"):
        result["ok"] = False
        result["errors"].append(
            "callback_base_url must be https:// (Shopify rejects non-https "
            "webhook endpoints)"
        )
        return result
    if not topic_enums:
        result["ok"] = False
        result["errors"].append("no topics requested")
        return result

    if not live:
        # DARK: no network at all (not even the read). The plan lists every
        # requested topic as missing; existing subscriptions are unknown.
        result["note"] = (
            "SIMULATED: push gates closed -- no Shopify call made; existing "
            "subscriptions unknown, every requested topic listed as missing."
        )
        return result

    try:
        # PAGINATE the subscription list: walk every page (until hasNextPage is
        # false, capped fail-soft at _WEBHOOK_MAX_PAGES) so a sub beyond page 1 is
        # never missed (finding #19). A missed sub would either force a duplicate
        # create (userError) or leave a BVI conflict unsurfaced/undeleted.
        edges: List[Dict[str, Any]] = []
        after: Optional[str] = None
        for _page in range(_WEBHOOK_MAX_PAGES):
            body = await _graphql(
                db,
                _WEBHOOK_SUBSCRIPTIONS_QUERY,
                {"first": _WEBHOOK_PAGE_SIZE, "after": after},
            )
            if not isinstance(body, dict) or body.get("errors"):
                result["ok"] = False
                result["errors"].append(
                    f"webhookSubscriptions query failed: {str((body or {}).get('errors'))[:300]}"
                )
                return result
            conn = ((body.get("data") or {}).get("webhookSubscriptions") or {})
            edges.extend(conn.get("edges") or [])
            page_info = conn.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
            if not after:
                break
        existing: List[Dict[str, Any]] = []
        for e in edges:
            node = (e or {}).get("node") or {}
            endpoint = node.get("endpoint") or {}
            existing.append(
                {
                    "id": node.get("id"),
                    "topic": node.get("topic"),
                    "callback_url": endpoint.get("callbackUrl"),
                }
            )
        result["existing"] = existing
        already = {
            x["topic"]
            for x in existing
            if x.get("topic") in topic_enums
            and x.get("callback_url") == callback_url
        }
        # Same topic subscribed at a DIFFERENT URL (e.g. still pointing at
        # BVI): surfaced so the owner sees the double-delivery risk; we still
        # treat OUR url as missing.
        result["conflicts"] = [
            x
            for x in existing
            if x.get("topic") in topic_enums
            and x.get("callback_url")
            and x.get("callback_url") != callback_url
        ]
        result["already_registered"] = sorted(already)
        result["missing"] = [t for t in topic_enums if t not in already]

        if not apply:
            return result

        result["applied"] = True
        for t in result["missing"]:
            body = await _graphql(
                db,
                _WEBHOOK_SUBSCRIPTION_CREATE,
                {
                    "topic": t,
                    "webhookSubscription": {
                        "callbackUrl": callback_url,
                        "format": "JSON",
                    },
                },
            )
            err = _user_errors(body, "webhookSubscriptionCreate")
            if err:
                result["ok"] = False
                result["errors"].append(f"{t}: {err}")
                continue
            sub = (
                (body.get("data") or {}).get("webhookSubscriptionCreate") or {}
            ).get("webhookSubscription") or {}
            result["created"].append({"topic": t, "id": sub.get("id")})

        # Cutover cleanup: optionally DELETE the surfaced conflicts (same topic
        # still pointing at a different URL, e.g. BVI) so each topic delivers
        # exactly once after the baton hand-off. Off by default (conflicts are
        # only surfaced). Each delete is fail-soft + recorded.
        #
        # SAFETY (finding #16): a conflict's old (BVI) subscription is deleted
        # ONLY once a WORKING subscription at the IMS callback URL provably exists
        # for that topic -- either just created this run (in result['created']) or
        # already registered (result['already_registered']). Deleting on a FAILED
        # create would leave that topic delivering NOWHERE (a zero-receiver gap:
        # refunds/orders webhooks silently stop reaching BOTH BVI and IMS). Order
        # is therefore create/verify IMS sub -> THEN delete the conflict.
        if delete_conflicts:
            safe_topics = {
                c.get("topic") for c in result["created"]
            } | set(result["already_registered"])
            for c in result["conflicts"]:
                topic = c.get("topic")
                if topic not in safe_topics:
                    # No confirmed IMS replacement for this topic -> keep the old
                    # subscription (do NOT create a zero-receiver gap).
                    result["ok"] = False
                    result["errors"].append(
                        f"skipped delete for {topic}: replacement create at IMS URL "
                        "did not succeed -- old subscription kept to avoid a "
                        "zero-receiver gap"
                    )
                    continue
                del_res = await delete_webhook_subscription(db, c.get("id"))
                if del_res.get("ok") and del_res.get("deleted"):
                    result["deleted_conflicts"].append(
                        {"topic": topic, "id": c.get("id")}
                    )
                else:
                    result["ok"] = False
                    result["errors"].append(
                        f"delete conflict {topic}: {del_res.get('errors')}"
                    )
        return result
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        result["ok"] = False
        result["errors"].append(str(e))
        return result
