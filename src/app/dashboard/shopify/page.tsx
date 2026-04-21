"use client";

import { useEffect, useState, useCallback } from "react";

interface ShopifyStatus {
  configured: boolean;
  storeUrl: string | null;
  stats: {
    totalSynced: number;
    failedSyncs: number;
    lastSync: string | null;
  };
}

interface PullStatus {
  totalLocalProducts: number;
  syncedWithShopify: number;
  localOnly: number;
}

interface WebhookData {
  shopifyWebhooks: Array<{
    id: string;
    topic: string;
    endpoint: { __typename: string; callbackUrl?: string };
    format: string;
    createdAt: string;
  }>;
  recentEvents: Array<{
    id: string;
    topic: string;
    shopifyId: string | null;
    status: string;
    message: string | null;
    createdAt: string;
  }>;
  availableTopics: string[];
}

export default function ShopifySettingsPage() {
  const [status, setStatus] = useState<ShopifyStatus | null>(null);
  const [pullStatus, setPullStatus] = useState<PullStatus | null>(null);
  const [webhookData, setWebhookData] = useState<WebhookData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Action states
  const [pulling, setPulling] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [registeringWebhooks, setRegisteringWebhooks] = useState(false);
  const [deletingWebhooks, setDeletingWebhooks] = useState(false);
  const [activeTab, setActiveTab] = useState<"sync" | "webhooks" | "events">("sync");

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      const [statusRes, pullRes] = await Promise.all([
        fetch("/api/shopify/status"),
        fetch("/api/shopify/pull"),
      ]);

      if (statusRes.ok) {
        setStatus(await statusRes.json());
      }
      if (pullRes.ok) {
        const pullData = await pullRes.json();
        if (pullData.success) setPullStatus(pullData.data);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchWebhooks = useCallback(async () => {
    try {
      const res = await fetch("/api/shopify/webhooks");
      if (res.ok) {
        const data = await res.json();
        if (data.success) setWebhookData(data.data);
      }
    } catch (err) {
      console.error("Failed to fetch webhooks:", err);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    if (activeTab === "webhooks" || activeTab === "events") {
      fetchWebhooks();
    }
  }, [activeTab, fetchWebhooks]);

  // ── Pull products FROM Shopify in RESUMABLE CHUNKS ──
  // The old /api/shopify/pull call routinely blew past Railway's 300s
  // maxDuration when the store has thousands of products + paced GraphQL.
  // This driver loops /api/shopify/pull/chunk with a cursor, so no single
  // server call exceeds ~60-90s. UI shows live running totals.
  const handlePullFromShopify = async () => {
    setPulling(true);
    setError(null);
    setSuccess(null);

    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission();
    }

    let cursor: string | null = null;
    let done = false;
    let totalPages = 0;
    let totalNew = 0;
    let totalUpdated = 0;
    let totalErrors = 0;
    let safetyCounter = 0;

    while (!done && safetyCounter < 300) {
      safetyCounter++;
      try {
        const res = await fetch("/api/shopify/pull/chunk", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cursor, maxSeconds: 60 }),
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
          setError(data.error || `Chunk failed at cursor ${cursor}`);
          setPulling(false);
          return;
        }
        totalPages += data.summary?.pagesProcessed || 0;
        totalNew += data.summary?.newCount || 0;
        totalUpdated += data.summary?.updatedCount || 0;
        totalErrors += data.summary?.errorCount || 0;
        cursor = data.nextCursor;
        done = data.done === true;
        setSuccess(
          `Pulling… ${totalNew + totalUpdated} products so far (${totalNew} new, ${totalUpdated} updated) across ${totalPages} pages${totalErrors ? ` · ${totalErrors} errors` : ""}. ${done ? "Done." : "Fetching next chunk…"}`
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "Chunk call failed");
        setPulling(false);
        return;
      }
    }

    const doneMsg = `Pull complete: ${totalNew} new, ${totalUpdated} updated, ${totalErrors} errors across ${totalPages} pages.`;
    setSuccess(doneMsg);
    setPulling(false);
    fetchAll();
    if (Notification.permission === "granted") {
      new Notification("Shopify Pull Complete", {
        body: doneMsg,
        icon: "/app-icon.png",
      });
    }
  };

  // ── Push ALL local products TO Shopify ──
  const handlePushToShopify = async () => {
    try {
      setPushing(true);
      setError(null);
      setSuccess(null);

      // Get all unsynced products
      const productsRes = await fetch("/api/products?limit=1000&status=DRAFT");
      const productsData = await productsRes.json();
      const unsyncedProducts = (productsData.data || []).filter(
        (p: any) => !p.shopifyProductId
      );

      if (unsyncedProducts.length === 0) {
        setSuccess("All products are already synced to Shopify");
        return;
      }

      const res = await fetch("/api/shopify/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          productIds: unsyncedProducts.map((p: any) => p.id),
        }),
      });

      const data = await res.json();
      if (data.success) {
        const s = data.summary || {};
        const base = `Pushed ${s.success || 0} products to Shopify (${s.failed || 0} failed, ${s.skipped || 0} skipped)`;
        if (s.aborted) {
          setError(
            `${base}. ${s.abortReason || "Push aborted after repeated failures."}`
          );
        } else {
          setSuccess(base);
        }
        await fetchAll();
      } else {
        setError(data.error || "Push failed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Push failed");
    } finally {
      setPushing(false);
    }
  };

  // ── Register all webhooks ──
  const handleRegisterWebhooks = async () => {
    try {
      setRegisteringWebhooks(true);
      setError(null);
      setSuccess(null);

      const res = await fetch("/api/shopify/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "register_all" }),
      });

      const data = await res.json();
      if (data.success) {
        setSuccess(data.message);
        await fetchWebhooks();
      } else {
        setError(data.error || "Failed to register webhooks");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to register webhooks");
    } finally {
      setRegisteringWebhooks(false);
    }
  };

  // ── Delete all webhooks ──
  const handleDeleteAllWebhooks = async () => {
    if (!confirm("Are you sure you want to remove all webhook subscriptions?")) return;
    try {
      setDeletingWebhooks(true);
      setError(null);
      setSuccess(null);

      const res = await fetch("/api/shopify/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete_all" }),
      });

      const data = await res.json();
      if (data.success) {
        setSuccess(data.message);
        await fetchWebhooks();
      } else {
        setError(data.error || "Failed to delete webhooks");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setDeletingWebhooks(false);
    }
  };

  // ── Delete single webhook ──
  const handleDeleteWebhook = async (webhookId: string) => {
    try {
      setError(null);
      const res = await fetch("/api/shopify/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete", webhookId }),
      });
      const data = await res.json();
      if (data.success) {
        setSuccess("Webhook deleted");
        await fetchWebhooks();
      } else {
        setError(data.error || "Failed to delete webhook");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-slate-600">Loading Shopify settings...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-8">
      <div className="max-w-6xl mx-auto">
        <h1 className="text-3xl font-bold text-slate-900 mb-2">
          Shopify Sync & Management
        </h1>
        <p className="text-slate-500 mb-8">
          Bidirectional product sync, webhook management, and real-time updates
        </p>

        {error && (
          <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
            {error}
            <button
              onClick={() => setError(null)}
              className="ml-2 text-red-500 hover:text-red-700"
            >
              ×
            </button>
          </div>
        )}

        {success && (
          <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded-lg text-green-700">
            {success}
            <button
              onClick={() => setSuccess(null)}
              className="ml-2 text-green-500 hover:text-green-700"
            >
              ×
            </button>
          </div>
        )}

        {/* Connection Status Banner */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div
                className={`w-4 h-4 rounded-full ${
                  status?.configured ? "bg-green-500" : "bg-red-500"
                }`}
              />
              <div>
                <p className="font-semibold text-slate-900">
                  {status?.configured ? "Connected to Shopify" : "Not Connected"}
                </p>
                <p className="text-sm text-slate-500">
                  {status?.storeUrl || "Configure credentials in Railway env vars"}
                </p>
              </div>
            </div>
            <div className="flex gap-3 text-sm">
              <div className="text-center px-4">
                <p className="text-2xl font-bold text-green-600">
                  {status?.stats.totalSynced || 0}
                </p>
                <p className="text-slate-500">Synced</p>
              </div>
              <div className="text-center px-4">
                <p className="text-2xl font-bold text-red-600">
                  {status?.stats.failedSyncs || 0}
                </p>
                <p className="text-slate-500">Failed</p>
              </div>
              <div className="text-center px-4">
                <p className="text-sm font-medium text-slate-700">
                  {status?.stats.lastSync
                    ? new Date(status.stats.lastSync).toLocaleString()
                    : "Never"}
                </p>
                <p className="text-slate-500">Last Sync</p>
              </div>
            </div>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex gap-1 mb-6 bg-white rounded-lg shadow p-1">
          {(["sync", "webhooks", "events"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-1 px-4 py-3 rounded-md text-sm font-medium transition ${
                activeTab === tab
                  ? "bg-blue-500 text-white"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {tab === "sync" && "Product Sync"}
              {tab === "webhooks" && "Webhook Subscriptions"}
              {tab === "events" && "Webhook Events"}
            </button>
          ))}
        </div>

        {/* ── SYNC TAB ── */}
        {activeTab === "sync" && (
          <div className="space-y-6">
            {/* Product Stats */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-xl font-semibold text-slate-900 mb-4">
                Product Inventory
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                <div className="p-4 bg-slate-50 rounded-lg text-center">
                  <p className="text-3xl font-bold text-slate-900">
                    {pullStatus?.totalLocalProducts || 0}
                  </p>
                  <p className="text-sm text-slate-500">Total Local Products</p>
                </div>
                <div className="p-4 bg-green-50 rounded-lg text-center">
                  <p className="text-3xl font-bold text-green-600">
                    {pullStatus?.syncedWithShopify || 0}
                  </p>
                  <p className="text-sm text-slate-500">Synced with Shopify</p>
                </div>
                <div className="p-4 bg-amber-50 rounded-lg text-center">
                  <p className="text-3xl font-bold text-amber-600">
                    {pullStatus?.localOnly || 0}
                  </p>
                  <p className="text-sm text-slate-500">Local Only (not on Shopify)</p>
                </div>
              </div>
            </div>

            {/* Sync Actions */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Pull FROM Shopify */}
              <div className="bg-white rounded-lg shadow p-6">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-lg bg-blue-100 flex items-center justify-center">
                    <svg
                      className="w-5 h-5 text-blue-600"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                      />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-lg font-semibold text-slate-900">
                      Pull from Shopify
                    </h3>
                    <p className="text-sm text-slate-500">
                      Import all products from your Shopify store into this app
                    </p>
                  </div>
                </div>

                <p className="text-sm text-slate-600 mb-4">
                  This will fetch all products, variants, images, and metafields
                  from Shopify and create/update them in your local inventory.
                  Existing products will be updated — new ones will be created.
                </p>

                <button
                  onClick={handlePullFromShopify}
                  disabled={pulling || !status?.configured}
                  className="w-full px-4 py-3 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:bg-slate-300 transition font-medium"
                >
                  {pulling ? (
                    <span className="flex items-center justify-center gap-2">
                      <svg
                        className="animate-spin h-4 w-4"
                        viewBox="0 0 24 24"
                      >
                        <circle
                          className="opacity-25"
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth="4"
                          fill="none"
                        />
                        <path
                          className="opacity-75"
                          fill="currentColor"
                          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                        />
                      </svg>
                      Pulling in background...
                    </span>
                  ) : (
                    "Pull All Products from Shopify"
                  )}
                </button>
              </div>

              {/* Push TO Shopify */}
              <div className="bg-white rounded-lg shadow p-6">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-lg bg-green-100 flex items-center justify-center">
                    <svg
                      className="w-5 h-5 text-green-600"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"
                      />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-lg font-semibold text-slate-900">
                      Push to Shopify
                    </h3>
                    <p className="text-sm text-slate-500">
                      Upload local-only products to your Shopify store
                    </p>
                  </div>
                </div>

                <p className="text-sm text-slate-600 mb-4">
                  This will push all draft products that haven&apos;t been synced
                  to Shopify yet. Products already on Shopify will be skipped.
                  {pullStatus?.localOnly
                    ? ` ${pullStatus.localOnly} product(s) ready to push.`
                    : " No unsynced products found."}
                </p>

                <button
                  onClick={handlePushToShopify}
                  disabled={pushing || !status?.configured || !pullStatus?.localOnly}
                  className="w-full px-4 py-3 bg-green-500 text-white rounded-lg hover:bg-green-600 disabled:bg-slate-300 transition font-medium"
                >
                  {pushing ? (
                    <span className="flex items-center justify-center gap-2">
                      <svg
                        className="animate-spin h-4 w-4"
                        viewBox="0 0 24 24"
                      >
                        <circle
                          className="opacity-25"
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth="4"
                          fill="none"
                        />
                        <path
                          className="opacity-75"
                          fill="currentColor"
                          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                        />
                      </svg>
                      Pushing Products...
                    </span>
                  ) : (
                    "Push Unsynced Products to Shopify"
                  )}
                </button>
              </div>
            </div>

            {/* Sync Collections */}
            <div className="bg-white rounded-lg shadow p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-lg bg-purple-100 flex items-center justify-center">
                  <svg
                    className="w-5 h-5 text-purple-600"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
                    />
                  </svg>
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-slate-900">
                    Sync Collections
                  </h3>
                  <p className="text-sm text-slate-500">
                    Pull all collections from Shopify
                  </p>
                </div>
              </div>
              <button
                onClick={async () => {
                  try {
                    setError(null);
                    setSuccess(null);
                    const res = await fetch("/api/collections/sync", {
                      method: "POST",
                    });
                    const data = await res.json();
                    if (data.success) {
                      setSuccess(data.message);
                    } else {
                      setError(data.error || "Collection sync failed");
                    }
                  } catch (err) {
                    setError(
                      err instanceof Error ? err.message : "Collection sync failed"
                    );
                  }
                }}
                disabled={!status?.configured}
                className="px-6 py-2 bg-purple-500 text-white rounded-lg hover:bg-purple-600 disabled:bg-slate-300 transition font-medium"
              >
                Sync Collections from Shopify
              </button>
            </div>
          </div>
        )}

        {/* ── WEBHOOKS TAB ── */}
        {activeTab === "webhooks" && (
          <div className="space-y-6">
            {/* Webhook Actions */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-xl font-semibold text-slate-900 mb-2">
                Webhook Subscriptions
              </h2>
              <p className="text-sm text-slate-500 mb-6">
                Webhooks allow Shopify to notify this app in real-time when products,
                inventory, orders, or collections change. Register webhooks to keep
                your local data automatically in sync.
              </p>

              <div className="flex gap-3 mb-6">
                <button
                  onClick={handleRegisterWebhooks}
                  disabled={registeringWebhooks || !status?.configured}
                  className="px-6 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:bg-slate-300 transition font-medium"
                >
                  {registeringWebhooks
                    ? "Registering..."
                    : "Register All Webhooks"}
                </button>

                <button
                  onClick={handleDeleteAllWebhooks}
                  disabled={
                    deletingWebhooks ||
                    !webhookData?.shopifyWebhooks.length
                  }
                  className="px-6 py-2 bg-red-500 text-white rounded-lg hover:bg-red-600 disabled:bg-slate-300 transition font-medium"
                >
                  {deletingWebhooks ? "Deleting..." : "Remove All Webhooks"}
                </button>

                <button
                  onClick={fetchWebhooks}
                  className="px-6 py-2 bg-slate-200 text-slate-700 rounded-lg hover:bg-slate-300 transition font-medium"
                >
                  Refresh
                </button>
              </div>

              {/* Active Webhooks */}
              {webhookData?.shopifyWebhooks &&
              webhookData.shopifyWebhooks.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-slate-50">
                        <th className="text-left p-3 font-medium text-slate-600">
                          Topic
                        </th>
                        <th className="text-left p-3 font-medium text-slate-600">
                          Callback URL
                        </th>
                        <th className="text-left p-3 font-medium text-slate-600">
                          Format
                        </th>
                        <th className="text-left p-3 font-medium text-slate-600">
                          Created
                        </th>
                        <th className="text-right p-3 font-medium text-slate-600">
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {webhookData.shopifyWebhooks.map((wh) => (
                        <tr key={wh.id} className="border-t border-slate-100">
                          <td className="p-3">
                            <span className="px-2 py-1 bg-blue-100 text-blue-700 text-xs rounded-full font-medium">
                              {wh.topic}
                            </span>
                          </td>
                          <td className="p-3 text-slate-600 font-mono text-xs truncate max-w-xs">
                            {wh.endpoint?.callbackUrl || "N/A"}
                          </td>
                          <td className="p-3 text-slate-600">{wh.format}</td>
                          <td className="p-3 text-slate-500">
                            {new Date(wh.createdAt).toLocaleDateString()}
                          </td>
                          <td className="p-3 text-right">
                            <button
                              onClick={() => handleDeleteWebhook(wh.id)}
                              className="text-red-500 hover:text-red-700 text-xs font-medium"
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-center py-8 text-slate-500">
                  <p className="mb-2">No webhooks registered yet.</p>
                  <p className="text-sm">
                    Click &quot;Register All Webhooks&quot; to start receiving real-time
                    updates from Shopify.
                  </p>
                </div>
              )}
            </div>

            {/* Available Topics */}
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="text-lg font-semibold text-slate-900 mb-3">
                Supported Webhook Topics
              </h3>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                {[
                  {
                    topic: "PRODUCTS_CREATE",
                    desc: "New product added on Shopify",
                  },
                  {
                    topic: "PRODUCTS_UPDATE",
                    desc: "Product details changed",
                  },
                  {
                    topic: "PRODUCTS_DELETE",
                    desc: "Product removed from Shopify",
                  },
                  {
                    topic: "INVENTORY_LEVELS_UPDATE",
                    desc: "Stock levels changed",
                  },
                  { topic: "ORDERS_CREATE", desc: "New order placed" },
                  { topic: "ORDERS_UPDATED", desc: "Order status changed" },
                  {
                    topic: "COLLECTIONS_CREATE",
                    desc: "New collection created",
                  },
                  {
                    topic: "COLLECTIONS_UPDATE",
                    desc: "Collection modified",
                  },
                  {
                    topic: "COLLECTIONS_DELETE",
                    desc: "Collection removed",
                  },
                ].map((t) => {
                  const isActive = webhookData?.shopifyWebhooks.some(
                    (w) => w.topic === t.topic
                  );
                  return (
                    <div
                      key={t.topic}
                      className={`p-3 rounded-lg border ${
                        isActive
                          ? "border-green-200 bg-green-50"
                          : "border-slate-200 bg-slate-50"
                      }`}
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <div
                          className={`w-2 h-2 rounded-full ${
                            isActive ? "bg-green-500" : "bg-slate-300"
                          }`}
                        />
                        <span className="text-xs font-medium text-slate-700">
                          {t.topic}
                        </span>
                      </div>
                      <p className="text-xs text-slate-500">{t.desc}</p>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {/* ── EVENTS TAB ── */}
        {activeTab === "events" && (
          <div className="bg-white rounded-lg shadow p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold text-slate-900">
                Recent Webhook Events
              </h2>
              <button
                onClick={fetchWebhooks}
                className="px-4 py-2 bg-slate-200 text-slate-700 rounded-lg hover:bg-slate-300 transition text-sm"
              >
                Refresh
              </button>
            </div>

            {webhookData?.recentEvents &&
            webhookData.recentEvents.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-slate-50">
                      <th className="text-left p-3 font-medium text-slate-600">
                        Time
                      </th>
                      <th className="text-left p-3 font-medium text-slate-600">
                        Topic
                      </th>
                      <th className="text-left p-3 font-medium text-slate-600">
                        Shopify ID
                      </th>
                      <th className="text-left p-3 font-medium text-slate-600">
                        Status
                      </th>
                      <th className="text-left p-3 font-medium text-slate-600">
                        Message
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {webhookData.recentEvents.map((evt) => (
                      <tr key={evt.id} className="border-t border-slate-100">
                        <td className="p-3 text-slate-500 text-xs whitespace-nowrap">
                          {new Date(evt.createdAt).toLocaleString()}
                        </td>
                        <td className="p-3">
                          <span className="px-2 py-1 bg-blue-100 text-blue-700 text-xs rounded-full">
                            {evt.topic}
                          </span>
                        </td>
                        <td className="p-3 text-slate-600 font-mono text-xs">
                          {evt.shopifyId || "—"}
                        </td>
                        <td className="p-3">
                          <span
                            className={`px-2 py-1 text-xs rounded-full font-medium ${
                              evt.status === "PROCESSED"
                                ? "bg-green-100 text-green-700"
                                : evt.status === "FAILED"
                                ? "bg-red-100 text-red-700"
                                : evt.status === "RECEIVED"
                                ? "bg-yellow-100 text-yellow-700"
                                : "bg-slate-100 text-slate-600"
                            }`}
                          >
                            {evt.status}
                          </span>
                        </td>
                        <td className="p-3 text-slate-600 text-xs truncate max-w-xs">
                          {evt.message || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-12 text-slate-500">
                <p className="mb-2">No webhook events received yet.</p>
                <p className="text-sm">
                  Events will appear here as Shopify sends real-time updates.
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
