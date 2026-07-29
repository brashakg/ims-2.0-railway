// ============================================================================
// IMS 2.0 - Online Store : Discount Rules  (rebuild of BVI DiscountRule; DARK)
// ============================================================================
// Owner-editable admin for the automatic ONLINE storefront discount rules. A rule
// sets what the WEBSITE shows -- e.g. "Ray-Ban frames -> 20% off". It is
// ONLINE-only: it never changes in-store POS pricing or the in-store discount
// caps. The winning rule for a product is the MOST SPECIFIC active match
// (category + brand + sub-brand  >  category + brand  >  category), tie-broken by
// priority. A product with an explicit manual online offer overrides its rule.
//
// PUSH-DARK: editing a rule recomputes the catalog's stored online prices, but
// those reach Shopify only behind the go-live push gates. Role-gated (in App.tsx
// + backend) to SUPERADMIN / ADMIN / CATALOG_MANAGER.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Tag, Plus, Pencil, Trash2, Loader2, RefreshCw, X, Info } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import {
  onlineDiscountRulesApi,
  type DiscountRule,
  type RuleCreatePayload,
  type RecomputeResult,
} from '../../services/api/onlineDiscountRules';

// The IMS canonical product categories a rule may target (mirrors
// product_master._CATEGORY_SPECS; the backend validates + 400s an unknown one).
const CATEGORY_OPTIONS: { value: string; label: string }[] = [
  { value: 'FRAME', label: 'Frames' },
  { value: 'SUNGLASS', label: 'Sunglasses' },
  { value: 'OPTICAL_LENS', label: 'Optical Lenses' },
  { value: 'READING_GLASSES', label: 'Reading Glasses' },
  { value: 'CONTACT_LENS', label: 'Contact Lenses' },
  { value: 'COLORED_CONTACT_LENS', label: 'Colour Contact Lenses' },
  { value: 'WATCH', label: 'Watches' },
  { value: 'SMARTWATCH', label: 'Smart Watches' },
  { value: 'SMARTGLASSES', label: 'Smart Glasses' },
  { value: 'WALL_CLOCK', label: 'Wall Clocks' },
  { value: 'ACCESSORIES', label: 'Accessories' },
  { value: 'SERVICES', label: 'Services' },
  { value: 'HEARING_AID', label: 'Hearing Aids' },
];

const CATEGORY_LABEL: Record<string, string> = Object.fromEntries(
  CATEGORY_OPTIONS.map((c) => [c.value, c.label]),
);

interface FormState {
  rule_id?: string;
  category: string;
  brand: string;
  sub_brand: string;
  discount_percentage: string;
  priority: string;
  active: boolean;
}

const EMPTY_FORM: FormState = {
  category: 'FRAME',
  brand: '',
  sub_brand: '',
  discount_percentage: '',
  priority: '0',
  active: true,
};

/** Human note for the fail-soft recompute summary. ALWAYS says something —
 *  including the N=0 case (OS-050: a silent zero hid an alias-scoped sweep that
 *  matched nothing while the footnote claimed every product was repriced). */
function recomputeNote(r?: RecomputeResult): string {
  if (!r) return '';
  // OS-068: ok:false means the bulk recompute ERRORED after the rule saved —
  // stored online prices may now be inconsistent with the rules. "Deferred"
  // implied it would happen by itself; nothing retries automatically.
  if (r.ok === false) {
    return ' — but the price recompute FAILED, so stored online prices may be stale. Use "Recompute prices" to retry.';
  }
  const p = r.products ?? 0;
  const v = r.variants ?? 0;
  if (!p && !v) return ' — 0 products matched this scope; nothing was repriced';
  const c = r.changed;
  const queued =
    c !== undefined
      ? c > 0
        ? `; ${c} repriced & queued for the next Shopify push`
        : '; prices unchanged (nothing queued)'
      : '';
  return ` (recomputed ${p} product${p === 1 ? '' : 's'}, ${v} variant${v === 1 ? '' : 's'}${queued})`;
}

/** True when the recompute demonstrably touched nothing — surfaced as a warning
 *  toast, never a plain success. */
function recomputeTouchedNothing(r?: RecomputeResult): boolean {
  return !!r && r.ok !== false && !(r.products ?? 0) && !(r.variants ?? 0);
}

export default function DiscountRulesPage() {
  const toast = useToast();
  const [rules, setRules] = useState<DiscountRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await onlineDiscountRulesApi.list();
      setRules(res.rules);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || 'Could not load rules');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setModalOpen(true);
  };

  const openEdit = (r: DiscountRule) => {
    setForm({
      rule_id: r.rule_id,
      category: r.category,
      brand: r.brand || '',
      sub_brand: r.sub_brand || '',
      discount_percentage: String(r.discount_percentage ?? ''),
      priority: String(r.priority ?? 0),
      active: r.active !== false,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const pct = Number(form.discount_percentage);
    if (Number.isNaN(pct) || pct < 0 || pct > 100) {
      toast.error('Discount % must be between 0 and 100');
      return;
    }
    const payload: RuleCreatePayload = {
      category: form.category,
      brand: form.brand.trim() || null,
      sub_brand: form.sub_brand.trim() || null,
      discount_percentage: pct,
      active: form.active,
      priority: Number(form.priority) || 0,
    };
    setSaving(true);
    try {
      if (form.rule_id) {
        const res = await onlineDiscountRulesApi.update(form.rule_id, payload);
        const msg = 'Rule updated' + recomputeNote(res.recompute);
        if (recomputeTouchedNothing(res.recompute)) toast.warning(msg);
        else toast.success(msg);
      } else {
        const res = await onlineDiscountRulesApi.create(payload);
        const msg = 'Rule created' + recomputeNote(res.recompute);
        if (recomputeTouchedNothing(res.recompute)) toast.warning(msg);
        else toast.success(msg);
      }
      setModalOpen(false);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || 'Could not save rule');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (r: DiscountRule) => {
    const label = [CATEGORY_LABEL[r.category] || r.category, r.brand, r.sub_brand]
      .filter(Boolean)
      .join(' / ');
    if (!window.confirm(`Delete the ${label} rule? The affected products revert to MRP or any broader rule.`)) {
      return;
    }
    try {
      const res = await onlineDiscountRulesApi.remove(r.rule_id);
      const msg = 'Rule deleted' + recomputeNote(res.recompute);
      if (recomputeTouchedNothing(res.recompute)) toast.warning(msg);
      else toast.success(msg);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || 'Could not delete rule');
    }
  };

  // OS-050: the manual full-catalog recompute (the API existed; no control did).
  // Re-applies every rule to the stored online prices — useful after a category
  // clean-up or when a scoped sweep is suspected to have missed alias-cased docs.
  const [recomputing, setRecomputing] = useState(false);
  const recomputeAll = async () => {
    if (recomputing) return;
    setRecomputing(true);
    try {
      const res = await onlineDiscountRulesApi.recompute();
      const msg = 'Recompute finished' + recomputeNote(res.recompute);
      if (recomputeTouchedNothing(res.recompute)) toast.warning(msg);
      else toast.success(msg);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || 'Recompute failed');
    } finally {
      setRecomputing(false);
    }
  };

  const sorted = useMemo(
    () =>
      [...rules].sort((a, b) => {
        const spec = (r: DiscountRule) => (r.sub_brand ? 2 : r.brand ? 1 : 0);
        return (
          spec(b) - spec(a) ||
          (b.priority ?? 0) - (a.priority ?? 0) ||
          (CATEGORY_LABEL[a.category] || a.category).localeCompare(
            CATEGORY_LABEL[b.category] || b.category,
          )
        );
      }),
    [rules],
  );

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Tag className="w-5 h-5" /> Online Discount Rules
        </h1>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={recomputeAll}
            disabled={recomputing}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-700 border border-gray-300 bg-white hover:bg-gray-50 rounded-lg px-3 py-1.5 disabled:opacity-60"
            title="Re-apply every active rule to the stored online prices across the whole catalog — changed products are queued for the next Shopify push (also use after a save reports a failed recompute)"
          >
            {recomputing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Recompute prices
          </button>
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv-red-600 hover:bg-bv-red-700 rounded-lg px-3 py-1.5"
          >
            <Plus className="w-4 h-4" /> Add rule
          </button>
        </div>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        These rules set the <span className="font-medium text-gray-700">website</span> price
        automatically. The most specific active rule wins &mdash; e.g.{' '}
        <span className="text-gray-700">Ray-Ban Aviator</span> beats{' '}
        <span className="text-gray-700">Ray-Ban</span> beats{' '}
        <span className="text-gray-700">Sunglasses</span>. In-store POS pricing is not affected.
      </p>

      <div className="mb-4 flex items-start gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
        <Info className="w-4 h-4 mt-0.5 shrink-0" />
        <span>
          Example: a <strong>Frames &rarr; 20% off</strong> rule shows every frame online at 20%
          below MRP (never below cost). A product with a hand-set online offer keeps that price.
          Saving reprices matching products and <strong>queues the changed ones as pending</strong> —
          they reach the website when an admin runs the push on the Shopify sync page (and only
          writes live once the owner has armed the push gates).
        </span>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500 p-6">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading rules&hellip;
          </div>
        ) : sorted.length === 0 ? (
          <div className="p-8 text-center text-sm text-gray-500">
            No discount rules yet. Add one to start pricing the storefront automatically.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b border-gray-200 bg-gray-50">
                  <th className="px-4 py-2.5 font-medium">Category</th>
                  <th className="px-4 py-2.5 font-medium">Brand</th>
                  <th className="px-4 py-2.5 font-medium">Sub-brand</th>
                  <th className="px-4 py-2.5 font-medium text-right">Discount</th>
                  <th className="px-4 py-2.5 font-medium text-right">Priority</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
                  <tr key={r.rule_id} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                    <td className="px-4 py-2.5 text-gray-900">
                      {CATEGORY_LABEL[r.category] || r.category}
                    </td>
                    <td className="px-4 py-2.5 capitalize text-gray-700">{r.brand || <span className="text-gray-300">any</span>}</td>
                    <td className="px-4 py-2.5 capitalize text-gray-700">{r.sub_brand || <span className="text-gray-300">any</span>}</td>
                    <td className="px-4 py-2.5 text-right font-semibold text-gray-900">
                      {r.discount_percentage}%
                    </td>
                    <td className="px-4 py-2.5 text-right text-gray-500">{r.priority ?? 0}</td>
                    <td className="px-4 py-2.5">
                      <span
                        className={
                          'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium border ' +
                          (r.active !== false
                            ? 'bg-green-100 text-green-800 border-green-200'
                            : 'bg-gray-100 text-gray-500 border-gray-200')
                        }
                      >
                        {r.active !== false ? 'Active' : 'Off'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => openEdit(r)}
                          className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                          title="Edit rule"
                        >
                          <Pencil className="w-4 h-4" />
                        </button>
                        <button
                          type="button"
                          onClick={() => remove(r)}
                          className="p-1.5 rounded-lg text-gray-500 hover:bg-red-50 hover:text-bv-red-600"
                          title="Delete rule"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create / edit modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white shadow-lg">
            <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
              <h2 className="text-sm font-semibold text-gray-900">
                {form.rule_id ? 'Edit rule' : 'Add discount rule'}
              </h2>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="p-1 rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-600"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="px-5 py-4 space-y-3">
              <label className="block">
                <span className="text-xs font-medium text-gray-600">Category</span>
                <select
                  value={form.category}
                  onChange={(e) => setForm({ ...form, category: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-bv-red-400 focus:ring-1 focus:ring-bv-red-200 focus:outline-none"
                >
                  {CATEGORY_OPTIONS.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs font-medium text-gray-600">Brand (optional)</span>
                  <input
                    value={form.brand}
                    onChange={(e) => setForm({ ...form, brand: e.target.value })}
                    placeholder="any brand"
                    className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-bv-red-400 focus:ring-1 focus:ring-bv-red-200 focus:outline-none"
                  />
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-gray-600">Sub-brand (optional)</span>
                  <input
                    value={form.sub_brand}
                    onChange={(e) => setForm({ ...form, sub_brand: e.target.value })}
                    placeholder="any sub-brand"
                    className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-bv-red-400 focus:ring-1 focus:ring-bv-red-200 focus:outline-none"
                  />
                </label>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs font-medium text-gray-600">Discount % off MRP</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    step="0.5"
                    value={form.discount_percentage}
                    onChange={(e) => setForm({ ...form, discount_percentage: e.target.value })}
                    placeholder="e.g. 20"
                    className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-bv-red-400 focus:ring-1 focus:ring-bv-red-200 focus:outline-none"
                  />
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-gray-600">Priority</span>
                  <input
                    type="number"
                    value={form.priority}
                    onChange={(e) => setForm({ ...form, priority: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-bv-red-400 focus:ring-1 focus:ring-bv-red-200 focus:outline-none"
                  />
                </label>
              </div>
              <label className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  checked={form.active}
                  onChange={(e) => setForm({ ...form, active: e.target.checked })}
                  className="rounded border-gray-300 text-bv-red-600 focus:ring-bv-red-200"
                />
                <span className="text-sm text-gray-700">Active</span>
              </label>
              <p className="text-[11px] text-gray-400 flex items-center gap-1">
                <RefreshCw className="w-3 h-3" /> Saving recomputes online prices for every product
                in this category (alias-safe match, never below cost) and queues changed products
                for the next Shopify push.
              </p>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-5 py-3">
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={saving}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv-red-600 hover:bg-bv-red-700 rounded-lg px-4 py-1.5 disabled:opacity-60"
              >
                {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                {form.rule_id ? 'Save changes' : 'Create rule'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
