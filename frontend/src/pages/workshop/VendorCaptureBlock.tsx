// ============================================================================
// IMS 2.0 - Workshop: vendor / lens-lab capture block
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet).

import { useState, useEffect } from 'react';
import { workshopApi, vendorsApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import type { Job } from './shared';

// ============================================================================
// Vendor capture block — admin stamps the lens lab + their order ID +
// tracking URL on a workshop job. Setting vendor_id for the first time is
// what makes the public vendor portal aware of the job.
// ============================================================================

export function VendorCaptureBlock({ job, onSaved }: { job: Job; onSaved: () => void }) {
  const toast = useToast();
  const j = job as Job & {
    vendor_id?: string;
    vendor_name?: string;
    vendor_order_id?: string;
    vendor_tracking_url?: string;
    vendor_status?: string;
    vendor_history?: Array<{
      status: string;
      note?: string;
      source: string;
      at: string;
    }>;
  };

  const [vendors, setVendors] = useState<Array<{ vendor_id: string; legal_name?: string; trade_name?: string; name?: string }>>([]);
  const [loadingVendors, setLoadingVendors] = useState(true);
  const [vendorId, setVendorId] = useState(j.vendor_id || '');
  const [vendorOrderId, setVendorOrderId] = useState(j.vendor_order_id || '');
  const [trackingUrl, setTrackingUrl] = useState(j.vendor_tracking_url || '');
  const [saving, setSaving] = useState(false);
  const [showStatusForm, setShowStatusForm] = useState(false);
  const [statusValue, setStatusValue] = useState('');
  const [statusNote, setStatusNote] = useState('');

  // Reset local form state when the user picks a different job
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setVendorId(j.vendor_id || '');
    setVendorOrderId(j.vendor_order_id || '');
    setTrackingUrl(j.vendor_tracking_url || '');
    setShowStatusForm(false);
    setStatusValue('');
    setStatusNote('');
  }, [j.id, j.vendor_id, j.vendor_order_id, j.vendor_tracking_url]);

  // Lazy-load the vendor directory once
  useEffect(() => {
    let cancelled = false;
    vendorsApi
      .getVendors({ is_active: true })
      .then((r) => {
        if (cancelled) return;
        const list = (r as { vendors?: unknown[] })?.vendors ?? r ?? [];
        setVendors(Array.isArray(list) ? (list as typeof vendors) : []);
      })
      .catch(() => {
        if (!cancelled) setVendors([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingVendors(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const dirty =
    vendorId !== (j.vendor_id || '') ||
    vendorOrderId !== (j.vendor_order_id || '') ||
    trackingUrl !== (j.vendor_tracking_url || '');

  const handleSave = async () => {
    setSaving(true);
    try {
      await workshopApi.patchJobVendor(job.id, {
        vendor_id: vendorId || null,
        vendor_order_id: vendorOrderId || null,
        vendor_tracking_url: trackingUrl || null,
      });
      toast.success('Vendor details saved');
      onSaved();
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to save vendor details';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const handlePostStatus = async () => {
    if (!statusValue.trim()) {
      toast.error('Pick a status');
      return;
    }
    setSaving(true);
    try {
      await workshopApi.postJobVendorStatus(job.id, {
        status: statusValue.trim(),
        note: statusNote.trim() || undefined,
      });
      toast.success('Vendor status logged');
      setShowStatusForm(false);
      setStatusValue('');
      setStatusNote('');
      onSaved();
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to log vendor status';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const VENDOR_STATUS_OPTIONS = ['ACKNOWLEDGED', 'IN_PROGRESS', 'DISPATCHED', 'DELIVERED', 'DELAYED', 'CANCELLED'];

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50/60 p-3">
      <div className="flex items-center justify-between mb-2">
        <p className="text-sm font-medium text-gray-900">Vendor / lens lab</p>
        {j.vendor_status && (
          <span className="px-2 py-0.5 rounded text-xs font-semibold bg-white border border-gray-300 text-gray-700">
            {j.vendor_status}
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-2 mb-2">
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-0.5">Lens lab</label>
          <select
            value={vendorId}
            onChange={(e) => setVendorId(e.target.value)}
            disabled={loadingVendors}
            className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded bg-white"
            title="Lens lab vendor"
          >
            <option value="">— Select vendor —</option>
            {vendors.map((v) => (
              <option key={v.vendor_id} value={v.vendor_id}>
                {v.trade_name || v.legal_name || v.name || v.vendor_id}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-0.5">Vendor order ID</label>
          <input
            type="text"
            value={vendorOrderId}
            onChange={(e) => setVendorOrderId(e.target.value)}
            placeholder="e.g. ZL-2026-44871"
            className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-0.5">Tracking URL</label>
          <input
            type="url"
            value={trackingUrl}
            onChange={(e) => setTrackingUrl(e.target.value)}
            placeholder="https://lab.example/track/..."
            className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
          />
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || saving}
          className="px-3 py-1.5 bg-bv-red-600 hover:bg-bv-red-700 text-white text-xs font-semibold rounded disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save vendor details'}
        </button>
        {j.vendor_id && (
          <button
            type="button"
            onClick={() => setShowStatusForm((s) => !s)}
            className="px-3 py-1.5 bg-white hover:bg-gray-50 text-gray-700 text-xs font-semibold rounded border border-gray-300"
          >
            {showStatusForm ? 'Cancel status update' : 'Log vendor status'}
          </button>
        )}
      </div>

      {showStatusForm && (
        <div className="mt-2 p-2 bg-white border border-gray-200 rounded space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <select
              value={statusValue}
              onChange={(e) => setStatusValue(e.target.value)}
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
              title="Vendor status"
            >
              <option value="">— Status —</option>
              {VENDOR_STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={statusNote}
              onChange={(e) => setStatusNote(e.target.value)}
              placeholder="Note (optional)"
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
            />
          </div>
          <button
            type="button"
            onClick={handlePostStatus}
            disabled={!statusValue.trim() || saving}
            className="px-3 py-1.5 bg-bv-red-600 hover:bg-bv-red-700 text-white text-xs font-semibold rounded disabled:opacity-50"
          >
            {saving ? 'Posting…' : 'Post status'}
          </button>
        </div>
      )}

      {Array.isArray(j.vendor_history) && j.vendor_history.length > 0 && (
        <details className="mt-2">
          <summary className="text-xs font-medium text-gray-600 cursor-pointer">
            Vendor history ({j.vendor_history.length})
          </summary>
          <ul className="mt-1 space-y-1">
            {j.vendor_history
              .slice()
              .reverse()
              .map((h, i) => (
                <li key={i} className="text-xs text-gray-700 flex gap-2">
                  <span className="font-mono text-gray-400">
                    {h.at ? new Date(h.at).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'}
                  </span>
                  <span className="font-semibold text-gray-800">{h.status}</span>
                  {h.note && <span className="text-gray-600">· {h.note}</span>}
                  <span className="text-gray-400 ml-auto">[{h.source}]</span>
                </li>
              ))}
          </ul>
        </details>
      )}
    </div>
  );
}
