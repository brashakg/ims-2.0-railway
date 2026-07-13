// ============================================================================
// IMS 2.0 — Vendor Portal (public, token-auth via URL)
// ============================================================================
// External lens labs hit `/vendor-portal/:tokenId` — no IMS login. The token
// in the URL is checked server-side; expired/disabled tokens render an
// "access denied" message.
//
// The page lists this vendor's open workshop jobs (PII-redacted to initials)
// and lets the lab post status updates: RECEIVED / IN_PRODUCTION / DISPATCHED
// / DELIVERED / ON_HOLD / CANCELLED, plus optional note + vendor_order_id +
// tracking URL.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  ClipboardCheck, AlertTriangle, Loader2, Package, Truck,
  CheckCircle2, PauseCircle, XCircle, Send, RefreshCw, ExternalLink,
} from 'lucide-react';
import {
  vendorPortalApi,
  type VendorPortalJob,
  type VendorPortalListResponse,
  type VendorPortalStatus,
} from '../../services/api/vendorPortal';

// ----------------------------------------------------------------------------
// Status taxonomy — mirrors the backend PORTAL_STATUSES set
// ----------------------------------------------------------------------------

const STATUSES: Array<{
  id: VendorPortalStatus;
  label: string;
  shortLabel: string;
  badge: string;
  cta: string;
}> = [
  { id: 'RECEIVED',      label: 'Acknowledge receipt of job',  shortLabel: 'Received',      badge: 'bg-blue-50 text-blue-700 border-blue-200',     cta: 'Mark received' },
  { id: 'IN_PRODUCTION', label: 'In production / cutting',      shortLabel: 'In production', badge: 'bg-amber-50 text-amber-700 border-amber-200',  cta: 'Mark in production' },
  { id: 'DISPATCHED',    label: 'Dispatched back to store',     shortLabel: 'Dispatched',    badge: 'bg-blue-50 text-blue-700 border-blue-200',    cta: 'Mark dispatched' },
  { id: 'DELIVERED',     label: 'Confirmed delivered',          shortLabel: 'Delivered',     badge: 'bg-green-50 text-green-700 border-green-200',  cta: 'Mark delivered' },
  { id: 'ON_HOLD',       label: 'On hold (need clarification)', shortLabel: 'On hold',       badge: 'bg-amber-50 text-amber-700 border-amber-200', cta: 'Mark on hold' },
  { id: 'CANCELLED',     label: 'Won\'t fulfil',                shortLabel: 'Cancelled',     badge: 'bg-red-50 text-red-700 border-red-200',         cta: 'Mark cancelled' },
];

const STATUS_ICONS: Record<VendorPortalStatus, typeof ClipboardCheck> = {
  RECEIVED: ClipboardCheck,
  IN_PRODUCTION: Package,
  DISPATCHED: Truck,
  DELIVERED: CheckCircle2,
  ON_HOLD: PauseCircle,
  CANCELLED: XCircle,
};

function statusBadge(s: string | null | undefined): string {
  return STATUSES.find((x) => x.id === s)?.badge
    ?? 'bg-gray-100 text-gray-700 border-gray-200';
}

// ----------------------------------------------------------------------------
// Page
// ----------------------------------------------------------------------------

export default function VendorPortalPage() {
  const { tokenId } = useParams<{ tokenId: string }>();
  const [data, setData] = useState<VendorPortalListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<VendorPortalJob | null>(null);

  const reload = useCallback(async () => {
    if (!tokenId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await vendorPortalApi.listJobs(tokenId);
      setData(resp);
    } catch (e: unknown) {
      const msg =
        (e as { response?: { status?: number; data?: { detail?: string } } })?.response?.data?.detail ??
        'Unable to load jobs. Token may be invalid, expired, or revoked.';
      const status = (e as { response?: { status?: number } })?.response?.status;
      setError(status === 401 || status === 404 ? msg : 'Network error — try again.');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [tokenId]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (!tokenId) {
    return (
      <AccessDenied msg="Missing portal token in URL." />
    );
  }

  if (loading && !data) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-gray-500" />
      </div>
    );
  }

  if (error || !data) {
    return <AccessDenied msg={error || 'Access denied.'} />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Header data={data} onReload={reload} loading={loading} />

      <main className="max-w-6xl mx-auto px-4 py-6 space-y-4">
        {data.jobs.length === 0 ? (
          <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
            <ClipboardCheck className="w-12 h-12 mx-auto mb-3 text-gray-400" />
            <h2 className="text-lg font-semibold text-gray-900">All caught up</h2>
            <p className="text-sm text-gray-500 mt-1">
              No open jobs assigned to {data.vendor_name}.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {data.jobs.map((job) => (
              <JobCard key={job.job_id} job={job} onUpdate={() => setActiveJob(job)} />
            ))}
          </div>
        )}
      </main>

      {activeJob && (
        <UpdateModal
          tokenId={tokenId}
          job={activeJob}
          onClose={() => setActiveJob(null)}
          onSaved={async () => {
            setActiveJob(null);
            await reload();
          }}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Header
// ----------------------------------------------------------------------------

function Header({ data, onReload, loading }: {
  data: VendorPortalListResponse;
  onReload: () => void;
  loading: boolean;
}) {
  const asOf = useMemo(() => {
    try {
      return new Date(data.as_of).toLocaleString('en-IN', {
        day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
      });
    } catch { return ''; }
  }, [data.as_of]);

  return (
    <header className="bg-white border-b border-gray-200">
      <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-mono text-gray-500 uppercase tracking-wide">
            Lens Lab Portal · Better Vision
          </p>
          <h1 className="text-xl font-semibold text-gray-900 mt-0.5">
            {data.vendor_name}
          </h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {data.total} open job{data.total === 1 ? '' : 's'}
            {asOf && ` · synced ${asOf}`}
          </p>
        </div>
        <button
          type="button"
          onClick={onReload}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          Refresh
        </button>
      </div>
    </header>
  );
}

// ----------------------------------------------------------------------------
// Job card
// ----------------------------------------------------------------------------

function JobCard({ job, onUpdate }: { job: VendorPortalJob; onUpdate: () => void }) {
  const due = job.expected_date
    ? new Date(job.expected_date).toLocaleDateString('en-IN', {
        day: '2-digit', month: 'short', year: 'numeric',
      })
    : '—';
  const StatusIcon = STATUS_ICONS[(job.vendor_status as VendorPortalStatus) || 'RECEIVED'] || ClipboardCheck;

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 hover:border-gray-300 transition">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-semibold text-gray-900 font-mono text-sm">
              {job.job_number || job.job_id}
            </h3>
            {job.vendor_order_id && (
              <span className="px-2 py-0.5 text-xs font-mono bg-gray-100 text-gray-700 rounded">
                #{job.vendor_order_id}
              </span>
            )}
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded border ${statusBadge(job.vendor_status)}`}
            >
              <StatusIcon className="w-3 h-3" />
              {STATUSES.find((s) => s.id === job.vendor_status)?.shortLabel || job.vendor_status || 'Pending'}
            </span>
          </div>
          <p className="text-sm text-gray-700 mt-2">
            <span className="font-mono text-gray-500 text-xs mr-2">For</span>
            {job.customer_initials}
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2 mt-3 text-xs">
            <Detail label="Frame" value={job.frame_brand || job.frame_model ? `${job.frame_brand || ''} ${job.frame_model || ''}`.trim() : '—'} />
            <Detail label="Lens" value={job.lens_type || '—'} />
            <Detail label="Coating" value={job.lens_coating || '—'} />
            <Detail label="Due" value={due} />
            {job.lens_diameter != null && <Detail label="Diameter" value={String(job.lens_diameter)} />}
            {job.fitting_height != null && <Detail label="Fit. Height" value={String(job.fitting_height)} />}
            {job.base_curve != null && <Detail label="Base Curve" value={String(job.base_curve)} />}
            {job.tint && <Detail label="Tint" value={job.tint} />}
          </div>
          {job.vendor_tracking_url && (
            <a
              href={job.vendor_tracking_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-3 inline-flex items-center gap-1 text-xs text-blue-700 hover:underline"
            >
              <ExternalLink className="w-3 h-3" />
              Tracking
            </a>
          )}
        </div>
        <button
          type="button"
          onClick={onUpdate}
          className="px-3 py-2 text-sm font-medium bg-gray-900 hover:bg-gray-700 text-white rounded-lg flex items-center gap-1.5 self-start"
        >
          <Send className="w-3.5 h-3.5" />
          Update status
        </button>
      </div>

      {(job.vendor_status_history?.length ?? 0) > 0 && (
        <details className="mt-3 pt-3 border-t border-gray-100">
          <summary className="text-xs font-medium text-gray-500 uppercase tracking-wide cursor-pointer">
            History · {job.vendor_status_history!.length}
          </summary>
          <ul className="mt-2 space-y-1.5 text-xs">
            {[...(job.vendor_status_history || [])]
              .reverse()
              .slice(0, 12)
              .map((h, i) => (
                <li key={i} className="flex items-start gap-2 text-gray-600">
                  <span className="font-mono">
                    {(() => {
                      try {
                        return new Date(h.logged_at).toLocaleString('en-IN', {
                          day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
                        });
                      } catch { return h.logged_at; }
                    })()}
                  </span>
                  <span className="font-medium text-gray-800">{h.status}</span>
                  {h.note && <span className="text-gray-500">— {h.note}</span>}
                  <span className="ml-auto text-[10px] uppercase font-mono text-gray-400">
                    {h.source === 'vendor_portal' ? 'lab' : 'store'}
                  </span>
                </li>
              ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-gray-500 uppercase tracking-wide font-mono">{label}</p>
      <p className="text-gray-900 font-medium truncate">{value}</p>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Update modal
// ----------------------------------------------------------------------------

function UpdateModal({
  tokenId, job, onClose, onSaved,
}: {
  tokenId: string;
  job: VendorPortalJob;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [status, setStatus] = useState<VendorPortalStatus>(
    (job.vendor_status as VendorPortalStatus) || 'RECEIVED',
  );
  const [note, setNote] = useState('');
  const [vendorOrderId, setVendorOrderId] = useState(job.vendor_order_id || '');
  const [trackingUrl, setTrackingUrl] = useState(job.vendor_tracking_url || '');
  const [submitting, setSubmitting] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const submit = async () => {
    setSubmitting(true);
    setErrMsg(null);
    try {
      await vendorPortalApi.postStatus(tokenId, job.job_id, {
        status,
        note: note.trim() || undefined,
        vendor_order_id: vendorOrderId.trim() || undefined,
        vendor_tracking_url: trackingUrl.trim() || undefined,
      });
      onSaved();
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to save. Try again.';
      setErrMsg(detail);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-gray-900 font-mono text-sm">
              {job.job_number || job.job_id}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">For {job.customer_initials}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700 text-2xl leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">
              Status
            </label>
            <div className="grid grid-cols-2 gap-2">
              {STATUSES.map((s) => {
                const Icon = STATUS_ICONS[s.id];
                const selected = status === s.id;
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => setStatus(s.id)}
                    className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium transition ${
                      selected
                        ? 'bg-gray-900 text-white border-gray-900'
                        : 'bg-white text-gray-700 border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <Icon className="w-4 h-4" />
                    {s.shortLabel}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">
              Note (optional)
            </label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={300}
              rows={2}
              placeholder="What changed? Anything to flag?"
              className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:border-gray-900 focus:ring-2 focus:ring-gray-900/20 outline-none resize-none"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">
                Vendor order ID
              </label>
              <input
                value={vendorOrderId}
                onChange={(e) => setVendorOrderId(e.target.value)}
                maxLength={60}
                placeholder="ZEISS-IN-2026-..."
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:border-gray-900 focus:ring-2 focus:ring-gray-900/20 outline-none font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">
                Tracking URL
              </label>
              <input
                value={trackingUrl}
                onChange={(e) => setTrackingUrl(e.target.value)}
                placeholder="https://..."
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:border-gray-900 focus:ring-2 focus:ring-gray-900/20 outline-none"
              />
            </div>
          </div>

          {errMsg && (
            <div className="flex items-center gap-2 p-2.5 bg-red-50 border border-red-200 rounded text-sm text-red-700">
              <AlertTriangle className="w-4 h-4 flex-shrink-0" />
              <span>{errMsg}</span>
            </div>
          )}
        </div>

        <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="px-4 py-2 text-sm font-medium bg-gray-900 hover:bg-gray-700 text-white rounded-lg flex items-center gap-1.5 disabled:opacity-50"
          >
            {submitting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-3.5 h-3.5" />
            )}
            {STATUSES.find((s) => s.id === status)?.cta || 'Submit'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Access denied
// ----------------------------------------------------------------------------

function AccessDenied({ msg }: { msg: string }) {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl border border-gray-200 p-8 max-w-md w-full text-center">
        <AlertTriangle className="w-12 h-12 mx-auto mb-3 text-amber-500" />
        <h1 className="text-lg font-semibold text-gray-900">Portal access denied</h1>
        <p className="text-sm text-gray-500 mt-2">{msg}</p>
        <p className="text-xs text-gray-400 mt-4">
          If you need a fresh portal link, contact your account manager at Better Vision.
        </p>
      </div>
    </div>
  );
}
