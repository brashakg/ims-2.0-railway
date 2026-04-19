"use client";

import { useCallback, useEffect, useState } from "react";
import {
  RefreshCw,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  Trash2,
  Plus,
  MapPin,
  Store,
} from "lucide-react";

interface LocationUser {
  id: string;
  name: string;
  email: string;
  role: string;
}

interface LocationCount {
  products: number;
  variants: number;
  users: number;
}

interface Location {
  id: string;
  name: string;
  code: string;
  address?: string | null;
  isActive: boolean;
  shopifyLocationId?: string | null;
  users?: LocationUser[];
  _count?: LocationCount;
}

export default function LocationsPage() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<{
    tone: "success" | "error" | "warn";
    text: string;
    details?: string;
  } | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    name: "",
    code: "",
    address: "",
  });

  const fetchLocations = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch("/api/locations");
      if (!response.ok) throw new Error("Failed to fetch locations");
      const data = await response.json();
      setLocations(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLocations();
  }, [fetchLocations]);

  const handleSyncFromShopify = async () => {
    setSyncing(true);
    setBanner(null);
    try {
      const res = await fetch("/api/locations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "sync_from_shopify" }),
      });
      const data = await res.json();
      if (!res.ok) {
        setBanner({
          tone: data.scopeIssue ? "warn" : "error",
          text: data.error || "Sync failed",
          details: data.rawError && data.scopeIssue ? data.rawError : undefined,
        });
      } else {
        setBanner({
          tone: "success",
          text: data.message || "Sync complete",
        });
        await fetchLocations();
      }
    } catch (err) {
      setBanner({
        tone: "error",
        text: err instanceof Error ? err.message : "Sync request failed",
      });
    } finally {
      setSyncing(false);
    }
  };

  const handleAddLocation = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name || !formData.code) {
      setError("Name and code are required");
      return;
    }
    try {
      setSaving(true);
      const response = await fetch("/api/locations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || data.message || "Failed to create location");
      }
      setFormData({ name: "", code: "", address: "" });
      setShowModal(false);
      setError(null);
      await fetchLocations();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create location");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (loc: Location) => {
    if (!confirm(`Delete "${loc.name}" (${loc.code})? This cannot be undone.`)) return;
    setDeletingId(loc.id);
    setBanner(null);
    try {
      const res = await fetch(`/api/locations/${loc.id}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) {
        setBanner({ tone: "error", text: data.error || "Delete failed" });
      } else {
        setBanner({ tone: "success", text: `Deleted "${loc.name}"` });
        await fetchLocations();
      }
    } catch (err) {
      setBanner({
        tone: "error",
        text: err instanceof Error ? err.message : "Delete failed",
      });
    } finally {
      setDeletingId(null);
    }
  };

  const shopifyLinked = locations.filter((l) => l.shopifyLocationId);
  const localOnly = locations.filter((l) => !l.shopifyLocationId);

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-8">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex justify-between items-start gap-4 flex-wrap mb-6">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">Locations</h1>
            <p className="text-sm text-slate-600 mt-1">
              Physical stores and Shopify-linked inventory locations.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleSyncFromShopify}
              disabled={syncing}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm"
            >
              {syncing ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <RefreshCw className="w-4 h-4" />
              )}
              Sync from Shopify
            </button>
            <button
              onClick={() => setShowModal(true)}
              className="flex items-center gap-2 px-4 py-2 border border-slate-300 bg-white text-slate-700 rounded-lg hover:bg-slate-50 text-sm"
            >
              <Plus className="w-4 h-4" />
              Add local
            </button>
          </div>
        </div>

        {banner && (
          <div
            className={`mb-4 p-4 rounded-lg border text-sm ${
              banner.tone === "success"
                ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                : banner.tone === "warn"
                  ? "bg-amber-50 border-amber-200 text-amber-800"
                  : "bg-red-50 border-red-200 text-red-800"
            }`}
          >
            <div className="flex items-start gap-2">
              {banner.tone === "success" ? (
                <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" />
              ) : (
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              )}
              <div>
                <div>{banner.text}</div>
                {banner.details && (
                  <div className="mt-1 text-xs opacity-80 font-mono break-all">
                    {banner.details}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {error && (
          <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
            {error}
          </div>
        )}

        {/* Summary strip */}
        {!loading && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
            <SummaryCard
              label="Shopify-linked"
              value={shopifyLinked.length}
              tone="good"
              icon={<Store className="w-4 h-4" />}
            />
            <SummaryCard
              label="Local only"
              value={localOnly.length}
              tone={localOnly.length > 0 ? "warn" : "neutral"}
              icon={<MapPin className="w-4 h-4" />}
            />
            <SummaryCard
              label="Total"
              value={locations.length}
              tone="neutral"
              icon={<MapPin className="w-4 h-4" />}
            />
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
          </div>
        ) : (
          <>
            {shopifyLinked.length > 0 && (
              <LocationGrid
                title="Shopify-linked"
                locations={shopifyLinked}
                onDelete={handleDelete}
                deletingId={deletingId}
              />
            )}
            {localOnly.length > 0 && (
              <LocationGrid
                title="Local-only (not linked to Shopify)"
                locations={localOnly}
                onDelete={handleDelete}
                deletingId={deletingId}
              />
            )}
            {locations.length === 0 && (
              <div className="p-8 text-center text-slate-500 bg-white rounded-lg">
                No locations found. Click &ldquo;Sync from Shopify&rdquo; to
                pull your real store locations.
              </div>
            )}
          </>
        )}
      </div>

      {/* Add Location Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full">
            <div className="p-6 border-b border-slate-200">
              <h2 className="text-xl font-semibold text-slate-900">
                Add Local Location
              </h2>
              <p className="text-xs text-slate-500 mt-1">
                For locations that don&apos;t exist in Shopify. Shopify-linked
                locations appear automatically after sync.
              </p>
            </div>
            <form onSubmit={handleAddLocation} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Location Name
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) =>
                    setFormData({ ...formData, name: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Main Warehouse"
                  disabled={saving}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Code
                </label>
                <input
                  type="text"
                  value={formData.code}
                  onChange={(e) =>
                    setFormData({ ...formData, code: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="MAIN"
                  disabled={saving}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Address
                </label>
                <input
                  type="text"
                  value={formData.address}
                  onChange={(e) =>
                    setFormData({ ...formData, address: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="123 Main Street"
                  disabled={saving}
                />
              </div>
              <div className="flex gap-2 pt-2">
                <button
                  type="submit"
                  disabled={saving}
                  className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-slate-300 text-sm"
                >
                  {saving ? "Creating..." : "Create"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowModal(false);
                    setError(null);
                    setFormData({ name: "", code: "", address: "" });
                  }}
                  className="flex-1 px-4 py-2 border border-slate-300 bg-white text-slate-700 rounded-lg hover:bg-slate-50 text-sm"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function LocationGrid({
  title,
  locations,
  onDelete,
  deletingId,
}: {
  title: string;
  locations: Location[];
  onDelete: (loc: Location) => void;
  deletingId: string | null;
}) {
  return (
    <div className="mb-8">
      <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">
        {title}
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {locations.map((location) => {
          const linked = Boolean(location.shopifyLocationId);
          const hasInventory =
            (location._count?.products || 0) > 0 ||
            (location._count?.variants || 0) > 0;
          const canDelete = !linked && !hasInventory;

          return (
            <div
              key={location.id}
              className="bg-white rounded-lg border border-slate-200 overflow-hidden"
            >
              <div className="p-4">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="min-w-0">
                    <h3 className="text-base font-semibold text-slate-900 truncate">
                      {location.name}
                    </h3>
                    <p className="text-xs text-slate-500 font-mono">
                      {location.code}
                    </p>
                  </div>
                  {linked ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 text-[11px] border border-emerald-200 flex-shrink-0">
                      <Store className="w-3 h-3" />
                      Shopify
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 text-[11px] border border-amber-200 flex-shrink-0">
                      <AlertTriangle className="w-3 h-3" />
                      Local
                    </span>
                  )}
                </div>

                {location.address && (
                  <p className="text-xs text-slate-600 mb-2">
                    {location.address}
                  </p>
                )}

                <div className="flex items-center gap-3 text-xs text-slate-500 mb-3">
                  <span>
                    <b className="text-slate-800">
                      {location._count?.variants || 0}
                    </b>{" "}
                    variant(s)
                  </span>
                  <span>
                    <b className="text-slate-800">
                      {location._count?.products || 0}
                    </b>{" "}
                    product(s)
                  </span>
                  <span>
                    <b className="text-slate-800">
                      {location._count?.users || 0}
                    </b>{" "}
                    user(s)
                  </span>
                </div>

                {!linked && (
                  <button
                    onClick={() => onDelete(location)}
                    disabled={!canDelete || deletingId === location.id}
                    title={
                      canDelete
                        ? "Delete this local-only location"
                        : "Move or clear its inventory before deleting"
                    }
                    className="inline-flex items-center gap-1 px-2 py-1 text-xs border border-red-200 bg-white text-red-700 rounded hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {deletingId === location.id ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Trash2 className="w-3 h-3" />
                    )}
                    Delete
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  tone,
  icon,
}: {
  label: string;
  value: number;
  tone: "good" | "warn" | "neutral";
  icon: React.ReactNode;
}) {
  const toneClasses =
    tone === "good"
      ? "text-emerald-700"
      : tone === "warn"
        ? "text-amber-700"
        : "text-slate-900";
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-3 flex items-center gap-3">
      <div className="w-9 h-9 rounded-lg bg-slate-100 flex items-center justify-center text-slate-600">
        {icon}
      </div>
      <div>
        <div className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">
          {label}
        </div>
        <div className={`text-xl font-bold ${toneClasses}`}>{value}</div>
      </div>
    </div>
  );
}
