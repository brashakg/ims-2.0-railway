"""
IMS 2.0 - Clinical Device Import tests (CLI-12)
================================================
Covers the parse_device_csv() pure mapper and the
POST /api/v1/clinical/device-import endpoint.

Tests are intentionally free of a live DB dependency -- the mapper is pure and
the endpoint only reads the upload, so all assertions run without MongoDB.

Run:
    JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest \\
        backend/tests/test_clinical_device_import.py -v
"""

from __future__ import annotations

import os
import sys
from io import BytesIO

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from fastapi import HTTPException  # noqa: E402

from api.routers.clinical_device_import import (  # noqa: E402
    EyeRx,
    _parse_axis,
    _parse_rx_float,
    parse_device_csv,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv(header: str, *rows: str) -> bytes:
    """Build CSV bytes from a header string and row strings."""
    lines = [header] + list(rows)
    return "\n".join(lines).encode("utf-8")


def _topcon_csv(*row_values: tuple) -> bytes:
    """Build a minimal Topcon/Nidek-layout CSV.

    row_values is a sequence of (r_sph, r_cyl, r_axis, r_add,
                                   l_sph, l_cyl, l_axis, l_add) tuples.
    """
    header = "Date,Time,ID,R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
    rows = []
    for i, vals in enumerate(row_values):
        (rs, rc, ra, radd, ls, lc, la, ladd) = vals
        rows.append(f"2026-01-01,12:00:0{i},P{i},{rs},{rc},{ra},{radd},{ls},{lc},{la},{ladd}")
    return _csv(header, *rows)


def _huvitz_csv(*row_tuples) -> bytes:
    """Build a minimal Huvitz/Zeiss per-eye-row CSV.

    row_tuples: sequence of (eye, sph, cyl, axis, add).
    """
    header = "Eye,SPH,CYL,AXIS,ADD"
    rows = [f"{eye},{sph},{cyl},{axis},{add}" for (eye, sph, cyl, axis, add) in row_tuples]
    return _csv(header, *rows)


# ---------------------------------------------------------------------------
# _parse_rx_float unit tests
# ---------------------------------------------------------------------------


class TestParseRxFloat:
    def test_none_returns_none(self):
        assert _parse_rx_float(None, "sph") is None

    def test_empty_string_returns_none(self):
        assert _parse_rx_float("", "sph") is None

    def test_plano_markers(self):
        for marker in ("DS", "SPH", "PL", "-", "0", "0.00", "N/A"):
            assert _parse_rx_float(marker, "sph") is None

    def test_valid_sph(self):
        assert _parse_rx_float("-2.25", "sph") == pytest.approx(-2.25)
        assert _parse_rx_float("+1.75", "sph") == pytest.approx(1.75)

    def test_valid_cyl(self):
        assert _parse_rx_float("-1.50", "cyl") == pytest.approx(-1.50)

    def test_valid_add(self):
        assert _parse_rx_float("1.50", "add") == pytest.approx(1.50)

    def test_sph_below_minimum(self):
        with pytest.raises(ValueError, match="SPH"):
            _parse_rx_float("-21.00", "sph")

    def test_sph_above_maximum(self):
        with pytest.raises(ValueError, match="SPH"):
            _parse_rx_float("+20.25", "sph")

    def test_cyl_out_of_range(self):
        with pytest.raises(ValueError, match="CYL"):
            _parse_rx_float("-6.25", "cyl")

    def test_add_below_minimum(self):
        with pytest.raises(ValueError, match="ADD"):
            _parse_rx_float("0.50", "add")

    def test_add_above_maximum(self):
        with pytest.raises(ValueError, match="ADD"):
            _parse_rx_float("3.75", "add")

    def test_non_025_step_rejected(self):
        with pytest.raises(ValueError, match="0.25"):
            _parse_rx_float("-1.30", "sph")

    def test_025_step_ok(self):
        assert _parse_rx_float("-1.25", "sph") == pytest.approx(-1.25)

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            _parse_rx_float("abc", "sph")


# ---------------------------------------------------------------------------
# _parse_axis unit tests
# ---------------------------------------------------------------------------


class TestParseAxis:
    def test_none_returns_none(self):
        assert _parse_axis(None) is None

    def test_empty_returns_none(self):
        assert _parse_axis("") is None

    def test_valid_axis(self):
        assert _parse_axis("90") == 90
        assert _parse_axis("180") == 180
        assert _parse_axis("1") == 1

    def test_out_of_range_high(self):
        with pytest.raises(ValueError, match="180"):
            _parse_axis("181")

    def test_zero_returns_none(self):
        # 0 is a sentinel "no axis" on many devices; mapped to None.
        assert _parse_axis("0") is None

    def test_non_integer_string(self):
        with pytest.raises(ValueError):
            _parse_axis("abc")

    def test_float_truncated(self):
        # Some devices export "90.0" -- should be accepted as int.
        assert _parse_axis("90.0") == 90


# ---------------------------------------------------------------------------
# parse_device_csv -- Format A (Topcon/Nidek)
# ---------------------------------------------------------------------------


class TestFormatA:
    def test_single_row(self):
        csv_bytes = _topcon_csv(("-1.25", "-0.50", "90", "1.50", "+0.75", "0", "", ""))
        result = parse_device_csv(csv_bytes)
        assert result.format_detected == "TOPCON_NIDEK_COLUMN"
        assert result.right_eye.sph == pytest.approx(-1.25)
        assert result.right_eye.cyl == pytest.approx(-0.50)
        assert result.right_eye.axis == 90
        assert result.right_eye.add == pytest.approx(1.50)
        assert result.left_eye.sph == pytest.approx(0.75)
        assert result.left_eye.cyl is None
        assert result.left_eye.add is None
        assert result.raw_row_count == 1
        assert result.warnings == []

    def test_multiple_rows_uses_last(self):
        csv_bytes = _topcon_csv(
            ("-1.00", "-0.25", "85", "", "+0.50", "0", "", ""),
            ("-1.25", "-0.50", "90", "1.50", "+0.75", "0", "", ""),
        )
        result = parse_device_csv(csv_bytes)
        assert result.raw_row_count == 2
        # Last row used
        assert result.right_eye.sph == pytest.approx(-1.25)
        assert len(result.warnings) == 1
        assert "last row" in result.warnings[0]

    def test_plano_eye(self):
        csv_bytes = _topcon_csv(("DS", "DS", "0", "", "DS", "DS", "0", ""))
        result = parse_device_csv(csv_bytes)
        assert result.right_eye.sph is None
        assert result.right_eye.cyl is None
        assert result.right_eye.axis is None

    def test_out_of_range_sph_raises_422(self):
        csv_bytes = _topcon_csv(("-25.00", "0", "90", "", "0", "0", "90", ""))
        with pytest.raises(HTTPException) as exc_info:
            parse_device_csv(csv_bytes)
        assert exc_info.value.status_code == 422
        assert "SPH" in exc_info.value.detail

    def test_case_insensitive_headers(self):
        # Some devices lowercase the header row.
        header = "date,time,id,r-sph,r-cyl,r-axis,r-add,l-sph,l-cyl,l-axis,l-add"
        row = "2026-01-01,12:00:00,P1,-1.25,-0.50,90,,+0.75,0,,"
        csv_bytes = (header + "\n" + row).encode("utf-8")
        result = parse_device_csv(csv_bytes)
        assert result.format_detected == "TOPCON_NIDEK_COLUMN"
        assert result.right_eye.sph == pytest.approx(-1.25)


# ---------------------------------------------------------------------------
# parse_device_csv -- Format B (Huvitz/Zeiss)
# ---------------------------------------------------------------------------


class TestFormatB:
    def test_od_os_labels(self):
        csv_bytes = _huvitz_csv(
            ("OD", "-1.25", "-0.50", "90", "1.50"),
            ("OS", "+0.75", "0", "", ""),
        )
        result = parse_device_csv(csv_bytes)
        assert result.format_detected == "HUVITZ_ZEISS_PER_EYE"
        assert result.right_eye.sph == pytest.approx(-1.25)
        assert result.right_eye.add == pytest.approx(1.50)
        assert result.left_eye.sph == pytest.approx(0.75)

    def test_r_l_labels(self):
        csv_bytes = _huvitz_csv(
            ("R", "-2.00", "-1.00", "80", ""),
            ("L", "-1.75", "-0.75", "95", ""),
        )
        result = parse_device_csv(csv_bytes)
        assert result.right_eye.sph == pytest.approx(-2.00)
        assert result.left_eye.sph == pytest.approx(-1.75)

    def test_right_left_labels(self):
        csv_bytes = _huvitz_csv(
            ("RIGHT", "-1.00", "0", "", ""),
            ("LEFT", "-0.75", "0", "", ""),
        )
        result = parse_device_csv(csv_bytes)
        assert result.right_eye.sph == pytest.approx(-1.00)
        assert result.left_eye.sph == pytest.approx(-0.75)

    def test_missing_left_eye_raises_422(self):
        csv_bytes = _huvitz_csv(("R", "-1.25", "-0.50", "90", ""))
        with pytest.raises(HTTPException) as exc_info:
            parse_device_csv(csv_bytes)
        assert exc_info.value.status_code == 422

    def test_multiple_right_uses_last(self):
        csv_bytes = _huvitz_csv(
            ("R", "-1.00", "0", "", ""),
            ("R", "-1.25", "-0.50", "90", ""),
            ("L", "+0.50", "0", "", ""),
        )
        result = parse_device_csv(csv_bytes)
        assert result.right_eye.sph == pytest.approx(-1.25)
        assert any("right-eye" in w.lower() for w in result.warnings)

    def test_out_of_range_axis_raises_422(self):
        csv_bytes = _huvitz_csv(
            ("R", "-1.25", "-0.50", "200", ""),
            ("L", "+0.50", "0", "", ""),
        )
        with pytest.raises(HTTPException) as exc_info:
            parse_device_csv(csv_bytes)
        assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# parse_device_csv -- structural / encoding edge cases
# ---------------------------------------------------------------------------


class TestParseEdgeCases:
    def test_empty_bytes_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            parse_device_csv(b"")
        assert exc_info.value.status_code == 400
        assert "empty" in exc_info.value.detail.lower()

    def test_header_only_no_data_raises_400(self):
        csv_bytes = b"R-SPH,R-CYL,R-AXIS,L-SPH,L-CYL,L-AXIS"
        with pytest.raises(HTTPException) as exc_info:
            parse_device_csv(csv_bytes)
        assert exc_info.value.status_code == 400

    def test_unknown_format_raises_400(self):
        csv_bytes = _csv("foo,bar,baz", "1,2,3")
        with pytest.raises(HTTPException) as exc_info:
            parse_device_csv(csv_bytes)
        assert exc_info.value.status_code == 400
        assert "Unrecognised" in exc_info.value.detail

    def test_latin1_encoded_file(self):
        # Simulates a device that exports Latin-1.
        header = "R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
        row = "-1.25,-0.50,90,,+0.75,0,,"
        content = (header + "\n" + row).encode("latin-1")
        result = parse_device_csv(content)
        assert result.right_eye.sph == pytest.approx(-1.25)

    def test_whitespace_padded_values(self):
        header = "R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
        row = " -1.25 , -0.50 , 90 , , +0.75 , 0 , , "
        csv_bytes = (header + "\n" + row).encode("utf-8")
        result = parse_device_csv(csv_bytes)
        assert result.right_eye.sph == pytest.approx(-1.25)
        assert result.right_eye.axis == 90


# ---------------------------------------------------------------------------
# Endpoint integration test (no DB required -- uses TestClient)
# ---------------------------------------------------------------------------


def _make_token(roles: list[str], store_id: str = "BV-TEST-01") -> str:
    """Mint a signed JWT the TestClient can present as Bearer."""
    import datetime
    import jwt

    secret = os.environ.get("JWT_SECRET_KEY", "test")
    payload = {
        "user_id": "test-user-001",
        "username": "testuser",
        "full_name": "Test User",
        "roles": roles,
        "activeRole": roles[0] if roles else "OPTOMETRIST",
        "active_store_id": store_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=8),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class TestEndpoint:
    def test_unauthenticated_returns_401(self, client):
        response = client.post("/api/v1/clinical/device-import")
        # FastAPI returns 422 (missing file param) before 401 if no auth at all;
        # with an invalid/missing token on a proper request expect 401.
        # We send a real CSV to distinguish auth failure from param failure.
        header = "R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
        row = "-1.25,-0.50,90,,+0.75,0,,"
        csv_bytes = (header + "\n" + row).encode("utf-8")
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", csv_bytes, "text/csv")},
            # No Authorization header
        )
        assert response.status_code in (401, 403)

    def test_wrong_role_returns_403(self, client):
        token = _make_token(["CASHIER"])
        header = "R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
        row = "-1.25,-0.50,90,,+0.75,0,,"
        csv_bytes = (header + "\n" + row).encode("utf-8")
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", csv_bytes, "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_optometrist_can_import_topcon(self, client):
        token = _make_token(["OPTOMETRIST"])
        header = "R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
        row = "-1.25,-0.50,90,1.50,+0.75,0,,"
        csv_bytes = (header + "\n" + row).encode("utf-8")
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", csv_bytes, "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["format_detected"] == "TOPCON_NIDEK_COLUMN"
        assert data["right_eye"]["sph"] == pytest.approx(-1.25)
        assert data["right_eye"]["axis"] == 90
        assert data["right_eye"]["add"] == pytest.approx(1.50)
        assert data["left_eye"]["sph"] == pytest.approx(0.75)

    def test_optometrist_can_import_huvitz(self, client):
        token = _make_token(["OPTOMETRIST"])
        header = "Eye,SPH,CYL,AXIS,ADD"
        rows = "R,-1.25,-0.50,90,\nL,+0.75,0,,"
        csv_bytes = (header + "\n" + rows).encode("utf-8")
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", csv_bytes, "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["format_detected"] == "HUVITZ_ZEISS_PER_EYE"
        assert data["right_eye"]["cyl"] == pytest.approx(-0.50)

    def test_out_of_range_returns_422(self, client):
        token = _make_token(["OPTOMETRIST"])
        header = "R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
        row = "-25.00,-0.50,90,,+0.75,0,,"
        csv_bytes = (header + "\n" + row).encode("utf-8")
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", csv_bytes, "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    def test_empty_file_returns_400(self, client):
        token = _make_token(["OPTOMETRIST"])
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", b"", "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400

    def test_unknown_format_returns_400(self, client):
        token = _make_token(["OPTOMETRIST"])
        csv_bytes = b"foo,bar\n1,2"
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", csv_bytes, "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert "Unrecognised" in response.json()["detail"]

    def test_store_manager_role_allowed(self, client):
        token = _make_token(["STORE_MANAGER"])
        header = "R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
        row = "-1.00,-0.25,80,,+0.50,0,,"
        csv_bytes = (header + "\n" + row).encode("utf-8")
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", csv_bytes, "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    def test_admin_role_allowed(self, client):
        token = _make_token(["ADMIN"])
        header = "R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
        row = "-1.00,-0.25,80,,+0.50,0,,"
        csv_bytes = (header + "\n" + row).encode("utf-8")
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", csv_bytes, "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    def test_workshop_staff_role_forbidden(self, client):
        token = _make_token(["WORKSHOP_STAFF"])
        header = "R-SPH,R-CYL,R-AXIS,R-ADD,L-SPH,L-CYL,L-AXIS,L-ADD"
        row = "-1.00,-0.25,80,,+0.50,0,,"
        csv_bytes = (header + "\n" + row).encode("utf-8")
        response = client.post(
            "/api/v1/clinical/device-import",
            files={"file": ("device.csv", csv_bytes, "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
