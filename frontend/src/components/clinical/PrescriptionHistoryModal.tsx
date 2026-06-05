// ============================================================================
// IMS 2.0 — Prescription history modal with progression Δ arrows
// ============================================================================
// Lists every Rx the customer has, newest-first, plus the adjacent-visit
// deltas surfaced from /prescriptions/customer/{id}/progression. Each
// delta row shows ↑ (myopia getting worse), ↓ (improving), or → (stable)
// per eye/parameter, so the optometrist can spot accelerating myopia at
// a glance.

import { useEffect, useState } from 'react';
import { X, Eye, ArrowUp, ArrowDown, ArrowRight, Loader2, Layers } from 'lucide-react';
import { prescriptionApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

interface Props {
  customerId: string;
  customerName?: string;
  isOpen: boolean;
  onClose: () => void;
  onOpenVersions?: (prescriptionId: string) => void;
}

interface RxRow {
  id: string;
  testDate?: string;
  rightEye?: { sphere?: number | null; cylinder?: number | null; axis?: number | null; add?: number | null };
  leftEye?: { sphere?: number | null; cylinder?: number | null; axis?: number | null; add?: number | null };
  optometristName?: string;
  status?: string;
}

// CLI-10 fix: backend progression_diffs() returns keys with `_delta` suffix
// (sphere_delta, cylinder_delta, axis_delta, addition_delta) and date fields
// as from_visit_at / to_visit_at.  The old interface matched neither, causing
// the delta section to render entirely blank.
interface Delta {
  from_date?: string;         // alias accepted for backwards-compat
  to_date?: string;           // alias accepted for backwards-compat
  from_visit_at?: string;     // actual backend key
  to_visit_at?: string;       // actual backend key
  right_eye: {
    sphere?: number | null;   sphere_delta?: number | null;
    cylinder?: number | null; cylinder_delta?: number | null;
    axis?: number | null;     axis_delta?: number | null;
    addition?: number | null; addition_delta?: number | null;
  };
  left_eye: {
    sphere?: number | null;   sphere_delta?: number | null;
    cylinder?: number | null; cylinder_delta?: number | null;
    axis?: number | null;     axis_delta?: number | null;
    addition?: number | null; addition_delta?: number | null;
  };
  pd?: number | null;
}

export function PrescriptionHistoryModal({
  customerId,
  customerName,
  isOpen,
  onClose,
  onOpenVersions,
}: Props) {
  const toast = useToast();
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState<RxRow[]>([]);
  const [deltas, setDeltas] = useState<Delta[]>([]);

  useEffect(() => {
    if (!isOpen || !customerId) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      prescriptionApi.getPrescriptions(customerId).catch(() => null),
      prescriptionApi.getProgression(customerId).catch(() => null),
    ])
      .then(([rxResp, progResp]) => {
        if (cancelled) return;
        const list = Array.isArray(rxResp)
          ? rxResp
          : (rxResp as { prescriptions?: RxRow[] })?.prescriptions || [];
        // Sort newest-first
        const sorted = [...list].sort((a: RxRow, b: RxRow) => {
          const da = a.testDate ? new Date(a.testDate).getTime() : 0;
          const db = b.testDate ? new Date(b.testDate).getTime() : 0;
          return db - da;
        });
        setHistory(sorted);
        setDeltas(progResp?.deltas || []);
      })
      .catch(() => {
        if (!cancelled) toast.error('Failed to load prescription history');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, customerId]);

  if (!isOpen) return null;

  const fmtPower = (v: number | null | undefined) => {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    return v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Eye className="w-5 h-5 text-blue-600" />
            <div>
              <h2 className="font-semibold text-gray-900">Prescription history</h2>
              <p className="text-xs text-gray-500">
                {customerName ? `for ${customerName}` : 'Visit-by-visit Rx with progression deltas'}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 text-gray-500 hover:bg-gray-100 rounded">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
            </div>
          ) : history.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <Eye className="w-12 h-12 mx-auto mb-3 opacity-40" />
              <p>No prescriptions on file.</p>
            </div>
          ) : (
            <>
              {/* Progression deltas */}
              {deltas.length > 0 && (
                <section>
                  <h3 className="text-sm font-medium text-gray-700 mb-2">Progression</h3>
                  <p className="text-xs text-gray-500 mb-3">
                    Change between adjacent finalized visits. Watch for sustained ↑ in SPH (myopia
                    accelerating) or growing CYL (developing astigmatism).
                  </p>
                  <div className="space-y-2">
                    {deltas.map((d, i) => (
                      <DeltaRow key={i} delta={d} />
                    ))}
                  </div>
                </section>
              )}

              {/* History list */}
              <section>
                <h3 className="text-sm font-medium text-gray-700 mb-2">
                  All prescriptions ({history.length})
                </h3>
                <div className="space-y-2">
                  {history.map((rx) => (
                    <div
                      key={rx.id}
                      className="border border-gray-200 rounded-lg p-3 hover:bg-gray-50"
                    >
                      <div className="flex items-start justify-between mb-2">
                        <div>
                          <p className="text-sm font-medium text-gray-900">
                            {rx.testDate
                              ? new Date(rx.testDate).toLocaleDateString('en-IN', {
                                  day: '2-digit',
                                  month: 'short',
                                  year: 'numeric',
                                })
                              : '—'}
                          </p>
                          <p className="text-xs text-gray-500">
                            {rx.optometristName ? `By ${rx.optometristName}` : ''}
                            {rx.status ? ` · ${rx.status}` : ''}
                          </p>
                        </div>
                        {onOpenVersions && rx.id && (
                          <button
                            type="button"
                            onClick={() => onOpenVersions(rx.id)}
                            className="px-2 py-1 text-xs font-semibold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 rounded flex items-center gap-1"
                          >
                            <Layers className="w-3 h-3" />
                            Versions
                          </button>
                        )}
                      </div>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                        <div className="rounded bg-gray-50 px-2 py-1.5">
                          <p className="text-gray-500 mb-0.5">Right (OD)</p>
                          <p className="font-mono">
                            {fmtPower(rx.rightEye?.sphere)} / {fmtPower(rx.rightEye?.cylinder)} ×{' '}
                            {rx.rightEye?.axis ?? '—'}
                            {rx.rightEye?.add !== null && rx.rightEye?.add !== undefined && (
                              <> · ADD {fmtPower(rx.rightEye?.add)}</>
                            )}
                          </p>
                        </div>
                        <div className="rounded bg-gray-50 px-2 py-1.5">
                          <p className="text-gray-500 mb-0.5">Left (OS)</p>
                          <p className="font-mono">
                            {fmtPower(rx.leftEye?.sphere)} / {fmtPower(rx.leftEye?.cylinder)} ×{' '}
                            {rx.leftEye?.axis ?? '—'}
                            {rx.leftEye?.add !== null && rx.leftEye?.add !== undefined && (
                              <> · ADD {fmtPower(rx.leftEye?.add)}</>
                            )}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-gray-200 bg-gray-50 flex items-center justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function DeltaRow({ delta }: { delta: Delta }) {
  const fmtArrow = (n: number | null | undefined) => {
    if (n === null || n === undefined || !Number.isFinite(n)) {
      return <span className="text-gray-400">—</span>;
    }
    if (Math.abs(n) < 0.125) {
      return (
        <span className="inline-flex items-center gap-0.5 text-gray-500">
          <ArrowRight className="w-3 h-3" />
          stable
        </span>
      );
    }
    if (n < 0) {
      // SPH going more negative = myopia worsening (e.g. -1.00 → -1.50, delta = -0.50)
      return (
        <span className="inline-flex items-center gap-0.5 text-red-600 font-semibold">
          <ArrowUp className="w-3 h-3" />
          {n.toFixed(2)}
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-0.5 text-green-600 font-semibold">
        <ArrowDown className="w-3 h-3" />+{n.toFixed(2)}
      </span>
    );
  };

  const fmtCyl = (n: number | null | undefined) => {
    if (n === null || n === undefined || !Number.isFinite(n)) return <span className="text-gray-400">—</span>;
    if (Math.abs(n) < 0.125) {
      return (
        <span className="inline-flex items-center gap-0.5 text-gray-500">
          <ArrowRight className="w-3 h-3" />
          stable
        </span>
      );
    }
    return (
      <span
        className={clsx(
          'inline-flex items-center gap-0.5 font-semibold',
          n < 0 ? 'text-amber-600' : 'text-blue-600',
        )}
      >
        {n < 0 ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />}
        {n > 0 ? `+${n.toFixed(2)}` : n.toFixed(2)}
      </span>
    );
  };

  // CLI-10: read `_delta`-suffixed keys (backend) OR bare keys (old shape)
  const rd = (eye: Delta['right_eye'] | Delta['left_eye'], field: 'sphere' | 'cylinder' | 'axis' | 'addition') => {
    const withSuffix = (eye as any)[`${field}_delta`];
    const bare = (eye as any)[field];
    const v = withSuffix !== undefined ? withSuffix : bare;
    return (v === null || v === undefined) ? null : Number(v);
  };

  const fmtDate = (s?: string) =>
    s
      ? new Date(s).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' })
      : '—';

  // Accept both from_date (old) and from_visit_at (current backend)
  const fromDate = delta.from_date ?? delta.from_visit_at;
  const toDate   = delta.to_date   ?? delta.to_visit_at;

  const rSph = rd(delta.right_eye, 'sphere');
  const rCyl = rd(delta.right_eye, 'cylinder');
  const rAdd = rd(delta.right_eye, 'addition');
  const lSph = rd(delta.left_eye, 'sphere');
  const lCyl = rd(delta.left_eye, 'cylinder');
  const lAdd = rd(delta.left_eye, 'addition');

  return (
    <div className="border border-gray-200 rounded-lg p-2.5 bg-white">
      <p className="text-xs text-gray-500 mb-2">
        {fmtDate(fromDate)} &rarr; {fmtDate(toDate)}
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
        <div className="bg-gray-50 rounded px-2 py-1.5">
          <p className="text-gray-500 mb-0.5">Right (OD)</p>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5">
            <span><span className="text-gray-400">SPH</span> {fmtArrow(rSph)}</span>
            <span><span className="text-gray-400">CYL</span> {fmtCyl(rCyl)}</span>
            {rAdd !== null && rAdd !== undefined && (
              <span><span className="text-gray-400">ADD</span> {fmtArrow(rAdd)}</span>
            )}
          </div>
        </div>
        <div className="bg-gray-50 rounded px-2 py-1.5">
          <p className="text-gray-500 mb-0.5">Left (OS)</p>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5">
            <span><span className="text-gray-400">SPH</span> {fmtArrow(lSph)}</span>
            <span><span className="text-gray-400">CYL</span> {fmtCyl(lCyl)}</span>
            {lAdd !== null && lAdd !== undefined && (
              <span><span className="text-gray-400">ADD</span> {fmtArrow(lAdd)}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default PrescriptionHistoryModal;
