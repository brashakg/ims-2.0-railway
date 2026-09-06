"""Workshop hand-off: which orders need fitting, and the job creation.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

import logging
from datetime import date, timedelta
from typing import Optional
from ...dependencies import get_order_repository

# Item-type / category buckets used to decide whether a confirmed order needs a
# workshop/lab job. Mirrors the POS client's Phase-6.8 gate so the safety-net and
# the client agree on what "a fitting order" is.
_WORKSHOP_LENS_TYPES = {
    "LENS",
    "OPTICAL_LENS",
    "OPTICAL_LENSES",
    "SPECTACLE_LENS",
    "SPECTACLE_LENSES",
    "RX_LENSES",
    "LENSES",
}
_WORKSHOP_FRAME_TYPES = {"FRAME", "FRAMES", "SPECTACLE_FRAME", "SUNGLASS", "SUNGLASSES"}


def _order_item_kind(it: dict) -> str:
    return str(it.get("item_type") or it.get("category") or "").strip().upper()


def _order_needs_fitting(order: dict) -> bool:
    """A spectacle job needs the lab/workshop when it has a lens to grind, or a
    frame paired with a prescription. Accessory / watch / contact-lens-box /
    service- or eye-test-only orders do NOT."""
    items = order.get("items") or []
    has_lens = any(
        _order_item_kind(it) in _WORKSHOP_LENS_TYPES or it.get("lens_details")
        for it in items
    )
    if has_lens:
        return True
    has_frame = any(_order_item_kind(it) in _WORKSHOP_FRAME_TYPES for it in items)
    has_rx = bool(order.get("prescription_id")) or any(
        it.get("prescription_id") for it in items
    )
    return bool(has_frame and has_rx)


def _ensure_workshop_job_for_order(
    order: dict, by_user: Optional[str]
) -> Optional[str]:
    """Idempotently ensure a CONFIRMED fitting order has a workshop/lab job, and
    that the order carries the reverse `workshop_job_id` pointer.

    The POS happy-path already creates the job from the client (Phase 6.8); this
    is the SAFETY NET for (a) that client call failing and (b) orders confirmed
    via a non-POS path (e.g. the Orders page). It NEVER creates a duplicate
    (skips when find_by_order already has a job) and NEVER raises -- a
    workshop-job hiccup must never block confirming a paid order. Returns the
    job_id (created or pre-existing), else None.
    """
    try:
        if not _order_needs_fitting(order):
            return None
        order_id = order.get("order_id")
        if not order_id:
            return None
        from ...dependencies import get_workshop_repository

        wrepo = get_workshop_repository()
        if wrepo is None:
            return None
        order_repo = get_order_repository()

        existing = wrepo.find_by_order(order_id)
        if existing:
            jid = existing[0].get("job_id")
            # Backfill the reverse pointer if the client created the job but
            # never stamped it back onto the order.
            if jid and not order.get("workshop_job_id") and order_repo is not None:
                try:
                    order_repo.update(
                        order_id,
                        {
                            "workshop_job_id": jid,
                            "workshop_job_number": existing[0].get("job_number"),
                        },
                    )
                except Exception:
                    pass
            return jid

        items = order.get("items") or []
        frame = next(
            (it for it in items if _order_item_kind(it) in _WORKSHOP_FRAME_TYPES), None
        )
        lens = next(
            (
                it
                for it in items
                if _order_item_kind(it) in _WORKSHOP_LENS_TYPES
                or it.get("lens_details")
            ),
            None,
        )
        rx_id = (
            (lens or {}).get("prescription_id")
            or (frame or {}).get("prescription_id")
            or order.get("prescription_id")
            or ""
        )
        # expected_date: the order's scheduled delivery (YYYY-MM-DD) or +5 days.
        exp = str(order.get("expected_delivery") or "")[:10]
        if not exp:
            exp = (date.today() + timedelta(days=5)).isoformat()

        from ..workshop import generate_job_number

        job_data = {
            "job_number": generate_job_number(wrepo),
            "order_id": order_id,
            "store_id": order.get("store_id"),
            "frame_details": (
                {
                    "product_id": frame.get("product_id"),
                    "name": frame.get("product_name") or frame.get("name"),
                    "sku": frame.get("sku"),
                    "brand": frame.get("brand"),
                }
                if frame
                else {}
            ),
            "lens_details": (
                lens.get("lens_details")
                if (lens and lens.get("lens_details"))
                else (
                    {
                        "product_id": lens.get("product_id"),
                        "name": lens.get("product_name") or lens.get("name"),
                    }
                    if lens
                    else {}
                )
            ),
            "prescription_id": rx_id,
            "fitting_instructions": None,
            "special_notes": order.get("notes"),
            "expected_date": exp,
            "status": "PENDING",
            "created_by": by_user,
            # Provenance: spawned by the confirm safety-net, not the POS client.
            "auto_created": True,
        }
        created = wrepo.create(job_data)
        if created and order_repo is not None:
            try:
                order_repo.update(
                    order_id,
                    {
                        "workshop_job_id": created.get("job_id"),
                        "workshop_job_number": created.get("job_number"),
                    },
                )
            except Exception:
                pass
        return created.get("job_id") if created else None
    except Exception as e:  # never block a confirm
        import logging

        logging.getLogger(__name__).warning(
            "[ORDERS] workshop auto-link skipped for %s: %s",
            order.get("order_id"),
            e,
        )
        return None
