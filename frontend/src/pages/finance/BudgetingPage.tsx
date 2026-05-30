// ============================================================================
// IMS 2.0 - Dual-mode Budgeting (planned vs actual)
// ============================================================================
// Per store, per period (YYYY-MM), per head. REVENUE = income target; every
// other head = an expense category. Plans are user-entered + persisted; the
// actuals (order revenue + APPROVED expenses) are derived server-side and shown
// in the variance view with amount + % and over/under colour coding.

import { useState, useEffect, useCallback, useMemo } from 'react';
import { Loader2, Plus, Trash2, Save, RefreshCw } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { storeApi } from '../../services/api/stores';
import { budgetsApi } from '../../services/api/budgets';
import type { BudgetLine, BudgetVariance } from '../../services/api/budgets';
import { formatCurrency } from './financeUtils';

const REVENUE_HEAD = 'REVENUE';

// Seed expense heads offered by default so a fresh store has a starting table.
const DEFAULT_EXPENSE_HEADS = [
  'rent',
  'salaries',
  'utilities',
  'marketing',
  'inventory',
  'maintenance',
  'travel',
  'miscellaneous',
];

function currentPeriod(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

type StoreOpt = { id: string; name: string };

export default function BudgetingPage() {
  const { user } = useAuth();
  const toast = useToast();

  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [storeId, setStoreId] = useState<string>(user?.activeStoreId || '');
  const [period, setPeriod] = useState<string>(currentPeriod());

  // Editable planned amounts keyed by head -> string (so the input is controlled).
  const [planned, setPlanned] = useState<Record<string, string>>({});
  // budget_id per head (for delete), from the last list fetch.
  const [budgetIds, setBudgetIds] = useState<Record<string, string | null>>({});
  const [newHead, setNewHead] = useState('');

  const [variance, setVariance] = useState<BudgetVariance | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // ---- store list ------------------------------------------------------
  useEffect(() => {
    storeApi
      .getStores()
      .then((res: any) => {
        const list = res?.stores || res || [];
        const mapped: StoreOpt[] = (Array.isArray(list) ? list : []).map((s: any) => ({
          id: String(s.store_id || s.id || s._id || ''),
          name: String(s.store_name || s.storeName || s.name || s.store_id || s.id || ''),
        })).filter((s: StoreOpt) => s.id);
        setStores(mapped);
        if (!storeId && mapped.length > 0) setStoreId(mapped[0].id);
      })
      .catch(() => setStores([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- load plans + variance for (store, period) -----------------------
  const load = useCallback(async () => {
    const sid = storeId || user?.activeStoreId;
    if (!sid) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [listRes, varRes] = await Promise.all([
        budgetsApi.list({ store_id: sid, period }),
        budgetsApi.variance({ store_id: sid, period }),
      ]);

      const plannedMap: Record<string, string> = {};
      const idMap: Record<string, string | null> = {};
      // Seed the default rows (so they show even with no saved plan yet).
      [REVENUE_HEAD, ...DEFAULT_EXPENSE_HEADS].forEach((h) => {
        plannedMap[h] = '';
      });
      (listRes.budgets || []).forEach((b: BudgetLine) => {
        plannedMap[b.head] = String(b.planned_amount ?? '');
        idMap[b.head] = b.budget_id;
      });
      // Any head that has an actual (from variance) but no plan row -> include it.
      (varRes.lines || []).forEach((ln) => {
        if (!(ln.head in plannedMap)) plannedMap[ln.head] = '';
      });

      setPlanned(plannedMap);
      setBudgetIds(idMap);
      setVariance(varRes);
    } catch {
      toast.error('Failed to load budgets');
      setVariance(null);
    } finally {
      setLoading(false);
    }
  }, [storeId, period, user?.activeStoreId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  // ---- ordered list of heads for the editable table --------------------
  const heads = useMemo(() => {
    const all = Object.keys(planned);
    return all.sort((a, b) => {
      if (a === REVENUE_HEAD) return -1;
      if (b === REVENUE_HEAD) return 1;
      return a.localeCompare(b);
    });
  }, [planned]);

  const addHead = () => {
    const h = newHead.trim();
    if (!h) return;
    if (h.toUpperCase() === REVENUE_HEAD) {
      toast.warning('REVENUE already exists');
      return;
    }
    if (h in planned) {
      toast.warning(`Head "${h}" already exists`);
      return;
    }
    setPlanned((p) => ({ ...p, [h]: '' }));
    setNewHead('');
  };

  // ---- save all non-empty planned amounts ------------------------------
  const saveAll = async () => {
    const sid = storeId || user?.activeStoreId;
    if (!sid) {
      toast.error('Select a store first');
      return;
    }
    const toSave = heads.filter((h) => planned[h] !== '' && planned[h] != null);
    if (toSave.length === 0) {
      toast.info('Nothing to save');
      return;
    }
    setSaving(true);
    try {
      for (const head of toSave) {
        const amount = Number(planned[head]);
        if (Number.isNaN(amount) || amount < 0) continue;
        await budgetsApi.upsert({ store_id: sid, period, head, planned_amount: amount });
      }
      toast.success('Budget saved');
      await load();
    } catch {
      toast.error('Failed to save budget');
    } finally {
      setSaving(false);
    }
  };

  const removeHead = async (head: string) => {
    const bid = budgetIds[head];
    if (bid) {
      try {
        await budgetsApi.remove(bid);
        toast.success(`Removed ${head}`);
      } catch {
        toast.error('Failed to remove line');
        return;
      }
    }
    setPlanned((p) => {
      const next = { ...p };
      delete next[head];
      return next;
    });
    if (bid) await load();
  };

  const totals = variance?.totals;

  return (
    <div className="p-4 tablet:p-6 space-y-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-col tablet:flex-row tablet:items-end tablet:justify-between gap-3">
        <div>
          <h1 className="text-xl tablet:text-2xl font-bold text-gray-900">Budgeting</h1>
          <p className="text-sm text-gray-500">
            Planned vs actual by head, per store, per month.
          </p>
        </div>
        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3 tablet:w-auto w-full">
          {stores.length > 1 && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Store</label>
              <select
                className="input-field w-full"
                value={storeId}
                onChange={(e) => setStoreId(e.target.value)}
              >
                {stores.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Period</label>
            <input
              type="month"
              className="input-field w-full"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
            />
          </div>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20 text-gray-400">
          <Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading…
        </div>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
            <SummaryCard
              label="Revenue (planned → actual)"
              planned={totals?.revenue_planned ?? 0}
              actual={totals?.revenue_actual ?? 0}
              pct={totals?.revenue_variance_pct ?? null}
              kind="revenue"
            />
            <SummaryCard
              label="Expenses (planned → actual)"
              planned={totals?.expense_planned ?? 0}
              actual={totals?.expense_actual ?? 0}
              pct={totals?.expense_variance_pct ?? null}
              kind="expense"
            />
            <SummaryCard
              label="Net (planned → actual)"
              planned={totals?.net_planned ?? 0}
              actual={totals?.net_actual ?? 0}
              pct={null}
              kind="net"
            />
          </div>

          {/* Editable planned table */}
          <div className="card">
            <div className="flex flex-col tablet:flex-row tablet:items-center tablet:justify-between gap-3 mb-4">
              <h2 className="text-lg font-semibold text-gray-900">Planned amounts</h2>
              <div className="flex items-center gap-2">
                <input
                  className="input-field w-40"
                  placeholder="New head…"
                  value={newHead}
                  onChange={(e) => setNewHead(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && addHead()}
                />
                <button className="btn-secondary flex items-center gap-1" onClick={addHead} type="button">
                  <Plus className="w-4 h-4" /> Add
                </button>
                <button
                  className="btn-primary flex items-center gap-1"
                  onClick={saveAll}
                  disabled={saving}
                  type="button"
                >
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  Save
                </button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-gray-200">
                    <th className="px-3 py-2">Head</th>
                    <th className="px-3 py-2 text-right">Planned (INR)</th>
                    <th className="px-3 py-2 w-10"></th>
                  </tr>
                </thead>
                <tbody>
                  {heads.map((head) => (
                    <tr key={head} className="border-b border-gray-100">
                      <td className="px-3 py-2 font-medium text-gray-900">
                        {head === REVENUE_HEAD ? (
                          <span className="inline-flex items-center gap-2">
                            REVENUE
                            <span className="badge-success text-[10px]">income target</span>
                          </span>
                        ) : (
                          head
                        )}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <input
                          type="number"
                          min={0}
                          step="any"
                          className="input-field w-40 text-right"
                          value={planned[head] ?? ''}
                          onChange={(e) =>
                            setPlanned((p) => ({ ...p, [head]: e.target.value }))
                          }
                        />
                      </td>
                      <td className="px-3 py-2 text-right">
                        {head !== REVENUE_HEAD && (
                          <button
                            type="button"
                            className="text-gray-400 hover:text-red-600"
                            onClick={() => removeHead(head)}
                            title="Remove head"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Variance view */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-gray-900">Planned vs Actual</h2>
              <button
                type="button"
                className="btn-secondary flex items-center gap-1"
                onClick={() => load()}
              >
                <RefreshCw className="w-4 h-4" /> Refresh
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-gray-200">
                    <th className="px-3 py-2">Head</th>
                    <th className="px-3 py-2 text-right">Planned</th>
                    <th className="px-3 py-2 text-right">Actual</th>
                    <th className="px-3 py-2 text-right">Variance</th>
                    <th className="px-3 py-2 text-right">Variance %</th>
                  </tr>
                </thead>
                <tbody>
                  {(variance?.lines || []).map((ln) => {
                    // Colour rule:
                    //  - revenue under target  -> red (actual < planned)
                    //  - revenue over target   -> green
                    //  - expense over budget   -> red (actual > planned)
                    //  - expense under budget  -> green
                    const favourable = ln.is_revenue
                      ? ln.actual >= ln.planned
                      : ln.actual <= ln.planned;
                    const colour = favourable ? 'text-green-600' : 'text-red-600';
                    return (
                      <tr key={ln.head} className="border-b border-gray-100">
                        <td className="px-3 py-2 font-medium text-gray-900">
                          {ln.is_revenue ? 'REVENUE' : ln.head}
                          {ln.planned === 0 && ln.actual > 0 && (
                            <span className="ml-2 badge-warning text-[10px]">unplanned</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-700">
                          {formatCurrency(ln.planned)}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-900 font-medium">
                          {formatCurrency(ln.actual)}
                        </td>
                        <td className={clsx('px-3 py-2 text-right font-medium', colour)}>
                          {ln.variance > 0 ? '+' : ''}
                          {formatCurrency(ln.variance)}
                        </td>
                        <td className={clsx('px-3 py-2 text-right', colour)}>
                          {ln.variance_pct == null ? (
                            <span className="text-gray-400">—</span>
                          ) : (
                            <>
                              {ln.variance_pct > 0 ? '+' : ''}
                              {ln.variance_pct.toFixed(1)}%
                            </>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {(variance?.lines || []).length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-3 py-6 text-center text-gray-400">
                        No plans or actuals for this period yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Summary card: planned -> actual with a favourable/unfavourable colour.
// ----------------------------------------------------------------------------
function SummaryCard({
  label,
  planned,
  actual,
  pct,
  kind,
}: {
  label: string;
  planned: number;
  actual: number;
  pct: number | null;
  kind: 'revenue' | 'expense' | 'net';
}) {
  // Revenue/net: higher actual is good. Expense: lower actual is good.
  const favourable =
    kind === 'expense' ? actual <= planned : actual >= planned;
  const colour = favourable ? 'text-green-600' : 'text-red-600';
  return (
    <div className="card">
      <p className="text-sm text-gray-500">{label}</p>
      <p className="text-2xl font-bold text-gray-900 mt-1">{formatCurrency(actual)}</p>
      <p className="text-xs text-gray-500 mt-0.5">Planned {formatCurrency(planned)}</p>
      {pct != null && (
        <p className={clsx('text-xs font-medium mt-1', colour)}>
          {pct > 0 ? '+' : ''}
          {pct.toFixed(1)}% vs plan
        </p>
      )}
    </div>
  );
}
