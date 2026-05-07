// ============================================================================
// IMS 2.0 — Walkout Result Panel (Pune Incentive Module i, Phase 3)
// ============================================================================
// Lets the user mark a walkout as DUE / NEGATIVE / CONVERTED. CONVERTED
// requires an order_id; the "Convert to order" CTA hands off to POS
// pre-filled with the customer.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShoppingCart, Loader2 } from 'lucide-react';
import { walkoutsApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import type { Walkout, WalkoutResultValue } from '../../types';

interface ResultPanelProps {
  walkout: Walkout;
  canEdit: boolean;
  onChanged: (w: Walkout) => void;
}

export function WalkoutResultBadge({ value }: { value?: string | null }) {
  if (!value) {
    return <span className="text-xs px-2 py-0.5 rounded-full border bg-gray-50 text-gray-500 border-gray-200">No result yet</span>;
  }
  const cls =
    value === 'CONVERTED' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
    value === 'NEGATIVE' ? 'bg-rose-50 text-rose-700 border-rose-200' :
    'bg-blue-50 text-blue-700 border-blue-200';
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full border ${cls}`}>{value}</span>
  );
}

export function ResultPanel({ walkout, canEdit, onChanged }: ResultPanelProps) {
  const toast = useToast();
  const navigate = useNavigate();

  const [busy, setBusy] = useState(false);
  const [orderInput, setOrderInput] = useState(walkout.converted_order_id || '');
  const [showConvertForm, setShowConvertForm] = useState(false);

  const setResult = async (
    result: WalkoutResultValue,
    orderId?: string,
  ) => {
    setBusy(true);
    try {
      const updated = await walkoutsApi.setResult(walkout.walkout_id, {
        result,
        converted_order_id: orderId,
      });
      onChanged(updated);
      toast.success(`Walkout marked ${result}`);
      setShowConvertForm(false);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Could not save result';
      toast.error(typeof msg === 'string' ? msg : 'Could not save result');
    } finally {
      setBusy(false);
    }
  };

  const handleConvertToOrder = () => {
    // Hand off to POS pre-filled with this customer + walkout_id so the
    // POS confirm step can call /walkouts/{id}/result on success.
    const params = new URLSearchParams();
    if (walkout.customer_id) params.set('customer_id', walkout.customer_id);
    params.set('mobile', walkout.mobile);
    params.set('walkout_id', walkout.walkout_id);
    params.set('return_to', `/walkouts/${walkout.walkout_id}`);
    navigate(`/pos?${params.toString()}`);
  };

  const isConverted = walkout.result === 'CONVERTED';
  const currentResult = walkout.result;

  return (
    <section className="card p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Outcome</h2>
        <WalkoutResultBadge value={currentResult} />
      </div>

      {isConverted && walkout.converted_order_id && (
        <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-3 py-2">
          Linked to order <span className="font-mono font-medium">{walkout.converted_order_id}</span>
        </div>
      )}

      {canEdit && (
        <>
          <div className="flex flex-wrap gap-2">
            <ResultButton
              label="Still due"
              active={currentResult === 'DUE'}
              onClick={() => setResult('DUE')}
              disabled={busy}
              className="border-blue-300 text-blue-700 hover:bg-blue-50"
            />
            <ResultButton
              label="Negative"
              active={currentResult === 'NEGATIVE'}
              onClick={() => setResult('NEGATIVE')}
              disabled={busy}
              className="border-rose-300 text-rose-700 hover:bg-rose-50"
            />
            <ResultButton
              label="Converted"
              active={currentResult === 'CONVERTED'}
              onClick={() => setShowConvertForm(true)}
              disabled={busy}
              className="border-emerald-300 text-emerald-700 hover:bg-emerald-50"
            />
            {!isConverted && (
              <button
                type="button"
                onClick={handleConvertToOrder}
                disabled={busy}
                className="text-xs px-3 py-1 rounded border border-bv-red-300 text-bv-red-700 hover:bg-bv-red-50 inline-flex items-center gap-1 disabled:opacity-50"
              >
                <ShoppingCart className="w-3 h-3" />
                Convert in POS
              </button>
            )}
          </div>

          {showConvertForm && (
            <div className="border border-emerald-200 rounded p-3 bg-emerald-50/30">
              <div className="text-sm font-medium text-gray-700 mb-2">
                Mark as CONVERTED — link to order
              </div>
              <input
                type="text"
                value={orderInput}
                onChange={e => setOrderInput(e.target.value)}
                placeholder="ORD-... or invoice number"
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm font-mono mb-2"
              />
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  className="btn-secondary text-xs px-3 py-1"
                  onClick={() => { setShowConvertForm(false); setOrderInput(walkout.converted_order_id || ''); }}
                  disabled={busy}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn-primary text-xs px-3 py-1 inline-flex items-center gap-1"
                  onClick={() => setResult('CONVERTED', orderInput.trim())}
                  disabled={busy || !orderInput.trim()}
                >
                  {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                  Confirm
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function ResultButton({
  label, active, onClick, disabled, className,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  className: string;
}) {
  const activeCls = active ? 'ring-2 ring-offset-1 ring-bv-red-300' : '';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`text-xs px-3 py-1 rounded border inline-flex items-center gap-1 disabled:opacity-50 ${className} ${activeCls}`}
    >
      {label}
    </button>
  );
}
