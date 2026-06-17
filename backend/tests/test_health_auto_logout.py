"""
IMS 2.0 - /health auto_logout policy block
==========================================
The PUBLIC /health endpoint exposes an `auto_logout` object so EVERY
authenticated user (including SALES_STAFF) can read the idle-logout policy
without an admin-gated call. The frontend session watcher reads it at startup.

Contract:
- With no system_settings stored, /health returns the fail-soft defaults
  (enabled=True, minutes=15, warn_seconds=60).
- When the system_settings singleton stores the override keys, /health reflects
  them (clamped to safe ranges).

/health must NEVER 500, so the resolver is fully wrapped in try/except and the
override portion is skipped when no DB is connected (local runs).
"""

import pytest


def _no_db(put_resp) -> bool:
    """True when a settings PUT ran without a DB (so we can't round-trip)."""
    try:
        return "(no DB)" in (put_resp.json().get("message", "") or "")
    except Exception:  # noqa: BLE001
        return False


def _reset_cache():
    """Bust the module-level TTL cache so a freshly-saved override is reflected
    immediately rather than waiting out the 30s TTL."""
    try:
        import api.main as _main

        _main._reset_auto_logout_cache()
    except Exception:  # noqa: BLE001
        pass


def test_health_auto_logout_defaults(client):
    """No overrides stored -> /health returns the documented defaults."""
    _reset_cache()
    resp = client.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "auto_logout" in body
    al = body["auto_logout"]
    assert al["enabled"] is True
    assert al["minutes"] == 15
    assert al["warn_seconds"] == 60


def test_health_auto_logout_also_under_api_v1(client):
    """The block is present on the /api/v1/health alias too."""
    _reset_cache()
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200, resp.text
    al = resp.json().get("auto_logout") or {}
    assert al.get("minutes") == 15
    assert al.get("warn_seconds") == 60
    assert al.get("enabled") is True


def test_health_auto_logout_reflects_overrides(client, auth_headers):
    """Stored overrides on the system_settings singleton are reflected on /health."""
    put = client.put(
        "/api/v1/settings/system",
        json={
            "auto_logout_enabled": False,
            "auto_logout_minutes": 30,
            "auto_logout_warn_seconds": 120,
        },
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    if _no_db(put):
        pytest.skip("system_settings collection unavailable (no DB)")

    _reset_cache()
    al = client.get("/health").json().get("auto_logout") or {}
    assert al.get("enabled") is False
    assert al.get("minutes") == 30
    assert al.get("warn_seconds") == 120

    # Restore the defaults so later tests / the singleton are clean.
    client.put(
        "/api/v1/settings/system",
        json={
            "auto_logout_enabled": True,
            "auto_logout_minutes": 15,
            "auto_logout_warn_seconds": 60,
        },
        headers=auth_headers,
    )
    _reset_cache()


def test_health_auto_logout_clamps_out_of_range(client, auth_headers):
    """Out-of-range stored values are clamped to the safe bounds on read."""
    put = client.put(
        "/api/v1/settings/system",
        json={
            "auto_logout_enabled": True,
            "auto_logout_minutes": 9999,  # clamps to 480
            "auto_logout_warn_seconds": 1,  # clamps to 10
        },
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    if _no_db(put):
        pytest.skip("system_settings collection unavailable (no DB)")

    _reset_cache()
    al = client.get("/health").json().get("auto_logout") or {}
    assert al.get("minutes") == 480
    assert al.get("warn_seconds") == 10

    # Restore the defaults.
    client.put(
        "/api/v1/settings/system",
        json={
            "auto_logout_enabled": True,
            "auto_logout_minutes": 15,
            "auto_logout_warn_seconds": 60,
        },
        headers=auth_headers,
    )
    _reset_cache()
