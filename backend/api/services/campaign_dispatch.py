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

Ownership (Codex 1419 round 1). A ONE_TIME (or unscheduled) campaign reaches
each recipient ONCE, whichever door runs and however often: every row carries
dedupe_ref "campaign:<campaign_id>:<customer or phone>", backed by the unique
partial index uniq_notification_dedupe_ref on notification_logs. A recipient
already queued for the campaign is reported "already_queued" (not re-gated,
not re-capped, not counted again); two doors racing on one recipient are
settled by the index (the loser sees a duplicate). A legacy RECURRING /
TRIGGERED campaign has no ref: a hand re-send of it is deliberate.

Durability. A recipient counts as queued (and consumes the 30-day cap) only
when the queue writer reports the row ON DISK (`persisted`); a storage failure
is "failed" with retryable=True so the door can leave the campaign open for a
safe retry, and an unfilled placeholder (D56) is "failed" with
reason unfilled_placeholder (not retryable: the template itself is wrong).

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

        {"audience": n, "sent": n, "skipped": n, "failed": n,
         "already_queued": n, "retryable": n, "results": [...]}

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
    kind = ((campaign.get("schedule") or {}).get("kind") or "ONE_TIME").upper()
    once_per_recipient = kind == "ONE_TIME"
    logs = db.get_collection("notification_logs")
    if once_per_recipient:
        # The unique backstop must exist BEFORE anything is queued; without it
        # two doors could each insert a row. Same index the SMS fallback uses.
        try:
            logs.create_index(
                "dedupe_ref",
                unique=True,
                partialFilterExpression={"dedupe_ref": {"$type": "string"}},
                name="uniq_notification_dedupe_ref",
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"campaign queue index unavailable, nothing queued: {exc}"
            ) from exc

    audience = seg.resolve_segment(
        db,
        campaign.get("segment_key", ""),
        store_id=campaign.get("store_id") or None,
        params=campaign.get("segment_params") or {},
    )

    results: List[Dict[str, Any]] = []
    sent = skipped = failed = already = retryable = 0

    def _skip(cid, reason: str) -> None:
        nonlocal skipped
        skipped += 1
        results.append({"customer_id": cid, "status": "skipped", "reason": reason})

    for r in audience:
        cid = r.get("customer_id")
        phone = r.get("phone") or ""
        ref = (
            f"campaign:{campaign_id}:{cid or phone}"
            if once_per_recipient and (cid or phone)
            else None
        )
        # Already queued for this campaign (another door, an earlier try, a
        # lost acknowledgement): nothing to do. A read failure here is a
        # failed recipient, never a guess.
        if ref:
            try:
                if logs.find_one({"dedupe_ref": ref}, {"_id": 1}):
                    already += 1
                    results.append(
                        {"customer_id": cid, "status": "already_queued"}
                    )
                    continue
            except Exception as exc:  # noqa: BLE001
                failed += 1
                retryable += 1
                results.append(
                    {"customer_id": cid, "status": "failed", "error": str(exc)}
                )
                continue
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
                # Stamped AT INSERT (one write): the campaign tag and the
                # once-per-recipient ref the unique index enforces.
                extra={"campaign_id": campaign_id, **({"dedupe_ref": ref} if ref else {})},
            )
            if res.get("duplicate"):
                # Another door queued this recipient between our check and
                # our insert; the unique index kept it to ONE row.
                already += 1
                results.append({"customer_id": cid, "status": "already_queued"})
                continue
            if res.get("status") == "REFUSED":
                failed += 1
                results.append(
                    {
                        "customer_id": cid,
                        "status": "failed",
                        "reason": "unfilled_placeholder",
                        "missing_variables": res.get("missing_variables"),
                    }
                )
                continue
            if not res.get("persisted"):
                failed += 1
                retryable += 1
                results.append(
                    {"customer_id": cid, "status": "failed", "reason": "not_saved"}
                )
                continue
            nid = res.get("notification_id")
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
            retryable += 1
            results.append({"customer_id": cid, "status": "failed", "error": str(exc)})
            logger.debug("campaign %s recipient failed: %s", campaign_id, exc)

    return {
        "audience": len(audience),
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "already_queued": already,
        "retryable": retryable,
        "results": results,
    }
