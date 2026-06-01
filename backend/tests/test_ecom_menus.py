"""
Tests for the BVI Phase 3 Menus / Mega-Menu module (FLAGSHIP #2).

Three layers, mirroring test_ecom_collections.py's style:
  1. EcomMenuRepository round-trips via the in-memory MockCollection (no live
     Mongo): create / get_by_handle / list filters / update (incl. whole-tree
     replace) / delete.
  2. The embedded recursive item tree: add a nested node, move a node to a new
     parent (+ cycle guard), remove a node + its subtree, patch a node's fields,
     and the position-renumber invariant after every mutation.
  3. Router wiring: every menus route is catalogued in rbac_policy.POLICY with
     the ecom role set, the literal reorder/move routes resolve over the
     {item_id} param route, check_access allow/deny, and the live role gate
     (SALES_STAFF 403 + fail-soft list without DB) -- none need a DB.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_ecom_menus.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from database.repositories.ecom_menu_repository import (  # noqa: E402
    EcomMenuRepository,
)
from api.services import rbac_policy as rbac  # noqa: E402


# ===========================================================================
# Layer 1 -- repository CRUD round-trips (MockCollection, no live Mongo)
# ===========================================================================

@pytest.fixture
def repo():
    return EcomMenuRepository(MockCollection("ecom_menus"))


def test_create_then_get_by_handle_roundtrip(repo):
    """A menu created via the repo is read back by handle with sane defaults
    (active, not default, dirty-from-birth, empty item tree)."""
    created = repo.create({"title": "Main Menu", "handle": "main-menu"})
    assert created is not None
    assert created["menu_id"]
    assert created["handle"] == "main-menu"
    # Defaults.
    assert created["active"] is True
    assert created["is_default"] is False
    assert created["items"] == []
    # PUSH-DARK: born dirty (nothing pushed to Shopify yet).
    assert created["locally_modified"] is True
    assert "created_at" in created and "updated_at" in created

    fetched = repo.get_by_handle("main-menu")
    assert fetched is not None
    assert fetched["menu_id"] == created["menu_id"]


def test_create_requires_handle(repo):
    """handle is the unique slug + idempotent key; a row without it is refused."""
    assert repo.create({"title": "No Handle"}) is None
    assert repo.create({}) is None
    assert repo.count() == 0


def test_get_missing_returns_none(repo):
    assert repo.get_by_handle("nope") is None
    assert repo.get_by_handle("") is None
    assert repo.get_by_id("nope") is None
    assert repo.get_by_id("") is None


def test_create_with_initial_tree_normalizes_ids_and_positions(repo):
    """An inbound items tree gets server-minted ids, parent links, and 0-based
    sibling positions on create."""
    created = repo.create(
        {
            "title": "Nav",
            "handle": "nav",
            "items": [
                {"title": "Eyeglasses", "item_type": "COLLECTION", "children": [
                    {"title": "Men", "item_type": "COLLECTION"},
                    {"title": "Women", "item_type": "COLLECTION"},
                ]},
                {"title": "Sunglasses", "item_type": "COLLECTION"},
            ],
        }
    )
    items = created["items"]
    assert [i["title"] for i in items] == ["Eyeglasses", "Sunglasses"]
    assert [i["position"] for i in items] == [0, 1]
    # Every node has an id + correct parent_id.
    eyeglasses = items[0]
    assert eyeglasses["id"]
    assert eyeglasses["parent_id"] is None
    kids = eyeglasses["children"]
    assert [k["title"] for k in kids] == ["Men", "Women"]
    assert [k["position"] for k in kids] == [0, 1]
    assert all(k["parent_id"] == eyeglasses["id"] for k in kids)
    # 3 nodes total (Eyeglasses + 2 kids) + Sunglasses = 4.
    assert EcomMenuRepository.count_nodes(items) == 4


def test_list_filters_by_active_and_default(repo):
    repo.create({"title": "A", "handle": "a", "active": True, "is_default": True})
    repo.create({"title": "B", "handle": "b", "active": False, "is_default": False})

    # Unfiltered -> both, ordered by handle (a before b).
    assert [m["handle"] for m in repo.list()] == ["a", "b"]
    assert [m["handle"] for m in repo.list(active=True)] == ["a"]
    assert [m["handle"] for m in repo.list(active=False)] == ["b"]
    assert [m["handle"] for m in repo.list(is_default=True)] == ["a"]


def test_update_patches_and_marks_dirty(repo):
    created = repo.create({"title": "Old", "handle": "h1", "locally_modified": False})
    mid = created["menu_id"]
    assert repo.get_by_id(mid)["locally_modified"] is False

    assert repo.update(mid, {"title": "New", "active": False}) is True
    doc = repo.get_by_id(mid)
    assert doc["title"] == "New"
    assert doc["active"] is False
    # Any update re-marks dirty for the Phase-5 push queue.
    assert doc["locally_modified"] is True
    # Identity / created_at are immutable through update().
    assert repo.update(mid, {"menu_id": "hacked", "created_at": "x"}) is False
    assert repo.get_by_id(mid)["menu_id"] == mid


def test_update_replaces_whole_tree(repo):
    mid = repo.create({"title": "M", "handle": "m"})["menu_id"]
    repo.update(mid, {"items": [{"title": "X"}, {"title": "Y"}]})
    items = repo.get_by_id(mid)["items"]
    assert [i["title"] for i in items] == ["X", "Y"]
    assert [i["position"] for i in items] == [0, 1]
    # Replacing again wipes the prior tree.
    repo.update(mid, {"items": [{"title": "Z"}]})
    items = repo.get_by_id(mid)["items"]
    assert [i["title"] for i in items] == ["Z"]


def test_delete_removes_menu(repo):
    mid = repo.create({"title": "Doomed", "handle": "doomed"})["menu_id"]
    assert repo.delete(mid) is True
    assert repo.get_by_id(mid) is None
    # Deleting again is a no-op False (already gone).
    assert repo.delete(mid) is False


# ===========================================================================
# Layer 2 -- embedded recursive item tree (add / move / remove / patch)
# ===========================================================================

def _seed_tree(repo):
    """Create a menu with a known 2-level tree and return (menu_id, ids).

    Top-level:  Eyeglasses(EG) -> [Men(M), Women(W)] , Sunglasses(SG)
    """
    created = repo.create(
        {
            "title": "Nav",
            "handle": "nav",
            "items": [
                {"title": "Eyeglasses", "children": [
                    {"title": "Men"},
                    {"title": "Women"},
                ]},
                {"title": "Sunglasses"},
            ],
        }
    )
    mid = created["menu_id"]
    items = created["items"]
    eg = items[0]
    ids = {
        "EG": eg["id"],
        "M": eg["children"][0]["id"],
        "W": eg["children"][1]["id"],
        "SG": items[1]["id"],
    }
    return mid, ids


def test_add_item_top_level_appends_and_renumbers(repo):
    mid, _ = _seed_tree(repo)
    doc = repo.add_item(mid, {"title": "Contact Lenses", "item_type": "COLLECTION"})
    assert doc is not None
    top = doc["items"]
    assert [i["title"] for i in top] == ["Eyeglasses", "Sunglasses", "Contact Lenses"]
    assert [i["position"] for i in top] == [0, 1, 2]
    # The new node carries a server-minted id + the linkage fields.
    assert top[2]["id"]
    assert top[2]["item_type"] == "COLLECTION"
    assert top[2]["parent_id"] is None


def test_add_item_under_parent_with_position(repo):
    mid, ids = _seed_tree(repo)
    # Insert "Kids" at position 1 under Eyeglasses (between Men and Women).
    doc = repo.add_item(mid, {"title": "Kids"}, parent_id=ids["EG"], position=1)
    eg = doc["items"][0]
    assert [k["title"] for k in eg["children"]] == ["Men", "Kids", "Women"]
    assert [k["position"] for k in eg["children"]] == [0, 1, 2]
    assert all(k["parent_id"] == ids["EG"] for k in eg["children"])


def test_add_item_unknown_parent_returns_none(repo):
    mid, _ = _seed_tree(repo)
    assert repo.add_item(mid, {"title": "X"}, parent_id="no-such-id") is None
    assert repo.add_item("no-such-menu", {"title": "X"}) is None


def test_move_item_reparents_and_renumbers(repo):
    mid, ids = _seed_tree(repo)
    # Move Women out from under Eyeglasses to top level, at position 0.
    doc = repo.move_item(mid, ids["W"], new_parent_id=None, position=0)
    assert doc is not None
    top = doc["items"]
    assert [i["title"] for i in top] == ["Women", "Eyeglasses", "Sunglasses"]
    assert [i["position"] for i in top] == [0, 1, 2]
    # Women now top-level (parent_id None); Eyeglasses keeps only Men.
    women = top[0]
    assert women["parent_id"] is None
    eg = top[1]
    assert [k["title"] for k in eg["children"]] == ["Men"]
    assert eg["children"][0]["position"] == 0


def test_move_item_into_another_subtree(repo):
    mid, ids = _seed_tree(repo)
    # Move Sunglasses under Eyeglasses (append).
    doc = repo.move_item(mid, ids["SG"], new_parent_id=ids["EG"])
    top = doc["items"]
    assert [i["title"] for i in top] == ["Eyeglasses"]  # SG no longer top-level
    eg = top[0]
    assert [k["title"] for k in eg["children"]] == ["Men", "Women", "Sunglasses"]
    assert [k["position"] for k in eg["children"]] == [0, 1, 2]
    assert eg["children"][2]["parent_id"] == ids["EG"]


def test_move_item_cycle_guard(repo):
    mid, ids = _seed_tree(repo)
    # Can't move Eyeglasses under its own child Men (would orphan the subtree).
    assert repo.move_item(mid, ids["EG"], new_parent_id=ids["M"]) is None
    # Can't move a node under itself.
    assert repo.move_item(mid, ids["EG"], new_parent_id=ids["EG"]) is None
    # Unknown node / unknown parent -> None.
    assert repo.move_item(mid, "no-node", new_parent_id=None) is None
    assert repo.move_item(mid, ids["M"], new_parent_id="no-parent") is None
    # The tree is unchanged after all the rejected moves.
    top = repo.get_by_id(mid)["items"]
    assert [i["title"] for i in top] == ["Eyeglasses", "Sunglasses"]
    assert [k["title"] for k in top[0]["children"]] == ["Men", "Women"]


def test_remove_item_drops_subtree_and_renumbers(repo):
    mid, ids = _seed_tree(repo)
    # Remove Eyeglasses -> its children Men/Women go with it.
    doc = repo.remove_item(mid, ids["EG"])
    top = doc["items"]
    assert [i["title"] for i in top] == ["Sunglasses"]
    assert top[0]["position"] == 0
    assert EcomMenuRepository.count_nodes(top) == 1
    # Removing an absent node is an idempotent no-op success.
    doc2 = repo.remove_item(mid, ids["EG"])
    assert [i["title"] for i in doc2["items"]] == ["Sunglasses"]


def test_remove_child_renumbers_siblings(repo):
    mid, ids = _seed_tree(repo)
    doc = repo.remove_item(mid, ids["M"])  # remove Men
    eg = doc["items"][0]
    assert [k["title"] for k in eg["children"]] == ["Women"]
    assert eg["children"][0]["position"] == 0


def test_update_item_patches_fields_in_place(repo):
    mid, ids = _seed_tree(repo)
    doc = repo.update_item(
        mid, ids["SG"],
        {"title": "Shades", "badge_text": "SALE", "badge_color": "#ff0000",
         "pinned_to_top": True, "item_type": "COLLECTION"},
    )
    sg = next(i for i in doc["items"] if i["id"] == ids["SG"])
    assert sg["title"] == "Shades"
    assert sg["badge_text"] == "SALE"
    assert sg["badge_color"] == "#ff0000"
    assert sg["pinned_to_top"] is True
    assert sg["item_type"] == "COLLECTION"
    # Structural fields untouched.
    assert sg["parent_id"] is None
    assert sg["position"] == 1


def test_update_item_unknown_returns_none(repo):
    mid, _ = _seed_tree(repo)
    assert repo.update_item(mid, "no-node", {"title": "X"}) is None
    assert repo.update_item("no-menu", "no-node", {"title": "X"}) is None


def test_tree_ops_unknown_menu_return_none(repo):
    assert repo.move_item("no-menu", "x", new_parent_id=None) is None
    assert repo.remove_item("no-menu", "x") is None


def test_count_nodes_pure_helper():
    tree = [
        {"id": "a", "children": [{"id": "b", "children": []}, {"id": "c", "children": [{"id": "d"}]}]},
        {"id": "e"},
    ]
    assert EcomMenuRepository.count_nodes(tree) == 5
    assert EcomMenuRepository.count_nodes([]) == 0


# ===========================================================================
# Layer 3 -- router RBAC catalogue + route resolution + role gate (no DB)
# ===========================================================================

_MENU_ROUTES = [
    ("GET", "/api/v1/online-store/menus"),
    ("POST", "/api/v1/online-store/menus"),
    ("GET", "/api/v1/online-store/menus/{menu_id}"),
    ("PUT", "/api/v1/online-store/menus/{menu_id}"),
    ("DELETE", "/api/v1/online-store/menus/{menu_id}"),
    ("POST", "/api/v1/online-store/menus/{menu_id}/items"),
    ("PUT", "/api/v1/online-store/menus/{menu_id}/items/reorder"),
    ("PUT", "/api/v1/online-store/menus/{menu_id}/items/{item_id}/move"),
    ("PUT", "/api/v1/online-store/menus/{menu_id}/items/{item_id}"),
    ("DELETE", "/api/v1/online-store/menus/{menu_id}/items/{item_id}"),
]

_ECOM_SET = {"ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"}


def test_every_menu_route_catalogued_with_ecom_roles():
    for method, path in _MENU_ROUTES:
        entry = rbac.policy_for(method, path)
        assert entry is not None, f"{method} {path} not catalogued in rbac_policy"
        assert set(entry["allowed"]) == _ECOM_SET, f"{method} {path} -> {entry['allowed']}"


def test_reorder_and_move_literals_beat_item_id_param():
    """The literal .../items/reorder + .../items/{item_id}/move must resolve to
    their own routes -- not be shadowed by the .../items/{item_id} param route."""
    reorder = rbac.policy_for("PUT", "/api/v1/online-store/menus/M1/items/reorder")
    assert reorder is not None
    assert reorder["path"].endswith("/items/reorder")

    move = rbac.policy_for("PUT", "/api/v1/online-store/menus/M1/items/I9/move")
    assert move is not None
    assert move["path"].endswith("/items/{item_id}/move")

    # A bare item id still resolves to the param route (PUT + DELETE).
    item_put = rbac.policy_for("PUT", "/api/v1/online-store/menus/M1/items/I9")
    assert item_put is not None and item_put["path"].endswith("/items/{item_id}")
    item_del = rbac.policy_for("DELETE", "/api/v1/online-store/menus/M1/items/I9")
    assert item_del is not None and item_del["path"].endswith("/items/{item_id}")


def test_check_access_allows_ecom_roles_denies_others():
    path = "/api/v1/online-store/menus"
    for role in ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER"):
        assert rbac.check_access("POST", path, [role]) is True, role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", "ACCOUNTANT"):
        assert rbac.check_access("POST", path, [role]) is False, role


def test_live_role_gate_forbids_sales_staff(client, staff_headers):
    """SALES_STAFF is outside the ecom set -> 403 before the handler (no DB needed)."""
    r = client.get("/api/v1/online-store/menus", headers=staff_headers)
    assert r.status_code == 403, r.text


def test_live_list_is_failsoft_without_db(client, auth_headers):
    """GET list returns 200 with a well-formed envelope even when no DB is
    connected (db_connected False -> empty list, never a 500)."""
    r = client.get("/api/v1/online-store/menus", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "menus" in body and "count" in body
    assert isinstance(body["menus"], list)
