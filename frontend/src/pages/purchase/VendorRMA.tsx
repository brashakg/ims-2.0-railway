// ============================================================================
// IMS 2.0 - N4 Vendor RMA + credit-note reconciliation
// ============================================================================
// A restrained Vendor RMA workbench: list + create, plus a detail drawer that
// drives the lifecycle (authorize -> dispatch -> record credit notes -> close).
// Money is shown in rupees but is authoritative in paise server-side. Neutral /
// monochrome surface with a single accent; colour only carries semantic state.

import { useState, useEffect, useCallback, startTransition } from 'react';
import { Plus, X as XIcon, Truck, FileText, CheckCircle2, Ban } from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import api from '../../services/api/client';
import {
  vendorRmaApi,
  type VendorRMA,
  type RMALinePayload,
  type RMAReason,
} from '../../services/api/vendorRma';

interface Vendor {
  vendor_id: string;
  legal_name: string;
  trade_name: string;
}

const REASONS: { value: RMAReason; label: string }[] = [
  { value: 'DEFECTIVE', label: 'Defective' },
  { value: 'WRONG', label: 'Wrong item' },
  { value: 'EXCESS', label: 'Excess' },
  { value: 'WARRANTY', label: 'Warranty' },
  { value: 'NON_ADAPT', label: 'Non-adapt (lens)' },
];

const STATUS_CHIP: Record<string, string> = {
  DRAFT: 'bg-gray-100 text-gray-700',
  AUTHORIZED: 'bg-blue-50 text-blue-700',
  DISPATCHED: 'bg-amber-50 text-amber-700',
  CREDIT_RECEIVED: 'bg-indigo-50 text-indigo-700',
  CLOSED: 'bg-green-50 text-green-700',
  REJECTED: 'bg-red-50 text-red-700',
};

const inr = (rupees?: number) =>
  `₹${(rupees ?? 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

type BlankLine = RMALinePayload;

export function VendorRMA() {
  const toast = useToast();
  const { user } = useAuth();
  const activeStoreId = user?.activeStoreId || '';

  const [rmas, setRmas] = useState<VendorRMA[]>([]);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [selected, setSelected] = useState<VendorRMA | null>(null);

  // create form state
  const [vendorId, setVendorId] = useState('');
  const [lines, setLines] = useState<BlankLine[]>([
    { product_id: '', product_name: '', quantity: 1, reason: 'DEFECTIVE', unit_cost: 0 },
  ]);
  const [createNotes, setCreateNotes] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [rmaResp, vendorResp] = await Promise.all([
        vendorRmaApi.list({ store_id: activeStoreId || undefined, limit: 100 }),
        api.get('/vendors/', { params: { limit: 100 } }),
      ]);
      startTransition(() => {
        setRmas(rmaResp.rmas || []);
        setVendors((vendorResp.data?.vendors || vendorResp.data || []) as Vendor[]);
      });
    } catch {
      toast.error('Failed to load vendor RMAs');
    } finally {
      setLoading(false);
    }
  }, [activeStoreId, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshSelected = async (rmaId: string) => {
    try {
      const fresh = await vendorRmaApi.get(rmaId);
      setSelected(fresh);
      setRmas((prev) => prev.map((r) => (r.rma_id === rmaId ? fresh : r)));
    } catch {
      /* fail-soft: the list reload below covers it */
    }
  };

  const resetCreate = () => {
    setVendorId('');
    setLines([{ product_id: '', product_name: '', quantity: 1, reason: 'DEFECTIVE', unit_cost: 0 }]);
    setCreateNotes('');
  };

  const submitCreate = async () => {
    const vendor = vendors.find((v) => v.vendor_id === vendorId);
    if (!vendor) {
      toast.error('Pick a vendor');
      return;
    }
    const cleanLines = lines.filter((l) => l.product_name.trim() && l.quantity > 0);
    if (cleanLines.length === 0) {
      toast.error('Add at least one line');
      return;
    }
    setSaving(true);
    try {
      await vendorRmaApi.raise({
        vendor_id: vendor.vendor_id,
        vendor_name: vendor.trade_name || vendor.legal_name,
        store_id: activeStoreId,
        lines: cleanLines,
        notes: createNotes || undefined,
      });
      toast.success('RMA raised');
      setShowCreate(false);
      resetCreate();
      await load();
    } catch {
      toast.error('Could not raise RMA');
    } finally {
      setSaving(false);
    }
  };

  const expectedTotal = lines.reduce((s, l) => s + (l.quantity || 0) * (l.unit_cost || 0), 0);

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Vendor RMA</h1>
          <p className="text-sm text-gray-500">
            Authorise, courier, and reconcile vendor credit notes for returned stock.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-1.5 rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
        >
          <Plus size={16} /> New RMA
        </button>
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
            <tr>
              <th className="px-4 py-3">RMA</th>
              <th className="px-4 py-3">Vendor</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Expected</th>
              <th className="px-4 py-3 text-right">Received</th>
              <th className="px-4 py-3 text-right">Variance</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                  Loading...
                </td>
              </tr>
            ) : rmas.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                  No RMAs yet.
                </td>
              </tr>
            ) : (
              rmas.map((r) => (
                <tr
                  key={r.rma_id}
                  className="cursor-pointer hover:bg-gray-50"
                  onClick={() => setSelected(r)}
                >
                  <td className="px-4 py-3 font-mono text-xs text-gray-700">{r.rma_id}</td>
                  <td className="px-4 py-3 text-gray-900">{r.vendor_name}</td>
                  <td className="px-4 py-3">
                    <span
                      className={clsx(
                        'inline-flex rounded-full px-2 py-0.5 text-xs font-medium',
                        STATUS_CHIP[r.status] || 'bg-gray-100 text-gray-700',
                      )}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{inr(r.expected_credit_rupees)}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{inr(r.received_credit_rupees)}</td>
                  <td
                    className={clsx(
                      'px-4 py-3 text-right tabular-nums',
                      (r.variance_paise ?? 0) > 0 ? 'text-red-600' : 'text-gray-500',
                    )}
                  >
                    {inr(r.variance_rupees)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <Modal title="New Vendor RMA" onClose={() => setShowCreate(false)}>
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Vendor</label>
              <select
                value={vendorId}
                onChange={(e) => setVendorId(e.target.value)}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              >
                <option value="">Select vendor</option>
                {vendors.map((v) => (
                  <option key={v.vendor_id} value={v.vendor_id}>
                    {v.trade_name || v.legal_name}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-700">Lines</span>
                <button
                  type="button"
                  onClick={() =>
                    setLines((l) => [
                      ...l,
                      { product_id: '', product_name: '', quantity: 1, reason: 'DEFECTIVE', unit_cost: 0 },
                    ])
                  }
                  className="text-xs font-medium text-gray-700 hover:underline"
                >
                  + Add line
                </button>
              </div>
              {lines.map((ln, i) => (
                <div key={i} className="grid grid-cols-12 gap-2">
                  <input
                    placeholder="Product"
                    value={ln.product_name}
                    onChange={(e) =>
                      setLines((arr) => arr.map((x, j) => (j === i ? { ...x, product_name: e.target.value } : x)))
                    }
                    className="col-span-4 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
                  />
                  <input
                    type="number"
                    min={1}
                    value={ln.quantity}
                    onChange={(e) =>
                      setLines((arr) =>
                        arr.map((x, j) => (j === i ? { ...x, quantity: Number(e.target.value) } : x)),
                      )
                    }
                    className="col-span-2 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
                  />
                  <select
                    value={ln.reason}
                    onChange={(e) =>
                      setLines((arr) =>
                        arr.map((x, j) => (j === i ? { ...x, reason: e.target.value as RMAReason } : x)),
                      )
                    }
                    className="col-span-3 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
                  >
                    {REASONS.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                  <input
                    type="number"
                    min={0}
                    placeholder="Unit cost"
                    value={ln.unit_cost}
                    onChange={(e) =>
                      setLines((arr) =>
                        arr.map((x, j) => (j === i ? { ...x, unit_cost: Number(e.target.value) } : x)),
                      )
                    }
                    className="col-span-2 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => setLines((arr) => arr.filter((_, j) => j !== i))}
                    className="col-span-1 flex items-center justify-center text-gray-400 hover:text-red-600"
                    disabled={lines.length === 1}
                  >
                    <XIcon size={16} />
                  </button>
                </div>
              ))}
            </div>

            <textarea
              placeholder="Notes (optional)"
              value={createNotes}
              onChange={(e) => setCreateNotes(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              rows={2}
            />

            <div className="flex items-center justify-between border-t border-gray-100 pt-3">
              <span className="text-sm text-gray-500">
                Expected credit: <span className="font-medium text-gray-900">{inr(expectedTotal)}</span>
              </span>
              <button
                type="button"
                onClick={submitCreate}
                disabled={saving}
                className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
              >
                {saving ? 'Saving...' : 'Raise RMA'}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {selected && (
        <RMADetail
          rma={selected}
          onClose={() => setSelected(null)}
          onChanged={async () => {
            await refreshSelected(selected.rma_id);
            await load();
          }}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Detail drawer with lifecycle actions
// ----------------------------------------------------------------------------

function RMADetail({
  rma,
  onClose,
  onChanged,
}: {
  rma: VendorRMA;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const [vendorRmaNumber, setVendorRmaNumber] = useState('');
  const [carrier, setCarrier] = useState('');
  const [awb, setAwb] = useState('');
  const [cnNumber, setCnNumber] = useState('');
  const [cnAmount, setCnAmount] = useState(0);
  const [approvalToken, setApprovalToken] = useState('');

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      toast.success(ok);
      await onChanged();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      const msg =
        typeof detail === 'object' && detail
          ? ((detail as { message?: string; error?: string }).message ||
            (detail as { error?: string }).error ||
            'Action failed')
          : typeof detail === 'string'
            ? detail
            : 'Action failed';
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`RMA ${rma.rma_id}`} onClose={onClose} wide>
      <div className="space-y-5">
        <div className="flex items-center justify-between">
          <span
            className={clsx(
              'inline-flex rounded-full px-2.5 py-1 text-xs font-medium',
              STATUS_CHIP[rma.status] || 'bg-gray-100 text-gray-700',
            )}
          >
            {rma.status}
          </span>
          <span className="text-sm text-gray-500">{rma.vendor_name}</span>
        </div>

        <div className="grid grid-cols-3 gap-3 rounded-md bg-gray-50 p-3 text-sm">
          <Stat label="Expected" value={inr(rma.expected_credit_rupees)} />
          <Stat label="Received" value={inr(rma.received_credit_rupees)} />
          <Stat
            label="Variance"
            value={inr(rma.variance_rupees)}
            danger={(rma.variance_paise ?? 0) > 0}
          />
        </div>

        {rma.vendor_rma_number && (
          <div className="text-sm text-gray-600">
            Vendor RMA #: <span className="font-medium text-gray-900">{rma.vendor_rma_number}</span>
          </div>
        )}
        {rma.courier && (
          <div className="text-sm text-gray-600">
            Courier: <span className="font-medium text-gray-900">{rma.courier.carrier}</span> · AWB{' '}
            <span className="font-mono">{rma.courier.awb}</span>
          </div>
        )}

        {rma.credit_notes?.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-gray-500">
              Credit notes
            </div>
            <ul className="space-y-1 text-sm">
              {rma.credit_notes.map((c) => (
                <li key={c.credit_note_number} className="flex justify-between">
                  <span className="font-mono text-xs text-gray-700">{c.credit_note_number}</span>
                  <span className="tabular-nums">{inr((c.received_paise ?? 0) / 100)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* --- lifecycle actions, gated by current status --- */}
        {rma.status === 'DRAFT' && (
          <ActionRow icon={<CheckCircle2 size={16} />} title="Authorize">
            <input
              placeholder="Vendor RMA number"
              value={vendorRmaNumber}
              onChange={(e) => setVendorRmaNumber(e.target.value)}
              className="flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
            <button
              type="button"
              disabled={busy || !vendorRmaNumber.trim()}
              onClick={() =>
                run(() => vendorRmaApi.authorize(rma.rma_id, vendorRmaNumber.trim()), 'Authorized')
              }
              className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Authorize
            </button>
          </ActionRow>
        )}

        {rma.status === 'AUTHORIZED' && (
          <ActionRow icon={<Truck size={16} />} title="Dispatch">
            <input
              placeholder="Carrier"
              value={carrier}
              onChange={(e) => setCarrier(e.target.value)}
              className="w-32 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
            <input
              placeholder="AWB / tracking"
              value={awb}
              onChange={(e) => setAwb(e.target.value)}
              className="flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
            <button
              type="button"
              disabled={busy || !carrier.trim() || !awb.trim()}
              onClick={() =>
                run(
                  () => vendorRmaApi.dispatch(rma.rma_id, { carrier: carrier.trim(), awb: awb.trim() }),
                  'Dispatched',
                )
              }
              className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Dispatch
            </button>
          </ActionRow>
        )}

        {(rma.status === 'DISPATCHED' || rma.status === 'CREDIT_RECEIVED') && (
          <ActionRow icon={<FileText size={16} />} title="Record credit note">
            <input
              placeholder="Credit note #"
              value={cnNumber}
              onChange={(e) => setCnNumber(e.target.value)}
              className="w-32 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
            <input
              type="number"
              min={0}
              placeholder="Amount (₹)"
              value={cnAmount}
              onChange={(e) => setCnAmount(Number(e.target.value))}
              className="w-28 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
            <input
              placeholder="Approval token (if large)"
              value={approvalToken}
              onChange={(e) => setApprovalToken(e.target.value)}
              className="flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
            <button
              type="button"
              disabled={busy || !cnNumber.trim() || cnAmount <= 0}
              onClick={() =>
                run(
                  () =>
                    vendorRmaApi.recordCreditNote(rma.rma_id, {
                      credit_note_number: cnNumber.trim(),
                      received_amount: cnAmount,
                      approval_token: approvalToken.trim() || undefined,
                    }),
                  'Credit recorded',
                )
              }
              className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Record
            </button>
          </ActionRow>
        )}

        {rma.status === 'CREDIT_RECEIVED' && (
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => run(() => vendorRmaApi.close(rma.rma_id), 'RMA closed')}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Close (reconciled)
            </button>
            {(rma.variance_paise ?? 0) > 0 && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  run(() => vendorRmaApi.close(rma.rma_id, { write_off_variance: true }), 'Closed; variance written off')
                }
                className="rounded-md border border-amber-300 px-3 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-50"
              >
                Close + write off variance
              </button>
            )}
          </div>
        )}

        {['DRAFT', 'AUTHORIZED', 'DISPATCHED'].includes(rma.status) && (
          <button
            type="button"
            disabled={busy}
            onClick={() => run(() => vendorRmaApi.reject(rma.rma_id), 'RMA rejected')}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-red-600 hover:underline disabled:opacity-50"
          >
            <Ban size={14} /> Reject RMA
          </button>
        )}
      </div>
    </Modal>
  );
}

// ----------------------------------------------------------------------------
// Small presentational helpers
// ----------------------------------------------------------------------------

function Modal({
  title,
  onClose,
  wide,
  children,
}: {
  title: string;
  onClose: () => void;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-16">
      <div className={clsx('w-full rounded-lg bg-white shadow-xl', wide ? 'max-w-2xl' : 'max-w-xl')}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h2 className="text-base font-semibold text-gray-900">{title}</h2>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <XIcon size={18} />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

function Stat({ label, value, danger }: { label: string; value: string; danger?: boolean }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-gray-400">{label}</div>
      <div className={clsx('tabular-nums font-medium', danger ? 'text-red-600' : 'text-gray-900')}>
        {value}
      </div>
    </div>
  );
}

function ActionRow({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-gray-200 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-sm font-medium text-gray-700">
        {icon} {title}
      </div>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

export default VendorRMA;
