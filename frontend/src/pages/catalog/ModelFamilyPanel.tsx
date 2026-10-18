// ============================================================================
// IMS 2.0 - Model family panel (G1, owner 2026-10-18)
// ============================================================================
// The product drawer's "Colours & sizes of this model": every item of the
// product's model (GET /products/{id}/family) and catalogue review's two
// fixes for mistakes -- Link to model (a product that IS another colour/size
// of an existing model) and Unlink (it is a different model). The catalogue
// roles only, as the server gates them.

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { productApi, type ProductFamily } from '../../services/api/products';

const fmt = (n: number | null | undefined) =>
  typeof n === 'number' ? `₹${n.toLocaleString('en-IN')}` : '—';

export function ModelFamilyPanel({ productId, canEdit }: { productId: string; canEdit: boolean }) {
  const toast = useToast();
  const [family, setFamily] = useState<ProductFamily | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setFamily(await productApi.getFamily(productId));
    } catch {
      setFamily(null); // fail-soft: the drawer simply has no model section
    }
  }, [productId]);

  useEffect(() => {
    setFamily(null);
    void load();
  }, [load]);

  if (!family || !family.family_category) return null;
  const members = family.members;
  const inModel = members.length > 1;
  const self = members.find((m) => m.product_id === productId);

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await fn();
      toast.success(done);
      await load();
    } catch (e: unknown) {
      toast.error(e instanceof Error && e.message ? e.message : 'That did not work. Try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="model-family-panel">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 mt-4 mb-1">
        {inModel ? `Colours & sizes of this model (${members.length})` : 'Model'}
      </h3>
      {inModel ? (
        <ul className="divide-y divide-gray-50 text-xs">
          {members.map((m) => (
            <li
              key={m.product_id}
              className={`flex items-center gap-2 py-1.5 ${m.product_id === productId ? 'font-semibold' : ''} ${
                m.is_active && !m.archived ? '' : 'opacity-60'
              }`}
            >
              <span className="min-w-0 flex-1 text-gray-800">
                {[m.color, m.size].filter(Boolean).join(' · ') || '—'}
                {m.is_lead && (
                  <span className="ml-1.5 rounded-full bg-bv-50 px-1.5 py-0.5 text-[10px] font-medium text-bv">
                    model
                  </span>
                )}
                {m.archived ? ' (archived)' : !m.is_active ? ' (off)' : ''}
              </span>
              {canEdit ? (
                <Link to={`/catalog/add?edit=${encodeURIComponent(m.product_id)}`} className="font-mono text-gray-600 hover:underline">
                  {m.sku}
                </Link>
              ) : (
                <span className="font-mono text-gray-600">{m.sku}</span>
              )}
              <span className="w-20 text-right tabular-nums text-gray-900">{fmt(m.offer_price ?? m.mrp)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-gray-600">
          {family.opted_out
            ? 'Marked as a model of its own.'
            : family.candidate_lead
              ? `Same brand and model number as ${family.candidate_lead.name || family.candidate_lead.sku}.`
              : 'No other colour or size of this model yet.'}
        </p>
      )}
      {canEdit && (
        <div className="mt-2 flex flex-wrap gap-2">
          {!inModel && family.candidate_lead && (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                act(() => productApi.linkToModel(productId), 'Linked: it is now a colour/size of that model.')
              }
              className="btn-secondary !py-1 !px-2.5 text-xs flex items-center gap-1.5"
            >
              {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              Link to model
            </button>
          )}
          {inModel && self && !self.is_lead && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (!window.confirm('Take this item out of the model? It becomes a model of its own.')) return;
                void act(() => productApi.unlinkFromModel(productId), 'Unlinked: it is a model of its own now.');
              }}
              className="btn-secondary !py-1 !px-2.5 text-xs flex items-center gap-1.5"
            >
              {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              Unlink (different model)
            </button>
          )}
        </div>
      )}
    </div>
  );
}
