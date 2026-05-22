"use client";

import { useEffect, useMemo, useState, use as usePromise } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Loader2,
  Plus,
  RefreshCw,
  Upload,
  AlertCircle,
  CheckCircle,
  Save,
  Star,
  EyeOff,
} from "lucide-react";
import MenuItemTree, { type MenuTreeNode } from "@/components/MenuItemTree";
import MenuItemEditor, {
  type MenuItemRecord,
} from "@/components/MenuItemEditor";

interface MenuDetail {
  id: string;
  shopifyMenuId: string | null;
  handle: string;
  title: string;
  isDefault: boolean;
  active: boolean;
  locallyModified: boolean;
  lastSyncedAt: string | null;
  items: MenuTreeNode[];
  flatItems: MenuItemRecord[];
}

// Next 15 client-page params shape: a Promise that we unwrap with `use()`.
interface PageProps {
  params: Promise<{ id: string }>;
}

export default function MenuEditorPage({ params }: PageProps) {
  const { id } = usePromise(params);

  const [menu, setMenu] = useState<MenuDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [titleEdit, setTitleEdit] = useState("");
  const [activeEdit, setActiveEdit] = useState(true);
  const [savingMeta, setSavingMeta] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [statusMessage, setStatusMessage] = useState<{
    type: "ok" | "err";
    text: string;
  } | null>(null);
  const [adding, setAdding] = useState(false);

  const fetchMenu = async () => {
    setError(null);
    try {
      const res = await fetch(`/api/menus/${id}`);
      const json = await res.json();
      if (!json.success) {
        setError(json.error || "Failed to load menu");
        setMenu(null);
        return;
      }
      setMenu(json.data);
      setTitleEdit(json.data.title);
      setActiveEdit(json.data.active);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load menu");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMenu();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const selectedItem = useMemo<MenuItemRecord | null>(() => {
    if (!menu || !selectedId) return null;
    return menu.flatItems.find((i) => i.id === selectedId) || null;
  }, [menu, selectedId]);

  const handleSaveMenuMeta = async () => {
    if (!menu) return;
    setSavingMeta(true);
    setStatusMessage(null);
    try {
      const res = await fetch(`/api/menus/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: titleEdit, active: activeEdit }),
      });
      const json = await res.json();
      if (!json.success) {
        setStatusMessage({ type: "err", text: json.error || "Failed to save" });
        return;
      }
      setStatusMessage({ type: "ok", text: "Menu metadata saved." });
      await fetchMenu();
    } catch (e) {
      setStatusMessage({
        type: "err",
        text: e instanceof Error ? e.message : "Failed to save",
      });
    } finally {
      setSavingMeta(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    setStatusMessage(null);
    try {
      const res = await fetch(`/api/menus/${id}/sync`, { method: "POST" });
      const json = await res.json();
      if (!json.success) {
        setStatusMessage({ type: "err", text: json.error || "Sync failed" });
        return;
      }
      const text = json.warning
        ? `Synced ${json.itemCount} items. ${json.warning}`
        : `Synced ${json.itemCount} items from Shopify.`;
      setStatusMessage({ type: "ok", text });
      await fetchMenu();
    } catch (e) {
      setStatusMessage({
        type: "err",
        text: e instanceof Error ? e.message : "Sync failed",
      });
    } finally {
      setSyncing(false);
    }
  };

  const handlePush = async () => {
    setPushing(true);
    setStatusMessage(null);
    try {
      const res = await fetch(`/api/menus/${id}/push`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const json = await res.json();
      if (!json.success) {
        setStatusMessage({ type: "err", text: json.error || "Push failed" });
        return;
      }
      setStatusMessage({
        type: "ok",
        text: json.skipped
          ? "Menu is already in sync."
          : `Pushed ${json.itemCount} items to Shopify.`,
      });
      await fetchMenu();
    } catch (e) {
      setStatusMessage({
        type: "err",
        text: e instanceof Error ? e.message : "Push failed",
      });
    } finally {
      setPushing(false);
    }
  };

  const handleAddTopLevel = async () => {
    setAdding(true);
    try {
      const res = await fetch(`/api/menus/${id}/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "New item",
          itemType: "HTTP",
          url: "/",
        }),
      });
      const json = await res.json();
      if (!json.success) {
        setStatusMessage({ type: "err", text: json.error || "Add failed" });
        return;
      }
      setSelectedId(json.data.id);
      await fetchMenu();
    } finally {
      setAdding(false);
    }
  };

  const handleAddChild = async (parentId: string) => {
    try {
      const res = await fetch(`/api/menus/${id}/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "New child item",
          itemType: "HTTP",
          url: "/",
          parentId,
        }),
      });
      const json = await res.json();
      if (!json.success) {
        setStatusMessage({ type: "err", text: json.error || "Add failed" });
        return;
      }
      setSelectedId(json.data.id);
      await fetchMenu();
    } catch (e) {
      setStatusMessage({
        type: "err",
        text: e instanceof Error ? e.message : "Add failed",
      });
    }
  };

  const handleDeleteItem = async (itemId: string) => {
    try {
      const res = await fetch(`/api/menus/${id}/items/${itemId}`, {
        method: "DELETE",
      });
      const json = await res.json();
      if (!json.success) {
        setStatusMessage({ type: "err", text: json.error || "Delete failed" });
        return;
      }
      if (selectedId === itemId) setSelectedId(null);
      await fetchMenu();
    } catch (e) {
      setStatusMessage({
        type: "err",
        text: e instanceof Error ? e.message : "Delete failed",
      });
    }
  };

  // ─── Drag-drop handler ──────────────────────────────────
  //
  // The tree component fires this when the user drops `dragId` onto
  // `targetId` at position "before" / "after" / "inside". We
  // recompute the affected level (or two, if the parent changed) on
  // the client and POST the whole reorder set in one call. The
  // server reindexes positions inside a transaction so the tree
  // never lands in a half-applied state.
  const handleMove = async (
    dragId: string,
    targetId: string | null,
    position: "before" | "after" | "inside"
  ) => {
    if (!menu) return;
    if (!targetId || dragId === targetId) return;

    // Walk the flat list to find the dragged + target items.
    const flat = menu.flatItems;
    const target = flat.find((i) => i.id === targetId);
    if (!target) return;

    // Cycle protection — refuse moves that would put an ancestor
    // under one of its descendants. Walk up the target chain;
    // if we hit dragId we abort.
    if (position === "inside") {
      let cursor: string | null = target.parentId;
      const limit = 50;
      for (let i = 0; i < limit; i++) {
        if (!cursor) break;
        if (cursor === dragId) return; // would form a cycle
        const p: MenuItemRecord | undefined = flat.find((it) => it.id === cursor);
        cursor = p?.parentId || null;
      }
    }

    // Decide the new parent + insert index.
    let newParentId: string | null;
    let insertIndex: number;
    if (position === "inside") {
      newParentId = target.id;
      insertIndex = flat.filter((i) => i.parentId === newParentId).length;
    } else {
      newParentId = target.parentId;
      const siblings = flat
        .filter((i) => i.parentId === newParentId)
        .sort((a, b) => a.position - b.position);
      const targetIdx = siblings.findIndex((i) => i.id === targetId);
      insertIndex = position === "before" ? targetIdx : targetIdx + 1;
    }

    // Rebuild the affected sibling array, removing the dragged
    // item if it currently lives in the same parent + adjusting
    // the insert index.
    const oldParentId =
      flat.find((i) => i.id === dragId)?.parentId ?? null;

    const newParentSiblings = flat
      .filter((i) => i.parentId === newParentId && i.id !== dragId)
      .sort((a, b) => a.position - b.position);

    if (oldParentId === newParentId) {
      const oldIdx = flat
        .filter((i) => i.parentId === newParentId)
        .sort((a, b) => a.position - b.position)
        .findIndex((i) => i.id === dragId);
      if (oldIdx !== -1 && oldIdx < insertIndex) insertIndex -= 1;
    }

    const dragged = flat.find((i) => i.id === dragId);
    if (!dragged) return;

    const reorderedNew = [
      ...newParentSiblings.slice(0, insertIndex),
      dragged,
      ...newParentSiblings.slice(insertIndex),
    ];

    const updates: Array<{
      id: string;
      position: number;
      parentId: string | null;
    }> = reorderedNew.map((item, idx) => ({
      id: item.id,
      position: idx,
      parentId: newParentId,
    }));

    // If the dragged item left its old parent, also reindex the old
    // parent's siblings so positions stay 0..N without gaps.
    if (oldParentId !== newParentId) {
      const oldParentSiblings = flat
        .filter((i) => i.parentId === oldParentId && i.id !== dragId)
        .sort((a, b) => a.position - b.position);
      oldParentSiblings.forEach((item, idx) => {
        updates.push({
          id: item.id,
          position: idx,
          parentId: oldParentId,
        });
      });
    }

    try {
      const res = await fetch(`/api/menus/${id}/items/reorder`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: updates }),
      });
      const json = await res.json();
      if (!json.success) {
        setStatusMessage({ type: "err", text: json.error || "Reorder failed" });
        return;
      }
      await fetchMenu();
    } catch (e) {
      setStatusMessage({
        type: "err",
        text: e instanceof Error ? e.message : "Reorder failed",
      });
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <Loader2 className="w-6 h-6 animate-spin text-slate-500" />
      </div>
    );
  }

  if (error || !menu) {
    return (
      <div className="min-h-screen p-6 bg-slate-50">
        <div className="max-w-2xl mx-auto">
          <Link
            href="/dashboard/admin/menus"
            className="inline-flex items-center gap-1 text-sm text-blue-600 hover:underline mb-4"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to menus
          </Link>
          <div className="bg-red-50 border border-red-200 rounded p-4 text-sm text-red-800 flex items-center gap-2">
            <AlertCircle className="w-4 h-4" />
            {error || "Menu not found"}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-7xl mx-auto px-6 py-4">
        <Link
          href="/dashboard/admin/menus"
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900 mb-2"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          All menus
        </Link>

        <div className="flex items-center justify-between gap-4 flex-wrap mb-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-xl font-semibold text-slate-900 truncate">
                {menu.title}
              </h1>
              <span className="text-xs font-mono px-1.5 py-0.5 bg-slate-100 rounded text-slate-600">
                {menu.handle}
              </span>
              {menu.isDefault && (
                <span className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 bg-amber-100 text-amber-800 rounded">
                  <Star className="w-3 h-3" />
                  Default
                </span>
              )}
              {!menu.active && (
                <span className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 bg-slate-200 text-slate-700 rounded">
                  <EyeOff className="w-3 h-3" />
                  Inactive
                </span>
              )}
            </div>
            <div className="text-xs text-slate-500 mt-1">
              {menu.locallyModified ? (
                <span className="text-yellow-700">
                  Local changes not yet pushed to Shopify.
                </span>
              ) : (
                <span className="text-emerald-700">In sync with Shopify.</span>
              )}
              {menu.lastSyncedAt && (
                <span className="ml-2">
                  Last synced {new Date(menu.lastSyncedAt).toLocaleString("en-IN")}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleSync}
              disabled={syncing}
              className="inline-flex items-center gap-1 px-3 py-1.5 border border-slate-300 rounded-md text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50"
            >
              {syncing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <RefreshCw className="w-3.5 h-3.5" />
              )}
              Sync from Shopify
            </button>
            <button
              type="button"
              onClick={handlePush}
              disabled={pushing}
              className="inline-flex items-center gap-1 px-3 py-1.5 bg-emerald-600 text-white rounded-md text-sm hover:bg-emerald-700 disabled:opacity-50"
            >
              {pushing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Upload className="w-3.5 h-3.5" />
              )}
              Push to Shopify
            </button>
          </div>
        </div>

        {statusMessage && (
          <div
            className={`mb-3 p-2 rounded border text-sm flex items-center gap-2 ${
              statusMessage.type === "ok"
                ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                : "bg-red-50 border-red-200 text-red-800"
            }`}
          >
            {statusMessage.type === "ok" ? (
              <CheckCircle className="w-4 h-4 flex-shrink-0" />
            ) : (
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
            )}
            {statusMessage.text}
          </div>
        )}

        {/* Menu metadata edit row */}
        <div className="bg-white border border-slate-200 rounded-md p-3 mb-3 flex items-center gap-3 flex-wrap">
          <input
            type="text"
            value={titleEdit}
            onChange={(e) => setTitleEdit(e.target.value)}
            placeholder="Menu title"
            className="px-2 py-1 border border-slate-300 rounded text-sm flex-1 min-w-[180px]"
          />
          <label className="flex items-center gap-1 text-xs text-slate-700">
            <input
              type="checkbox"
              checked={activeEdit}
              onChange={(e) => setActiveEdit(e.target.checked)}
            />
            Active
          </label>
          <button
            type="button"
            onClick={handleSaveMenuMeta}
            disabled={savingMeta}
            className="inline-flex items-center gap-1 px-2 py-1 border border-slate-300 text-sm text-slate-700 rounded hover:bg-slate-100 disabled:opacity-50"
          >
            {savingMeta ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            Save metadata
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
          {/* Tree pane */}
          <div className="lg:col-span-3 bg-white border border-slate-200 rounded-md p-3 min-h-[500px]">
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-sm font-semibold text-slate-900">Items</h2>
              <button
                type="button"
                onClick={handleAddTopLevel}
                disabled={adding}
                className="inline-flex items-center gap-1 px-2 py-1 bg-blue-600 text-white rounded text-xs hover:bg-blue-700 disabled:opacity-50"
              >
                {adding ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <Plus className="w-3 h-3" />
                )}
                Add top-level item
              </button>
            </div>

            {menu.items.length === 0 ? (
              <div className="text-sm text-slate-500 text-center py-8">
                No items yet. Click "Add top-level item" to get started, or
                "Sync from Shopify" if this menu already exists there.
              </div>
            ) : (
              <MenuItemTree
                nodes={menu.items}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onMove={handleMove}
                onAddChild={handleAddChild}
                onDelete={handleDeleteItem}
              />
            )}

            <p className="text-[11px] text-slate-400 mt-3">
              Drag any row by the grip icon. Drop on the top third of a row to
              place before, the bottom third to place after, or the middle to
              nest as a child.
            </p>
          </div>

          {/* Editor pane */}
          <div className="lg:col-span-2 bg-white border border-slate-200 rounded-md min-h-[500px]">
            <MenuItemEditor
              menuId={menu.id}
              item={selectedItem}
              onSaved={fetchMenu}
              onDeleted={() => {
                setSelectedId(null);
                fetchMenu();
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
