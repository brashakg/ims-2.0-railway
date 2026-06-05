"""
Product-image auto-edit scaffolding (Online Store design queue)
===============================================================
Covers the provider/storage seams + the /auto-edit route's SAFE-BY-DESIGN
contract: idempotent (no re-edit of APPROVED, no-op if already in REVIEW),
fail-soft (no provider / provider error keeps the RAW image, never 500s), and
the happy path (RAW -> editor -> storage -> attach_edited -> REVIEW). No creds
or network -- the editor/storage/repo are faked.
"""

import asyncio

import api.services.image_editor as ied
import api.services.object_storage as ost
from api.routers import online_store_images as imod

USER = {"user_id": "u1", "roles": ["ADMIN"]}


def _run(coro):
    return asyncio.run(coro)


# --- fakes ------------------------------------------------------------------

class _FakeImgRepo:
    def __init__(self, img):
        self.img = dict(img)

    def get_by_id(self, _iid):
        return dict(self.img)

    def set_status(self, _iid, target, by=None):  # noqa: ARG002
        self.img["status"] = target
        return dict(self.img)

    def attach_edited(self, _iid, url, by=None):  # noqa: ARG002
        if self.img.get("status") != "IN_PROGRESS":
            return None  # mirrors the real repo: requires IN_PROGRESS
        self.img.update(status="REVIEW", edited_url=url, kind="EDITED")
        return dict(self.img)


class _FakeEditor:
    name = "fake"

    def available(self):
        return True

    async def edit(self, raw, spec):  # noqa: ARG002
        return b"EDITED_PNG_BYTES"


class _DisabledEditor:
    name = "disabled"

    def available(self):
        return False

    async def edit(self, raw, spec):  # noqa: ARG002
        raise RuntimeError("disabled")


class _FakeStorage:
    name = "fakestore"

    def available(self):
        return True

    def put(self, key, data, content_type="image/png"):  # noqa: ARG002
        return f"https://cdn.example/{key}"


def _wire(monkeypatch, repo, editor):
    monkeypatch.setattr(imod, "_require_repo", lambda: repo)
    monkeypatch.setattr(ied, "get_image_editor", lambda: editor)
    monkeypatch.setattr(ost, "get_object_storage", lambda: _FakeStorage())

    async def _fetch(_url):
        return b"raw-bytes"

    monkeypatch.setattr(imod, "_fetch_image_bytes", _fetch)


# --- route: happy path + idempotency + fail-soft ----------------------------

def test_auto_edit_success_moves_to_review(monkeypatch):
    repo = _FakeImgRepo(
        {"image_id": "I1", "product_id": "P1", "url": "https://x/raw.png", "status": "QUEUED"}
    )
    _wire(monkeypatch, repo, _FakeEditor())
    res = _run(imod.auto_edit_image("I1", current_user=USER))
    assert res["auto_edit"] == "ok"
    assert res["provider"] == "fake" and res["storage"] == "fakestore"
    assert res["image"]["status"] == "REVIEW"
    assert res["image"]["edited_url"] == "https://cdn.example/P1/I1.png"


def test_auto_edit_approved_is_409(monkeypatch):
    import pytest
    from fastapi import HTTPException

    repo = _FakeImgRepo({"image_id": "I1", "url": "x", "status": "APPROVED"})
    monkeypatch.setattr(imod, "_require_repo", lambda: repo)
    with pytest.raises(HTTPException) as ei:
        _run(imod.auto_edit_image("I1", current_user=USER))
    assert ei.value.status_code == 409


def test_auto_edit_already_in_review_is_noop(monkeypatch):
    repo = _FakeImgRepo(
        {"image_id": "I1", "url": "x", "status": "REVIEW", "edited_url": "https://cdn/e.png"}
    )
    monkeypatch.setattr(imod, "_require_repo", lambda: repo)
    res = _run(imod.auto_edit_image("I1", current_user=USER))
    assert res["auto_edit"] == "skipped"


def test_auto_edit_no_provider_keeps_raw(monkeypatch):
    repo = _FakeImgRepo({"image_id": "I1", "url": "x", "status": "QUEUED"})
    monkeypatch.setattr(imod, "_require_repo", lambda: repo)
    monkeypatch.setattr(ied, "get_image_editor", lambda: _DisabledEditor())
    res = _run(imod.auto_edit_image("I1", current_user=USER))
    assert res["auto_edit"] == "skipped"
    assert res["image"]["status"] == "QUEUED"  # RAW kept, unchanged


def test_auto_edit_provider_error_is_failsoft(monkeypatch):
    class _BoomEditor:
        name = "boom"

        def available(self):
            return True

        async def edit(self, raw, spec):  # noqa: ARG002
            raise RuntimeError("provider down")

    repo = _FakeImgRepo({"image_id": "I1", "product_id": "P1", "url": "x", "status": "QUEUED"})
    _wire(monkeypatch, repo, _BoomEditor())
    res = _run(imod.auto_edit_image("I1", current_user=USER))
    assert res["auto_edit"] == "failed"
    assert "provider down" in res["reason"]


# --- provider / storage seams ----------------------------------------------

def test_factory_disabled_when_unconfigured(monkeypatch):
    monkeypatch.delenv("IMAGE_EDIT_PROVIDER", raising=False)
    monkeypatch.delenv("PHOTOROOM_API_KEY", raising=False)
    monkeypatch.setattr(ied, "_rembg_editor", lambda: None)
    ed = ied.get_image_editor()
    assert ed.name == "disabled" and ed.available() is False


def test_photoroom_available_with_key(monkeypatch):
    monkeypatch.setenv("IMAGE_EDIT_PROVIDER", "photoroom")
    monkeypatch.setenv("PHOTOROOM_API_KEY", "key123")
    ed = ied.get_image_editor()
    assert ed.name == "photoroom" and ed.available() is True


def test_editspec_from_env_strips_hash(monkeypatch):
    monkeypatch.setenv("IMAGE_EDIT_BG_COLOR", "#000000")
    assert ied.EditSpec.from_env().background_color == "000000"


def test_local_storage_writes_and_returns_url(tmp_path):
    st = ost.LocalDiskStorage(root=str(tmp_path), base_url="/edited")
    url = st.put("P1/I1.png", b"abc", "image/png")
    assert url == "/edited/P1/I1.png"
    assert (tmp_path / "P1" / "I1.png").read_bytes() == b"abc"
