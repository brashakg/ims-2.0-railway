// ============================================================================
// IMS 2.0 - ONDC Seller Node page  (BVI-20)
// ============================================================================
// Admin page for the ONDC (Open Network for Digital Commerce) seller-node
// scaffolding.  Shows current enable state, last catalog publish, ONDC order
// count, TCS summary, and a manual publish button.
//
// DARK by default: the backend is SIMULATED until IMS_ONDC_ENABLED=1 and SNP
// credentials are configured.  This page surfaces that clearly so the owner
// knows the state before the network is live.
//
// Role gate: SUPERADMIN / ADMIN only (matches the backend /ondc/status gate).

import { useEffect, useState, useCallback } from 'react';
import {
  Network,
  RefreshCw,
  Upload,
  CheckCircle2,
  AlertCircle,
  Info,
  Package,
  ShoppingCart,
  IndianRupee,
  Clock,
  Loader2,
} from 'lucide-react';
import { ondcSellerApi, type OndcStatus, type OndcPublishResult } from '../../services/api/ondcSeller';
import { useToast } from '../../context/ToastContext';

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusBadge({ enabled }: { enabled: boolean }) {
  if (enabled) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-green-100 px-3 py-1 text-xs font-semibold text-green-700">
        <CheckCircle2 className="h-3.5 w-3.5" />
        ACTIVE
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-700">
      <AlertCircle className="h-3.5 w-3.5" />
      DARK (simulated)
    </span>
  );
}

interface KpiCardProps {
  icon: typeof Package;
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}

function KpiCard({ icon: Icon, label, value, sub, color = 'blue' }: KpiCardProps) {
  const colorMap: Record<string, string> = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-green-50 text-green-600',
    amber: 'bg-amber-50 text-amber-600',
    purple: 'bg-purple-50 text-purple-600',
  };
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex items-start gap-4">
        <div className={`rounded-lg p-2.5 ${colorMap[color] || colorMap.blue}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <p className="text-sm font-medium text-gray-500">{label}</p>
          <p className="mt-0.5 text-2xl font-bold text-gray-900">{value}</p>
          {sub && <p className="mt-0.5 text-xs text-gray-500">{sub}</p>}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function OndcSellerPage() {
  const toast = useToast();
  const [status, setStatus] = useState<OndcStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [publishResult, setPublishResult] = useState<OndcPublishResult | null>(null);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    try {
      const data = await ondcSellerApi.getStatus();
      setStatus(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handlePublish = async () => {
    setPublishing(true);
    setPublishResult(null);
    try {
      const result = await ondcSellerApi.publishCatalog();
      setPublishResult(result);
      if (result.ok) {
        toast.success(
          result.mode === 'SIMULATED'
            ? `Catalog build: ${result.item_count} items (SIMULATED — gate off)`
            : `Catalog published: ${result.item_count} items to SNP`
        );
        await fetchStatus();
      } else {
        toast.error(`Publish failed: ${result.error || 'Unknown error'}`);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Network error';
      toast.error(`Publish error: ${msg}`);
    } finally {
      setPublishing(false);
    }
  };

  const fmt = (iso: string | null) => {
    if (!iso) return 'Never';
    return new Date(iso).toLocaleString('en-IN', {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      {/* Header */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-blue-50 p-2.5">
            <Network className="h-6 w-6 text-blue-600" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-900">ONDC Seller Node</h1>
            <p className="text-sm text-gray-500">
              India Open Commerce Network — seller scaffolding (BVI-20)
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {status && <StatusBadge enabled={status.enabled} />}
          <button
            onClick={fetchStatus}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Dark-mode notice */}
      {status && !status.enabled && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <Info className="mt-0.5 h-5 w-5 flex-shrink-0 text-amber-600" />
          <div>
            <p className="text-sm font-semibold text-amber-800">ONDC integration is DARK (simulated)</p>
            <p className="mt-0.5 text-sm text-amber-700">{status.note}</p>
            {status.simulated_reason && (
              <p className="mt-1 text-xs text-amber-600">Reason: {status.simulated_reason}</p>
            )}
          </div>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && !status && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      )}

      {/* KPI grid */}
      {status && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <KpiCard
            icon={Package}
            label="Catalog items"
            value={status.last_item_count}
            sub="last publish"
            color="blue"
          />
          <KpiCard
            icon={ShoppingCart}
            label="ONDC orders"
            value={status.ondc_order_count}
            sub="channel=ONDC in IMS"
            color="green"
          />
          <KpiCard
            icon={IndianRupee}
            label="TCS recorded"
            value={`Rs ${status.tcs_total.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`}
            sub="1% on gross payout"
            color="purple"
          />
          <KpiCard
            icon={Clock}
            label="Last publish"
            value={status.last_published_at ? fmt(status.last_published_at).split(',')[0] : 'Never'}
            sub={status.last_published_at ? fmt(status.last_published_at).split(',')[1]?.trim() : 'Not yet published'}
            color="amber"
          />
        </div>
      )}

      {/* Catalog publish card */}
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Catalog publish</h2>
            <p className="mt-1 text-sm text-gray-500">
              Push the IMS catalog to the configured SNP (Seller Network Participant).
              When DARK, this is a dry-run showing the item count — no network call is made.
            </p>
            {status?.last_published_at && (
              <p className="mt-2 text-xs text-gray-400">
                Last publish: {fmt(status.last_published_at)}
                {status.last_item_count > 0 && ` — ${status.last_item_count} items`}
              </p>
            )}
          </div>
          <button
            onClick={handlePublish}
            disabled={publishing}
            className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {publishing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Upload className="h-4 w-4" />
            )}
            {publishing ? 'Publishing...' : 'Publish catalog'}
          </button>
        </div>

        {/* Publish result */}
        {publishResult && (
          <div
            className={`mt-4 rounded-lg border p-4 ${
              publishResult.ok
                ? 'border-green-200 bg-green-50 text-green-800'
                : 'border-red-200 bg-red-50 text-red-800'
            }`}
          >
            <div className="flex items-start gap-2">
              {publishResult.ok ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-green-600" />
              ) : (
                <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-600" />
              )}
              <div className="text-sm">
                <p className="font-semibold">
                  {publishResult.ok ? 'Publish complete' : 'Publish failed'}
                  {' '}
                  <span className="font-normal text-gray-500">
                    (mode: {publishResult.mode})
                  </span>
                </p>
                {publishResult.ok && (
                  <p className="mt-0.5">
                    {publishResult.item_count} items
                    {publishResult.mode === 'SIMULATED' && ' (dry-run — no network call)'}
                    {publishResult.published_at && ` — ${fmt(publishResult.published_at)}`}
                  </p>
                )}
                {!publishResult.ok && publishResult.error && (
                  <p className="mt-0.5">{publishResult.error}</p>
                )}
                {publishResult.simulated_reason && (
                  <p className="mt-0.5 text-xs text-gray-500">
                    Reason: {publishResult.simulated_reason}
                  </p>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* SNP configuration empty state */}
      {status && !status.enabled && (
        <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 p-8 text-center">
          <Network className="mx-auto h-10 w-10 text-gray-400" />
          <h3 className="mt-3 text-base font-semibold text-gray-900">Configure SNP partner</h3>
          <p className="mt-2 max-w-sm mx-auto text-sm text-gray-500">
            To go live, choose an SNP (Eunimart, eSamudaay, eSellers, etc.), obtain
            credentials, and add an integration record in your MongoDB:
          </p>
          <pre className="mt-4 mx-auto max-w-lg rounded-lg bg-gray-100 p-4 text-left text-xs text-gray-700 overflow-auto">
{`// integrations collection (MongoDB)
{
  "type": "ondc",
  "enabled": true,
  "config": {
    "snp_url": "https://your-snp.example.com",
    "subscriber_id": "yourdomain.in",
    "subscriber_url": "https://api.yourdomain.in/api/v1/ondc",
    "ukp": "<HMAC secret from SNP>",
    "city_code": "std:020"
  }
}`}
          </pre>
          <p className="mt-3 text-xs text-gray-500">
            Then set <code className="rounded bg-gray-200 px-1 py-0.5">IMS_ONDC_ENABLED=1</code> in
            the Railway environment variables and redeploy.
          </p>
        </div>
      )}

      {/* Protocol info */}
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="text-base font-semibold text-gray-900">About ONDC integration</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {[
            { label: 'Protocol', value: 'Beckn v0.9.4 / ONDC spec v2' },
            { label: 'Domain', value: 'ONDC:RET12 (Fashion & Accessories)' },
            { label: 'TCS rate', value: '1% on gross payout (per ONDC policy)' },
            { label: 'Default', value: 'DARK — no network calls until enabled' },
            { label: 'Callbacks', value: '/on_search, /on_select, /on_init, /on_confirm, /on_status, /on_cancel' },
            { label: 'Catalog source', value: 'IMS catalog_products + catalog_variants (active, with price)' },
          ].map(({ label, value }) => (
            <div key={label} className="rounded-lg bg-gray-50 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">{label}</p>
              <p className="mt-0.5 text-sm text-gray-800">{value}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
