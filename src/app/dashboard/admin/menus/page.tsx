"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Loader2,
  Plus,
  RefreshCw,
  ChevronRight,
  AlertCircle,
  CheckCircle,
  EyeOff,
  Star,
  CloudDownload,
} from "lucide-react";

interface MenuRow {
  id: string;
  shopifyMenuId: string | null;
  handle: string;
  title: string;
  isDefault: boolean;
  active: boolean;
  locallyModified: boolean;
  lastSyncedAt: string | null;
  createdAt: string;
  updatedAt: string;
  _count: { items: number };
}

export default function MenusListPage() {
  const [menus, setMenus] = useState<MenuRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newHandle, setNewHandle] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  // Bootstrap sync state — pulls every live Shopify menu into the
  // local DB in one click. Required for first use because the page
  // only reads local Menu rows.
  const [bulkSyncing, setBulkSyncing] = useState(false);
  const [bulkSyncMessage, setBulkSyncMessage] = useState<string | null>(null);

  const fetchMenus = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (includeInactive) params.set("includeInactive", "1");
      const res = await fetch(`/api/menus?${params.toString()}`);
      const json = await res.json();
      if (!json.success) {
        setError(json.error || "Failed to load menus");
        setMenus([]);
        return;
      }
      setMenus(json.data || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load menus");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMenus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeInactive]);

  const handleBulkSync = async () => {
    setBulkSyncing(true);
    setBulkSyncMessage(null);
    try {
      const res = await fetch("/api/menus/bulk-sync", { method: "POST" });
      const json = await res.json();
      if (!json.success) {
        setBulkSyncMessage(`Sync failed: ${json.error || "Unknown error"}`);
        return;
      }
      setBulkSyncMessage(
        `Synced ${json.total} menu${json.total === 1 ? "" : "s"} from Shopify ` +
          `(${json.created} created, ${json.updated} updated, ${json.skipped} skipped).`
      );
      await fetchMenus();
    } catch (err) {
      setBulkSyncMessage(
        `Sync error: ${err instanceof Error ? err.message : "Unknown"}`
      );
    } finally {
      setBulkSyncing(false);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);
    if (!newTitle.trim() || !newHandle.trim()) {
      setCreateError("Title and handle are required");
      return;
    }
    setCreating(true);
    try {
      const res = await fetch("/api/menus", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: newTitle.trim(),
          handle: newHandle.trim().toLowerCase(),
        }),
      });
      const json = await res.json();
      if (!json.success) {
        setCreateError(json.error || "Failed to create menu");
        return;
      }
      setNewTitle("");
      setNewHandle("");
      setShowCreateForm(false);
      await fetchMenus();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create");
    } finally {
      setCreating(false);
    }
  };

  const formatDate = (s: string | null) => {
    if (!s) return "Never";
    try {
      return new Date(s).toLocaleString("en-IN", {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "Unknown";
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">
              Storefront menus
            </h1>
            <p className="text-sm text-slate-600 mt-1">
              Edit Shopify navigation menus locally and push changes back. Default
              menus (main-menu, footer, links) cannot be deleted.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={includeInactive}
                onChange={(e) => setIncludeInactive(e.target.checked)}
              />
              Show inactive
            </label>
            <button
              type="button"
              onClick={fetchMenus}
              className="inline-flex items-center gap-1 px-3 py-1.5 border border-slate-300 rounded-md text-sm text-slate-700 hover:bg-slate-100"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Refresh
            </button>
            <button
              type="button"
              onClick={handleBulkSync}
              disabled={bulkSyncing}
              className="inline-flex items-center gap-1 px-3 py-1.5 bg-emerald-600 text-white rounded-md text-sm hover:bg-emerald-700 disabled:opacity-50"
              title="Pull every live menu (and its items) from Shopify into the local DB. Use this once on first visit."
            >
              {bulkSyncing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <CloudDownload className="w-3.5 h-3.5" />
              )}
              Sync from Shopify
            </button>
            <button
              type="button"
              onClick={() => setShowCreateForm((v) => !v)}
              className="inline-flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700"
            >
              <Plus className="w-3.5 h-3.5" />
              New menu
            </button>
          </div>
        </div>

        {showCreateForm && (
          <form
            onSubmit={handleCreate}
            className="bg-white rounded-lg border border-slate-200 p-4 mb-4 shadow-sm"
          >
            <h2 className="text-sm font-medium text-slate-900 mb-3">
              Create a new menu
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">
                  Title
                </label>
                <input
                  type="text"
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="e.g. Promotions"
                  className="w-full px-3 py-1.5 border border-slate-300 rounded-md text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">
                  Handle
                </label>
                <input
                  type="text"
                  value={newHandle}
                  onChange={(e) =>
                    setNewHandle(e.target.value.toLowerCase().replace(/\s+/g, "-"))
                  }
                  placeholder="promotions"
                  className="w-full px-3 py-1.5 border border-slate-300 rounded-md text-sm font-mono"
                />
                <p className="text-[11px] text-slate-500 mt-1">
                  Lowercase, digits, and hyphens only. Used in Shopify URLs.
                </p>
              </div>
            </div>
            {createError && (
              <div className="text-xs text-red-600 mb-2 flex items-center gap-1">
                <AlertCircle className="w-3 h-3" /> {createError}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowCreateForm(false)}
                className="px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100 rounded-md"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={creating}
                className="inline-flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700 disabled:opacity-50"
              >
                {creating ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : null}
                Create
              </button>
            </div>
          </form>
        )}

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-md p-3 mb-4 text-sm text-red-800 flex items-center gap-2">
            <AlertCircle className="w-4 h-4" />
            {error}
          </div>
        )}

        {bulkSyncMessage && (
          <div className="bg-emerald-50 border border-emerald-200 rounded-md p-3 mb-4 text-sm text-emerald-800 flex items-center gap-2">
            <CheckCircle className="w-4 h-4" />
            {bulkSyncMessage}
          </div>
        )}

        <div className="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
          {loading ? (
            <div className="p-8 text-center text-slate-500">
              <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
              Loading menus...
            </div>
          ) : menus.length === 0 ? (
            <div className="p-8 text-center">
              <p className="text-slate-700 mb-2">No menus found locally.</p>
              <p className="text-sm text-slate-500 mb-4">
                Click <strong>Sync from Shopify</strong> above to pull every
                live menu (and its items) into this app, or create a new menu
                from scratch.
              </p>
              <button
                type="button"
                onClick={handleBulkSync}
                disabled={bulkSyncing}
                className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 text-white rounded-md text-sm hover:bg-emerald-700 disabled:opacity-50"
              >
                {bulkSyncing ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <CloudDownload className="w-4 h-4" />
                )}
                Sync from Shopify now
              </button>
            </div>
          ) : (
            <ul className="divide-y divide-slate-200">
              {menus.map((m) => (
                <li
                  key={m.id}
                  className="p-4 hover:bg-slate-50 transition-colors"
                >
                  <Link
                    href={`/dashboard/admin/menus/${m.id}`}
                    className="flex items-center justify-between gap-4"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-slate-900 truncate">
                          {m.title}
                        </span>
                        <span className="text-xs font-mono px-1.5 py-0.5 bg-slate-100 rounded text-slate-600">
                          {m.handle}
                        </span>
                        {m.isDefault && (
                          <span className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 bg-amber-100 text-amber-800 rounded">
                            <Star className="w-3 h-3" />
                            Default
                          </span>
                        )}
                        {!m.active && (
                          <span className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 bg-slate-200 text-slate-700 rounded">
                            <EyeOff className="w-3 h-3" />
                            Inactive
                          </span>
                        )}
                        {m.locallyModified && (
                          <span className="text-[11px] px-1.5 py-0.5 bg-yellow-100 text-yellow-800 rounded">
                            Unsaved on Shopify
                          </span>
                        )}
                        {!m.locallyModified && m.lastSyncedAt && (
                          <span className="inline-flex items-center gap-1 text-[11px] text-emerald-700">
                            <CheckCircle className="w-3 h-3" />
                            In sync
                          </span>
                        )}
                      </div>
                      <div className="mt-1 text-xs text-slate-500 flex items-center gap-3 flex-wrap">
                        <span>{m._count?.items || 0} items</span>
                        <span>Last synced: {formatDate(m.lastSyncedAt)}</span>
                        {m.shopifyMenuId ? (
                          <span className="font-mono truncate">
                            {m.shopifyMenuId.replace("gid://shopify/Menu/", "id ")}
                          </span>
                        ) : (
                          <span className="text-orange-600">Not on Shopify</span>
                        )}
                      </div>
                    </div>
                    <ChevronRight className="w-4 h-4 text-slate-400 flex-shrink-0" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
