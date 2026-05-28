"""
IMS 2.0 - Editable HSN->GST master resolver tests
=================================================
Covers the DB-override layer added on top of the static GST_CATEGORY_TABLE
(PR #251) in api/services/gst_rates.py:
  - resolve_gst_rate falls back to the static table when the master is empty
  - editable master overrides by category_hint and by exact HSN
  - category-spelling normalization
  - never raises on junk input
  - idempotent seeder against a fake collection
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import gst_rates  # noqa: E402
from api.services.gst_rates import resolve_gst_rate  # noqa: E402


def _empty_lookup(monkeypatch):
    monkeypatch.setattr(
        gst_rates, "_load_lookup", lambda: {"by_hsn": {}, "by_cat": {}}
    )


# ---- fallback to the static #251 table when the master is empty -------------
def test_fallback_contact_lens_5pct(monkeypatch):
    _empty_lookup(monkeypatch)
    for cat in ("CONTACT_LENS", "CONTACT_LENSES", "COLORED_CONTACT_LENS", "COLOUR_CONTACTS"):
        assert resolve_gst_rate(category=cat) == 5.0


def test_fallback_corrective_optical_5pct(monkeypatch):
    _empty_lookup(monkeypatch)
    for cat in ("FRAME", "OPTICAL_LENS", "LENS", "RX_LENSES", "READING_GLASSES", "SPECTACLE"):
        assert resolve_gst_rate(category=cat) == 5.0


def test_fallback_non_corrective_18pct(monkeypatch):
    _empty_lookup(monkeypatch)
    for cat in ("SUNGLASS", "SUNGLASSES", "WATCH", "SMARTWATCH", "ACCESSORIES", "SERVICES"):
        assert resolve_gst_rate(category=cat) == 18.0


def test_fallback_unknown_category_defaults_to_5(monkeypatch):
    # Optical-dominant fallback: the static GST_CATEGORY_TABLE default moved
    # 18% -> 5% on 2026-05-28 (QA: uncategorized product billed at 18%), so an
    # unknown category now resolves to 5% via the static table fall-through.
    _empty_lookup(monkeypatch)
    assert resolve_gst_rate(category="WIDGET") == 5.0
    assert resolve_gst_rate(category=None) == 5.0
    assert resolve_gst_rate() == 5.0


def test_category_spelling_variants_normalize(monkeypatch):
    _empty_lookup(monkeypatch)
    assert resolve_gst_rate(category="contact-lenses") == 5.0
    assert resolve_gst_rate(category="Colour Contacts") == 5.0
    assert resolve_gst_rate(category="eyeglass_frame") == 5.0


# ---- editable master overrides the static table ----------------------------
def test_master_override_by_category(monkeypatch):
    monkeypatch.setattr(
        gst_rates, "_load_lookup",
        lambda: {"by_hsn": {}, "by_cat": {"CONTACT_LENS": 12.0}},
    )
    assert resolve_gst_rate(category="CONTACT_LENSES") == 12.0   # override
    assert resolve_gst_rate(category="SUNGLASSES") == 18.0       # static fallback


def test_exact_hsn_beats_category(monkeypatch):
    monkeypatch.setattr(
        gst_rates, "_load_lookup",
        lambda: {"by_hsn": {"900490": 18.0}, "by_cat": {"SPECTACLE": 5.0}},
    )
    assert resolve_gst_rate(hsn_code="900490", category="SPECTACLE") == 18.0
    assert resolve_gst_rate(category="SPECTACLE") == 5.0


def test_resolver_never_raises(monkeypatch):
    _empty_lookup(monkeypatch)
    assert isinstance(resolve_gst_rate(hsn_code=12345, category=999), float)  # type: ignore[arg-type]


# ---- idempotent seeder against a fake collection ---------------------------
class _FakeColl:
    def __init__(self):
        self.docs = []

    def find_one(self, q):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()


def test_seed_is_idempotent(monkeypatch):
    fake = _FakeColl()
    monkeypatch.setattr(gst_rates, "_get_collection", lambda: fake)
    monkeypatch.setattr(gst_rates, "invalidate_cache", lambda: None)

    first = gst_rates.seed_hsn_gst_master()
    assert first == len(gst_rates.HSN_GST_SEED)
    cl = fake.find_one({"hsn_code": "900130"})
    assert cl is not None and cl["gst_rate"] == 5.0

    cl["gst_rate"] = 12.0  # simulate an owner edit
    second = gst_rates.seed_hsn_gst_master()
    assert second == 0
    assert fake.find_one({"hsn_code": "900130"})["gst_rate"] == 12.0
