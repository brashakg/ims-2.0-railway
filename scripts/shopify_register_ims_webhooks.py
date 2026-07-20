# Register the full set of Shopify webhook topics IMS's signed receiver handles,
# INCLUDING the three lifecycle-cleanup topics IMS now has handlers for:
#   orders/delete      -> VOID the IMS online order (soft; never hard-delete)
#   customers/delete   -> flag the IMS customer for data erasure (keep history)
#   app/uninstalled    -> loud alert + integration-health record (token revoked)
#
# The base BVI-retirement cutover set lives in
# backend/api/services/shopify_push.py (CUTOVER_WEBHOOK_TOPICS); this runner
# imports it and EXTENDS it with the three topics above, then hands the combined
# list to shopify_push.register_webhooks -- which is IDEMPOTENT (it only creates
# subscriptions for topics not already registered at this exact callback URL, and
# stays DARK/SIMULATED unless the triple push gate is live).
#
# DRY-RUN BY DEFAULT (prints the plan, no Shopify mutation). Pass --apply to
# create the missing subscriptions. Run against prod via Railway so creds are
# injected, e.g.:
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\shopify_register_ims_webhooks.py
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\shopify_register_ims_webhooks.py --apply --base-url https://ims-api.example.com

import asyncio
import os
import sys

from pymongo import MongoClient

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)

from api.services import shopify_push  # noqa: E402

# The lifecycle-cleanup topics IMS added handlers for (agents/implementations/
# nexus.py _SHOPIFY_TOPIC_HANDLERS). Kept here so registering them is
# reproducible and greppable, not a one-off manual dashboard action.
IMS_EXTRA_WEBHOOK_TOPICS = [
    "orders/delete",
    "customers/delete",
    "app/uninstalled",
]


def registered_topics():
    """The full topic set to register: the shared cutover set + IMS's extra
    lifecycle-cleanup topics, de-duplicated while preserving order."""
    combined = list(shopify_push.CUTOVER_WEBHOOK_TOPICS) + IMS_EXTRA_WEBHOOK_TOPICS
    seen = set()
    out = []
    for topic in combined:
        if topic not in seen:
            seen.add(topic)
            out.append(topic)
    return out


def _resolve_base_url(argv):
    for i, arg in enumerate(argv):
        if arg == "--base-url" and i + 1 < len(argv):
            return argv[i + 1].strip()
        if arg.startswith("--base-url="):
            return arg.split("=", 1)[1].strip()
    return (
        os.environ.get("IMS_PUBLIC_BASE_URL")
        or os.environ.get("PUBLIC_BASE_URL")
        or ""
    ).strip()


def main():
    apply = "--apply" in sys.argv
    base_url = _resolve_base_url(sys.argv)
    topics = registered_topics()

    print("IMS Shopify webhook topics to register (idempotent):")
    for topic in topics:
        marker = "  (new)" if topic in IMS_EXTRA_WEBHOOK_TOPICS else ""
        print(f"  - {topic}{marker}")

    if not base_url:
        print(
            "\nERROR: no callback base URL. Pass --base-url https://<ims-api-host> "
            "or set IMS_PUBLIC_BASE_URL. (Dry-run still needs it to build the "
            "callback URL.)"
        )
        return 1

    url = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGODB_URL")
    db = None
    if url:
        db = MongoClient(url, serverSelectionTimeoutMS=20000)["ims_2_0"]
    else:
        print(
            "\nNOTE: no MONGO_PUBLIC_URL / MONGODB_URL -- running without a DB "
            "(register_webhooks stays SIMULATED)."
        )

    result = asyncio.run(
        shopify_push.register_webhooks(db, base_url, topics=topics, apply=apply)
    )

    print(f"\nmode         : {result.get('mode')}")
    print(f"applied      : {result.get('applied')}")
    print(f"callback_url : {result.get('callback_url')}")
    print(f"already      : {result.get('already_registered')}")
    print(f"missing      : {result.get('missing')}")
    print(f"created      : {result.get('created')}")
    print(f"conflicts    : {result.get('conflicts')}")
    if result.get("errors"):
        print(f"errors       : {result.get('errors')}")
    if not apply:
        print("\nDRY-RUN (no mutation). Re-run with --apply to create missing subs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
