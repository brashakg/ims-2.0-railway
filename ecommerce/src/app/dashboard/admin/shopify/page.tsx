"use client";

import { useEffect, useState } from "react";
import { Loader2, RefreshCw, Trash2, CheckCircle, XCircle, Webhook } from "lucide-react";

interface ShopifyStatus {
  configured: boolean;
  storeUrl: string | null;
  stats: {
    totalSynced: number;
    failedSyncs: number;
    lastSync: string | null;
  };
}

interface WebhookInfo {
  id: string;
  topic: string;
  endpoint: { __typename: string; callbackUrl?: string };
  format: string;
  createdAt: string;
}

interface WebhookEvent {
  id: string;
  topic: string;
  shopifyId: string | null;
  status: string;
  message: string | null;
  createdAt: string;
}

export default function ShopifySettingsPage() {
  const [status, setStatus] = useState<ShopifyStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Webhook state
  const [webhooks, setWebhooks] = useState<WebhookInfo[]>([]);
  const [recentEvents, setRecentEvents] = useState<WebhookEvent[]>([]);
  const [webhookLoading, setWebhookLoading] = useState(false);
  const [registering, setRegistering] = useState(false);

  useEffect(() => {
    fetchStatus();
    fetchWebhooks();
  }, []);

  const fetchStatus = async () => {
    try {
      setLoading(true);
      const res = await fetch("/api/shopify/status");
      if (!res.ok) throw new Error("Failed to fetch Shopify status");
      setStatus(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  const fetchWebhooks = async () => {
    try {
      setWebhookLoading(true);
      const res = await fetch("/api/shopify/webhooks");
      if (!res.ok) return;
      const data = await res.json();
      if (data.success) {
        setWebhooks(data.data?.shopifyWebhooks || []);
        setRecentEvents(data.data?.recentEvents || []);
      }
    } catch { /* ignore */ } finally {
      setWebhookLoading(false);
    }
  };

  const handleRegisterWebhooks = async () => {
    setRegistering(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch("/api/shopify/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "register_all" }),
      });
      const data = await res.json();
      if (data.success) {
        setSuccess(data.message);
        fetchWebhooks();
      } else {
        setError(data.error || "Failed to register webhooks");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setRegistering(false);
    }
  };

  const handleDeleteAllWebhooks = async () => {
    if (!confirm("Delete all webhook subscriptions?")) return;
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch("/api/shopify/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete_all" }),
      });
      const data = await res.json();
      if (data.success) {
        setSuccess(data.message);
        fetchWebhooks();
      } else {
        setError(data.error || "Failed to delete webhooks");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch("/api/shopify/status");
      if (!res.ok) throw new Error("API unreachable");
      const data = await res.json();
      if (data.configured) {
        setSuccess(`Connected to ${data.storeUrl}`);
      } else {
        setError("Shopify is not configured. Check environment variables.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connection test failed");
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-8">
      <div className="max-w-5xl mx-auto space-y-6">
        <h1 className="text-3xl font-bold text-slate-900">Shopify Integration</h1>

        {error && (
          <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">{error}</div>
        )}
        {success && (
          <div className="p-4 bg-green-50 border border-green-200 rounded-lg text-green-700 text-sm">{success}</div>
        )}

        {/* Connection Status */}
        <div className="bg-white rounded-xl shadow-sm border p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-slate-900">Connection Status</h2>
            <button
              onClick={handleTestConnection}
              disabled={testing}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm"
            >
              {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              Test Connection
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="p-4 bg-slate-50 rounded-lg">
              <p className="text-xs text-slate-500 mb-1">Status</p>
              <div className="flex items-center gap-2">
                <div className={`w-2.5 h-2.5 rounded-full ${status?.configured ? "bg-green-500" : "bg-red-500"}`} />
                <span className="font-semibold text-sm">{status?.configured ? "Connected" : "Not Configured"}</span>
              </div>
            </div>
            <div className="p-4 bg-slate-50 rounded-lg">
              <p className="text-xs text-slate-500 mb-1">Store URL</p>
              <p className="font-semibold text-sm truncate">{status?.storeUrl || "—"}</p>
            </div>
            <div className="p-4 bg-slate-50 rounded-lg">
              <p className="text-xs text-slate-500 mb-1">Total Synced</p>
              <p className="text-xl font-bold text-green-600">{status?.stats.totalSynced || 0}</p>
            </div>
            <div className="p-4 bg-slate-50 rounded-lg">
              <p className="text-xs text-slate-500 mb-1">Failed Syncs</p>
              <p className="text-xl font-bold text-red-600">{status?.stats.failedSyncs || 0}</p>
            </div>
          </div>
        </div>

        {/* Webhooks */}
        <div className="bg-white rounded-xl shadow-sm border p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Webhook Subscriptions</h2>
              <p className="text-xs text-slate-500 mt-1">
                {webhooks.length} active webhook{webhooks.length !== 1 ? "s" : ""} registered with Shopify
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleRegisterWebhooks}
                disabled={registering}
                className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 text-sm"
              >
                {registering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Webhook className="w-4 h-4" />}
                Register All Webhooks
              </button>
              {webhooks.length > 0 && (
                <button
                  onClick={handleDeleteAllWebhooks}
                  className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 text-sm"
                >
                  <Trash2 className="w-4 h-4" />
                  Delete All
                </button>
              )}
            </div>
          </div>

          {webhookLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
            </div>
          ) : webhooks.length === 0 ? (
            <div className="text-center py-8 text-slate-500 text-sm">
              No webhooks registered. Click &quot;Register All Webhooks&quot; to set up real-time sync.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-slate-50">
                    <th className="text-left py-2.5 px-3 font-medium text-slate-600">Topic</th>
                    <th className="text-left py-2.5 px-3 font-medium text-slate-600">Callback URL</th>
                    <th className="text-left py-2.5 px-3 font-medium text-slate-600">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {webhooks.map((wh) => (
                    <tr key={wh.id} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="py-2.5 px-3 font-mono text-xs">{wh.topic}</td>
                      <td className="py-2.5 px-3 text-xs text-slate-600 truncate max-w-xs">
                        {wh.endpoint?.callbackUrl || "—"}
                      </td>
                      <td className="py-2.5 px-3 text-xs text-slate-500">
                        {new Date(wh.createdAt).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Recent Webhook Events */}
        {recentEvents.length > 0 && (
          <div className="bg-white rounded-xl shadow-sm border p-6">
            <h2 className="text-lg font-semibold text-slate-900 mb-4">Recent Webhook Events</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-slate-50">
                    <th className="text-left py-2.5 px-3 font-medium text-slate-600">Topic</th>
                    <th className="text-left py-2.5 px-3 font-medium text-slate-600">Status</th>
                    <th className="text-left py-2.5 px-3 font-medium text-slate-600">Message</th>
                    <th className="text-left py-2.5 px-3 font-medium text-slate-600">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {recentEvents.slice(0, 20).map((evt) => (
                    <tr key={evt.id} className="border-b border-slate-100">
                      <td className="py-2.5 px-3 font-mono text-xs">{evt.topic}</td>
                      <td className="py-2.5 px-3">
                        <span className={`inline-flex items-center gap-1 text-xs font-semibold ${
                          evt.status === "PROCESSED" ? "text-green-700" :
                          evt.status === "FAILED" ? "text-red-700" :
                          "text-yellow-700"
                        }`}>
                          {evt.status === "PROCESSED" ? <CheckCircle className="w-3 h-3" /> :
                           evt.status === "FAILED" ? <XCircle className="w-3 h-3" /> : null}
                          {evt.status}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-xs text-slate-600 truncate max-w-xs">{evt.message || "—"}</td>
                      <td className="py-2.5 px-3 text-xs text-slate-500">
                        {new Date(evt.createdAt).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Setup Instructions */}
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-6">
          <h3 className="font-semibold text-blue-900 mb-2">Connection Info</h3>
          <p className="text-sm text-blue-800 mb-2">
            This app connects to Shopify via CLI OAuth (client credentials). Credentials are set in Railway environment variables:
          </p>
          <ul className="text-sm text-blue-800 space-y-1 list-disc list-inside">
            <li><code>SHOPIFY_STORE_URL</code> — Your .myshopify.com domain</li>
            <li><code>SHOPIFY_CLIENT_ID</code> + <code>SHOPIFY_CLIENT_SECRET</code> — OAuth app credentials</li>
            <li><code>SHOPIFY_ACCESS_TOKEN</code> — Legacy admin token (fallback)</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
