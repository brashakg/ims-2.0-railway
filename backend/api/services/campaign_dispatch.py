"""
IMS 2.0 - Campaign audience fan-out (THE one loop)
==================================================
A campaign reaches its audience through exactly one function, whoever starts
it: the manual "Send now" door (POST /marketing/campaigns/{id}/send) and
MEGAPHONE's scheduled tick both call fan_out_campaign.

Before this existed the scheduled tick carried its own copy of the loop, and
the copy had drifted (audit L1-F27): it tested only the customer's consent
flag and skipped the shared 30-day frequency cap and its comms_ledger row. One
loop means the two doors cannot disagree about who may be messaged.

What is shared (per recipient, in this order):
  1. consent        marketing.is_opted_out (flag + STOP/OPT_OUT ledger)
  2. frequency cap  reminder_rail.check_frequency_cap (comms_ledger, 30 days)
  3. a phone number
  4. queue          send_notification (PENDING; DISPATCH_MODE-gated drain)
  5. cap ledger     reminder_rail.record_outbound

What stays with the caller, on purpose: the per-user rate limit and the
quiet-hours answer (the manual door REFUSES outside the promo window, the
scheduled tick DEFERS to the next morning), and the campaign status / counter
write that follows the fan-out.

Nothing here talks to a provider. Every message is a PENDING row that only
MEGAPHONE's drain can dispatch, and only when DISPATCH_MODE allows it.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List

logger = logging.getLogger(__name__)


async def fan_out_campaign(
    db,
    campaign: Dict[str, Any],
    *,
    send_store_id: str,
    triggered_by: str,
    send_notification: Callable[..., Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Resolve the campaign's segment and queue one message per eligible
    recipient. Returns the counts and the per-recipient result list:

        {"audience": n, "sent": n, "skipped": n, "failed": n, "results": [...]}

    `send_notification` is passed in so each door keeps its own (test-patchable)
    reference to the one queue writer. A recipient failure is counted, never
    raised; a segment-resolution failure propagates BEFORE anything is queued.
    """
    from api.routers.marketing import is_opted_out
    from api.services import campaign_segments as seg
    from api.services import reminder_rail as rail

    campaign_id = campaign.get("campaign_id", "")
    template_id = campaign.get("template_id", "")
    primary_channel = (campaign.get("channels") or ["WHATSAPP"])[0]

    audience = seg.resolve_segment(
        db,
        campaign.get("segment_key", ""),
        store_id=campaign.get("store_id") or None,
        params=campaign.get("segment_params") or {},
    )

    results: List[Dict[str, Any]] = []
    sent = skipped = failed = 0

    def _skip(cid, reason: str) -> None:
        nonlocal skipped
        skipped += 1
        results.append({"customer_id": cid, "status": "skipped", "reason": reason})

    for r in audience:
        cid = r.get("customer_id")
        # Consent gate (full is_opted_out): flag OR a STOP/OPT_OUT ledger entry.
        # Ad-hoc rows w/o customer_id send (no consent record to check).
        if cid and is_opted_out(cid, db):
            _skip(cid, "opted_out")
            continue
        # Shared 30-day frequency cap (soft ceiling), the SAME cap the automated
        # reminder rail counts against.
        if cid and not rail.check_frequency_cap(db, cid, category="MARKETING"):
            _skip(cid, "freqcap")
            continue
        phone = r.get("phone") or ""
        if not phone:
            _skip(cid, "no_phone")
            continue
        try:
            res = await send_notification(
                store_id=send_store_id,
                customer_id=cid or "",
                customer_phone=phone,
                customer_name=r.get("name", "Customer"),
                template_id=template_id,
                channel=primary_channel,
                variables={**(r.get("variables") or {}), "campaign_id": campaign_id},
                category="MARKETING",
                triggered_by=triggered_by,
                related_entity_type="campaign",
                related_entity_id=campaign_id,
            )
            # Tag the just-queued notification_logs row with campaign_id so
            # analytics can aggregate it. send_notification doesn't take a
            # campaign_id, so we stamp it on the row it created.
            nid = res.get("notification_id")
            if nid:
                try:
                    db.get_collection("notification_logs").update_one(
                        {"notification_id": nid},
                        {"$set": {"campaign_id": campaign_id}},
                    )
                except Exception:  # noqa: BLE001
                    pass
            # Record the cap ledger row so this send counts against the shared
            # 30-day cap (transactional categories are skipped inside).
            if cid:
                rail.record_outbound(
                    db,
                    cid,
                    channel=primary_channel,
                    category="MARKETING",
                    campaign_id=campaign_id,
                )
            sent += 1
            results.append(
                {"customer_id": cid, "status": "queued", "notification_id": nid}
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            results.append({"customer_id": cid, "status": "failed", "error": str(exc)})
            logger.debug("campaign %s recipient failed: %s", campaign_id, exc)

    return {
        "audience": len(audience),
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }
