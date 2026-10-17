"""Shopify's own answer to the tracking + policy re-send, for the test fakes.

The real productVariantsBulkUpdate echoes every row it updated with the fields
the mutation selects (``inventoryPolicy inventoryItem { id tracked }``), and a
press puts or keeps a listing on the website only on that answer
(``shopify_push.tracking_verdicts``). A fake that answers the re-send with
nothing is a Shopify that confirmed nothing -- the press then withholds the
listing, as it should. Fakes that model a working Shopify answer with this.
"""


def tracking_answer(query, variables):
    """The echo body when ``query`` is the tracking re-send, else None."""
    if "imsVariantInventoryUpdate" not in (query or ""):
        return None
    rows = (variables or {}).get("variants") or []
    return {
        "data": {
            "productVariantsBulkUpdate": {
                "userErrors": [],
                "productVariants": [
                    {
                        "id": r.get("id"),
                        "inventoryPolicy": r.get("inventoryPolicy"),
                        "inventoryItem": {"id": None, "tracked": (r.get("inventoryItem") or {}).get("tracked")},
                    }
                    for r in rows
                ],
            }
        }
    }
