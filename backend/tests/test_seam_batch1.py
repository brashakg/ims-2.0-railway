"""Regression tests for the FE<->BE seam batch-1 fixes:

  - GET /api/v1/users must be registered WITHOUT a trailing slash (the FE calls
    `/users`; with redirect_slashes=False the slash-only route 404'd, breaking
    User Management, the Activity-Log filter, and the Store-Setup employee tab).
  - The vendor-portal status update must accept the key the public lab portal FE
    actually submits ("vendor_tracking_url"), not only the canonical "tracking_url"
    (the unknown key was silently dropped, losing the lab's tracking URL).
"""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_users_list_route_registered_without_trailing_slash():
    from api.main import app

    matches = [
        r
        for r in app.routes
        if getattr(r, "path", None) == "/api/v1/users"
        and "GET" in (getattr(r, "methods", None) or set())
    ]
    assert matches, "GET /api/v1/users (no trailing slash) is not registered"


def test_vendor_portal_status_accepts_vendor_tracking_url_alias():
    from api.routers.vendor_portal import VendorPortalStatusUpdate

    # Key the public lab-portal FE actually sends.
    m = VendorPortalStatusUpdate(**{"status": "DISPATCHED", "vendor_tracking_url": "https://lab/track/1"})
    assert m.tracking_url == "https://lab/track/1"

    # Canonical key must still work.
    m2 = VendorPortalStatusUpdate(**{"status": "DISPATCHED", "tracking_url": "https://lab/track/2"})
    assert m2.tracking_url == "https://lab/track/2"

    # Absent -> None (optional).
    m3 = VendorPortalStatusUpdate(**{"status": "RECEIVED"})
    assert m3.tracking_url is None
