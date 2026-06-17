// ============================================================================
// IMS 2.0 - Promotions admin (F11 advanced promotions + F12 cross-category bundles)
// ============================================================================
// Create / list / deactivate promo rules. The live POS apply is DARK behind the
// PROMO_ENGINE_ENABLED env flag; this page authors + previews rules so they are
// ready before the owner flips it on per-store. Restrained light UI.
//
// Backend: routers/promotions.py (/api/v1/promotions). Service imported DIRECTLY
// (the barrel re-export is unreliable for new services -- see CLAUDE memory).

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, RefreshCw, X, Power, Tag } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  promotionsApi,
  type PromoRule,
  type PromoRuleCreate,
  type PromoType,
} from '../../services/api/promotions';

const PROMO_TYPES: { id: PromoType; label: string; hint: string }[] = [
  { id: 'THRESHOLD', label: 'Threshold spend', hint: 'Spend >= X, get % off' },
  { id: 'PERCENT', label: 'Flat % off', hint: '% off eligible lines' },
  { id: 'BOGO', label: 'Buy N get M', hint: 'Buy N units, get M at % off' },
  { id: 'COMBO', label: 'Cross-category bundle', hint: '2+ categories present -> % off' },
  { id: 'SECOND_PAIR', label: '2nd pair %', hint: 'Cheaper pair gets % off (N10)' },
];

const DISCOUNT_CATEGORIES = ['MASS', 'PREMIUM', 'LUXURY', 'SERVICE', 'NON_DISCOUNTABLE'];

const emptyForm: PromoRuleCreate = {
  name: '',
  promo_type: 'THRESHOLD',
  description: '',
  reward_value: 10,
  stackable: false,
  priority: 0,
  min_cart_value: null,
  min_qty: null,
  trigger_categories: null,
  buy_quantity: null,
  get_quantity: null,
  combo_groups: null,
  max_discount_amount: null,
  active: true,
};

function typeChip(t: string): string {
  switch (t) {
    case 'THRESHOLD':
      return 'bg-blue-100 text-blue-700';
    case 'BOGO':
      return 'bg-purple-100 text-purple-700';
    case 'COMBO':
      return 'bg-amber-100 text-amber-700';
    case 'SECOND_PAIR':
      return 'bg-teal-100 text-teal-700';
    default:
      return 'bg-gray-100 text-gray-700';
  }
}

export default function PromotionsPage() {
  const { user } = useAuth();
  const toast = useToast();
  const storeId = user?.activeStoreId || undefined;

  const [rules, setRules] = useState<PromoRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<PromoRuleCreate>(emptyForm);

  const canWrite = useMemo(() => {
    const roles = user?.roles || [];
    return roles.some((r) =>
      ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER'].includes(r),
    );
  }, [user]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await promotionsApi.listRules({ store_id: storeId });
      setRules(res.rules || []);
    } catch {
      toast.error('Could not load promo rules');
    } finally {
      setLoading(false);
    }
  }, [storeId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const update = <K extends keyof PromoRuleCreate>(key: K, val: PromoRuleCreate[K]) =>
    setForm((f) => ({ ...f, [key]: val }));

  const submit = async () => {
    if (!form.name || form.name.trim().length < 2) {
      toast.error('Name is required');
      return;
    }
    setSaving(true);
    try {
      const payload: PromoRuleCreate = {
        ...form,
        store_ids: storeId ? [storeId] : null,
        trigger_categories:
          form.trigger_categories && form.trigger_categories.length
            ? form.trigger_categories
            : null,
      };
      await promotionsApi.createRule(payload);
      toast.success('Promo rule created');
      setShowCreate(false);
      setForm(emptyForm);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Could not create rule');
    } finally {
      setSaving(false);
    }
  };

  const deactivate = async (rule: PromoRule) => {
    if (!window.confirm(`Deactivate "${rule.name}"?`)) return;
    try {
      await promotionsApi.deactivateRule(rule.promo_id);
      toast.success('Promo rule deactivated');
      load();
    } catch {
      toast.error('Could not deactivate rule');
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900 flex items-center gap-2">
            <Tag className="w-6 h-6 text-bv-red-600" />
            Promotions
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Advanced offers + cross-category bundles. Rules are evaluated at POS only
            when the engine is enabled (off by default); author + preview them here.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
          {canWrite && (
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm text-white bg-bv-red-600 rounded-lg hover:bg-bv-red-700"
            >
              <Plus className="w-4 h-4" /> New rule
            </button>
          )}
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
            <tr>
              <th className="text-left font-medium px-4 py-3">Name</th>
              <th className="text-left font-medium px-4 py-3">Type</th>
              <th className="text-right font-medium px-4 py-3">Reward %</th>
              <th className="text-left font-medium px-4 py-3">Stacking</th>
              <th className="text-right font-medium px-4 py-3">Used</th>
              <th className="text-left font-medium px-4 py-3">Status</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-gray-400">
                  Loading...
                </td>
              </tr>
            ) : rules.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-gray-400">
                  No promo rules yet.
                </td>
              </tr>
            ) : (
              rules.map((r) => (
                <tr key={r.promo_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">{r.name}</div>
                    {r.description && (
                      <div className="text-xs text-gray-500">{r.description}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={clsx(
                        'inline-block px-2 py-0.5 rounded text-xs font-medium',
                        typeChip(r.promo_type),
                      )}
                    >
                      {r.promo_type}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right text-gray-900">
                    {r.reward_value}%
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {r.stackable ? 'Stackable' : 'Exclusive'}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-600">
                    {r.uses_count ?? 0}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={clsx(
                        'inline-block px-2 py-0.5 rounded text-xs font-medium',
                        r.active
                          ? 'bg-green-100 text-green-700'
                          : 'bg-gray-100 text-gray-500',
                      )}
                    >
                      {r.active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {canWrite && r.active && (
                      <button
                        onClick={() => deactivate(r)}
                        title="Deactivate"
                        className="inline-flex items-center gap-1 px-2 py-1 text-xs text-gray-600 border border-gray-200 rounded hover:bg-gray-50"
                      >
                        <Power className="w-3.5 h-3.5" /> Off
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <div className="fixed inset-0 bg-black/30 flex items-start justify-center z-50 p-4 overflow-y-auto">
          <div className="bg-white rounded-xl border border-gray-200 w-full max-w-lg my-8">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 sticky top-0 bg-white">
              <h2 className="text-lg font-semibold text-gray-900">New promo rule</h2>
              <button
                onClick={() => setShowCreate(false)}
                className="text-gray-400 hover:text-gray-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="px-5 py-4 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Name
                </label>
                <input
                  value={form.name}
                  onChange={(e) => update('name', e.target.value)}
                  placeholder="e.g. Watch + Sunglass 10% bundle"
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Type
                </label>
                <select
                  value={form.promo_type}
                  onChange={(e) => update('promo_type', e.target.value as PromoType)}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
                >
                  {PROMO_TYPES.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label} — {t.hint}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Reward % off
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={form.reward_value ?? 0}
                    onChange={(e) => update('reward_value', Number(e.target.value))}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Max discount (Rs)
                  </label>
                  <input
                    type="number"
                    min={0}
                    value={form.max_discount_amount ?? ''}
                    onChange={(e) =>
                      update(
                        'max_discount_amount',
                        e.target.value ? Number(e.target.value) : null,
                      )
                    }
                    placeholder="optional ceiling"
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
                  />
                </div>
              </div>

              {form.promo_type === 'THRESHOLD' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Minimum cart value (Rs)
                  </label>
                  <input
                    type="number"
                    min={0}
                    value={form.min_cart_value ?? ''}
                    onChange={(e) =>
                      update('min_cart_value', e.target.value ? Number(e.target.value) : null)
                    }
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
                  />
                </div>
              )}

              {form.promo_type === 'BOGO' && (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Buy quantity
                    </label>
                    <input
                      type="number"
                      min={1}
                      value={form.buy_quantity ?? ''}
                      onChange={(e) =>
                        update('buy_quantity', e.target.value ? Number(e.target.value) : null)
                      }
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Get quantity
                    </label>
                    <input
                      type="number"
                      min={1}
                      value={form.get_quantity ?? ''}
                      onChange={(e) =>
                        update('get_quantity', e.target.value ? Number(e.target.value) : null)
                      }
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
                    />
                  </div>
                </div>
              )}

              {form.promo_type === 'COMBO' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Bundle categories (2+, all must be in the cart)
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {DISCOUNT_CATEGORIES.map((c) => {
                      const groups = form.combo_groups || [];
                      const selected = groups.some((g) => g.category === c);
                      return (
                        <button
                          key={c}
                          type="button"
                          onClick={() =>
                            update(
                              'combo_groups',
                              selected
                                ? groups.filter((g) => g.category !== c)
                                : [...groups, { category: c }],
                            )
                          }
                          className={clsx(
                            'px-2.5 py-1 rounded text-xs border',
                            selected
                              ? 'bg-bv-red-50 border-bv-red-200 text-bv-red-700'
                              : 'border-gray-200 text-gray-600 hover:bg-gray-50',
                          )}
                        >
                          {c}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {(form.promo_type === 'PERCENT' || form.promo_type === 'SECOND_PAIR') && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Eligible categories (optional; blank = all)
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {DISCOUNT_CATEGORIES.map((c) => {
                      const cats = form.trigger_categories || [];
                      const selected = cats.includes(c);
                      return (
                        <button
                          key={c}
                          type="button"
                          onClick={() =>
                            update(
                              'trigger_categories',
                              selected ? cats.filter((x) => x !== c) : [...cats, c],
                            )
                          }
                          className={clsx(
                            'px-2.5 py-1 rounded text-xs border',
                            selected
                              ? 'bg-bv-red-50 border-bv-red-200 text-bv-red-700'
                              : 'border-gray-200 text-gray-600 hover:bg-gray-50',
                          )}
                        >
                          {c}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={!!form.stackable}
                  onChange={(e) => update('stackable', e.target.checked)}
                />
                Stackable (combines with other promos). Default is exclusive — only the
                single best promo fires.
              </label>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Description (optional)
                </label>
                <textarea
                  value={form.description ?? ''}
                  onChange={(e) => update('description', e.target.value)}
                  rows={2}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
                />
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-gray-100">
              <button
                onClick={() => setShowCreate(false)}
                className="px-3 py-2 text-sm text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={submit}
                disabled={saving}
                className="px-4 py-2 text-sm text-white bg-bv-red-600 rounded-lg hover:bg-bv-red-700 disabled:opacity-50"
              >
                {saving ? 'Saving...' : 'Create rule'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
