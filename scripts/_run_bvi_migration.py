"""Wrapper for migrate_bvi_pim.py --commit run via the nested railway-run env
chain: maps DATABASE_PUBLIC_URL (old BVI Postgres) -> ECOMMERCE_DATABASE_URL
(+sslmode=require, the proxy needs it) and MONGO_PUBLIC_URL -> MONGODB_URL,
retries the flaky PG proxy connect, then hands off to the migration. No secret
is ever printed."""
import os
import runpy
import sys
import time


def main() -> None:
    u = os.environ.get("DATABASE_PUBLIC_URL", "")
    if not u:
        print("[WRAPPER] ERROR: DATABASE_PUBLIC_URL not injected")
        sys.exit(1)
    os.environ["ECOMMERCE_DATABASE_URL"] = u + ("&" if "?" in u else "?") + "sslmode=require"
    m = os.environ.get("MONGO_PUBLIC_URL", "")
    if not m:
        print("[WRAPPER] ERROR: MONGO_PUBLIC_URL not injected")
        sys.exit(1)
    os.environ["MONGODB_URL"] = m

    import psycopg2  # noqa: PLC0415
    for attempt in range(1, 6):
        try:
            conn = psycopg2.connect(os.environ["ECOMMERCE_DATABASE_URL"], connect_timeout=15)
            conn.close()
            print("[WRAPPER] PG reachable on attempt %d" % attempt)
            break
        except Exception:  # noqa: BLE001
            print("[WRAPPER] PG attempt %d failed; sleeping 20s" % attempt)
            time.sleep(20)
    else:
        print("[WRAPPER] PG unreachable after 5 attempts; aborting")
        sys.exit(2)

    # Forward any extra CLI args (e.g. --entities variants) so phases can run
    # one at a time -- a mid-run proxy flake must not silently zero later phases.
    sys.argv = ["migrate_bvi_pim.py", "--commit"] + sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    runpy.run_path(os.path.join(here, "migrate_bvi_pim.py"), run_name="__main__")


if __name__ == "__main__":
    main()
