// ============================================================================
// IMS 2.0 — Lens Fitting Handoff Modal (Phase 6.8)
// ============================================================================
// Sales-staff → Workshop handoff form that captures the physical fitting
// measurements the technician needs to cut / edge / tint / mount a lens
// into a frame. Opens immediately after a prescription order is created
// in POS and MUST be completed (confirmation checkbox) before the order
// is finalised.
//
// Fields were spec'd by Avinash 2026-04-21 (image attachment):
//   DIA · FH · B SIZE · DBL · TINT · BASE CURVE · COATING · OTHER
//   + order date / time / ordered_by (auto-filled)
//   + expected date of receiving lens (from vendor)
//   + vendor order id (purchase order reference for the lens supplier)
//   + "I confirm the power details and product details are perfect" checkbox

import { useEffect, useState } from 'react';
import { X, Check } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

export interface LensFittingFormValue {
  dia: string;
  fh: string;
  b_size: string;
  dbl: string;
  tint: string;
  base_curve: string;
  coating: string;
  other: string;
  vendor_order_id: string;
  order_date: string;
  order_time: string;
  ordered_by: string;
  ordered_by_name: string;
  expected_lens_receive_date: string;
  confirmed_by_sales: boolean;
  confirmed_at: string;
}

interface LensFittingFormModalProps {
  /** Pre-fill coating from the lens configurator if already chosen. */
  prefilledCoating?: string;
  /** Submit handler — the parent POS layout calls the backend PATCH. */
  onSave: (v: LensFittingFormValue) => Promise<void> | void;
  onBack: () => void;
  /** True while the parent is awaiting the backend response. */
  isSaving?: boolean;
}

const today = () => new Date().toISOString().slice(0, 10);
const nowHHMM = () => {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};
const defaultReceive = () => {
  // Lens suppliers typically deliver in 3-5 working days; default +5
  const d = new Date();
  d.setDate(d.getDate() + 5);
  return d.toISOString().slice(0, 10);
};

export function LensFittingFormModal({
  prefilledCoating,
  onSave,
  onBack,
  isSaving = false,
}: LensFittingFormModalProps) {
  const { user } = useAuth();
  const [v, setV] = useState<LensFittingFormValue>({
    dia: '',
    fh: '',
    b_size: '',
    dbl: '',
    tint: '',
    base_curve: '',
    coating: prefilledCoating || '',
    other: '',
    vendor_order_id: '',
    order_date: today(),
    order_time: nowHHMM(),
    ordered_by: user?.id || '',
    ordered_by_name: user?.name || '',
    expected_lens_receive_date: defaultReceive(),
    confirmed_by_sales: false,
    confirmed_at: '',
  });
  const [err, setErr] = useState<string | null>(null);

  // Keep ordered_by in sync if the auth context loads after mount
  useEffect(() => {
    if (user?.id && !v.ordered_by) {
      setV(prev => ({ ...prev, ordered_by: user.id || '', ordered_by_name: user.name || '' }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  const field = (key: keyof LensFittingFormValue, value: string | boolean) =>
    setV(prev => ({ ...prev, [key]: value } as LensFittingFormValue));

  const handleSave = async () => {
    if (!v.confirmed_by_sales) {
      setErr('Please confirm the power and product details are correct before saving.');
      return;
    }
    setErr(null);
    const payload: LensFittingFormValue = {
      ...v,
      confirmed_at: new Date().toISOString(),
    };
    await onSave(payload);
  };

  // Labels match the image exactly (red, all-caps, monospace feel for numeric fields)
  const LensField = ({
    label,
    k,
    placeholder,
  }: {
    label: string;
    k: keyof LensFittingFormValue;
    placeholder?: string;
  }) => (
    <div className="grid grid-cols-3 items-center gap-3">
      <label className="text-sm font-semibold text-bv-red-600 uppercase tracking-wide">
        {label}
      </label>
      <input
        type="text"
        value={String(v[k] ?? '')}
        onChange={(e) => field(k, e.target.value)}
        placeholder={placeholder || ''}
        className="col-span-2 px-3 py-2 border border-gray-300 rounded text-sm text-gray-900 focus:border-bv-red-400 focus:outline-none"
      />
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h2 className="text-base font-semibold text-gray-900">Lens Detail</h2>
          <button
            onClick={onBack}
            className="p-1 hover:bg-gray-100 rounded text-gray-500"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-3">
          <LensField label="DIA" k="dia" placeholder="e.g. 65, 70" />
          <LensField label="FH" k="fh" placeholder="Fitting height (mm)" />
          <LensField label="B SIZE" k="b_size" placeholder="Vertical (mm)" />
          <LensField label="DBL" k="dbl" placeholder="Bridge (mm)" />
          <LensField label="TINT" k="tint" placeholder="Colour / %" />
          <LensField label="BASE CURVE" k="base_curve" placeholder="e.g. 6, 8" />
          <LensField label="COATING" k="coating" placeholder="AR / Blue-cut / Photochromic" />
          <LensField label="OTHER" k="other" placeholder="Any additional notes" />

          <div className="h-px bg-gray-200 my-2" />

          {/* Metadata — mostly auto-filled but editable */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">Order Date</label>
              <input
                type="date"
                value={v.order_date}
                onChange={(e) => field('order_date', e.target.value)}
                className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-gray-900"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">Order Time</label>
              <input
                type="time"
                value={v.order_time}
                onChange={(e) => field('order_time', e.target.value)}
                className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-gray-900"
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-medium text-gray-600 block mb-1">Ordered By</label>
            <input
              type="text"
              value={v.ordered_by_name}
              onChange={(e) => field('ordered_by_name', e.target.value)}
              placeholder="Sales staff name"
              className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-gray-900 bg-gray-50"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-gray-600 block mb-1">
              Expected Date of Receiving Lens
            </label>
            <input
              type="date"
              value={v.expected_lens_receive_date}
              onChange={(e) => field('expected_lens_receive_date', e.target.value)}
              className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-gray-900"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-gray-600 block mb-1">
              Vendor Order ID
              <span className="text-gray-400 font-normal ml-1">
                (PO reference with lens supplier)
              </span>
            </label>
            <input
              type="text"
              value={v.vendor_order_id}
              onChange={(e) => field('vendor_order_id', e.target.value)}
              placeholder="e.g. ZEISS-24-00123"
              className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-gray-900"
            />
          </div>

          {/* Confirmation checkbox — required */}
          <label className="flex items-start gap-2 mt-3 p-3 bg-amber-50 border border-amber-300 rounded cursor-pointer">
            <input
              type="checkbox"
              checked={v.confirmed_by_sales}
              onChange={(e) => field('confirmed_by_sales', e.target.checked)}
              className="mt-0.5 rounded border-gray-400"
            />
            <span className="text-sm text-amber-900 font-medium">
              I confirm the power details and product details are perfect.
            </span>
          </label>

          {err && (
            <p className="text-sm text-red-600 mt-2 font-medium">{err}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-center gap-3 px-5 py-4 border-t border-gray-200 bg-gray-50">
          <button
            onClick={handleSave}
            disabled={isSaving}
            className={`px-8 py-2 rounded font-semibold text-sm transition-colors ${
              isSaving
                ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                : 'bg-bv-red-600 text-white hover:bg-bv-red-700'
            }`}
          >
            {isSaving ? (
              <span className="flex items-center gap-2">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
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
                    d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
                  />
                </svg>
                Saving
              </span>
            ) : (
              <span className="flex items-center gap-2">
                <Check className="w-4 h-4" />
                Save
              </span>
            )}
          </button>
          <button
            onClick={onBack}
            disabled={isSaving}
            className="px-8 py-2 rounded font-semibold text-sm bg-bv-red-600 text-white hover:bg-bv-red-700 disabled:opacity-60"
          >
            Back
          </button>
        </div>
      </div>
    </div>
  );
}

export default LensFittingFormModal;
