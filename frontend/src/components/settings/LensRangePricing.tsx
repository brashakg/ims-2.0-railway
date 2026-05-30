// ============================================================================
// IMS 2.0 — Lens range pricing (May 2026)
// ============================================================================
// Bracket pricing for the lens-pricing engine. Operator sets one row per
// (brand × index × category × parameter × min..max) tier; the resolver
// picks the matching range at quote time.
//
// Backend endpoints:
//   GET    /admin/lens/pricing-ranges
//   POST   /admin/lens/pricing-ranges
//   PUT    /admin/lens/pricing-ranges/{id}
//   DELETE /admin/lens/pricing-ranges/{id}        (soft — flips is_active)
//   POST   /admin/lens/pricing-ranges/quote       (used by POS, not here)

import { useEffect, useMemo, useState } from 'react';
import { Loader2, Plus, Trash2, Save, RefreshCw } from 'lucide-react';
import { adminLensApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

type Parameter = 'sphere' | 'cylinder' | 'addition';
type Category = 'SINGLE_VISION' | 'BIFOCAL' | 'PROGRESSIVE' | 'OFFICE';

const PARAMETERS: Parameter[] = ['sphere', 'cylinder', 'addition'];
const CATEGORIES: Category[] = ['SINGLE_VISION', 'BIFOCAL', 'PROGRESSIVE', 'OFFICE'];

interface LensBrand {
  id?: string;
  brand_id?: string;
  name?: string;
}

interface LensIndex {
  id?: string;
  index_id?: string;
  value?: string;
  name?: string;
}

interface RangeRow {
  range_id: string;
  brand_id: string;
  index_id: string;
  category: string;
  parameter: 'sphere' | 'cylinder' | 'addition';
  min_value: number;
  max_value: number;
  base_price: number;
  is_active: boolean;
}

export function LensRangePricingSection() {
  const { hasRole } = useAuth();
  const toast = useToast();
  const canEdit = hasRole(['SUPERADMIN', 'ADMIN']);

  const [brands, setBrands] = useState<LensBrand[]>([]);
  const [indices, setIndices] = useState<LensIndex[]>([]);
  const [ranges, setRanges] = useState<RangeRow[]>([]);

  const [loading, setLoading] = useState(true);
  const [filterBrand, setFilterBrand] = useState('');
  const [filterIndex, setFilterIndex] = useState('');
  const [filterCategory, setFilterCategory] = useState('');

  // Inline new-row state
  const [newRow, setNewRow] = useState({
    brand_id: '',
    index_id: '',
    category: 'SINGLE_VISION' as Category,
    parameter: 'sphere' as Parameter,
    min_value: 0,
    max_value: 0,
    base_price: 0,
  });
  const [creating, setCreating] = useState(false);

  const brandLabel = (b: LensBrand) => b.name || b.brand_id || b.id || 'Unknown';
  const brandId = (b: LensBrand) => b.brand_id || b.id || '';
  const indexLabel = (i: LensIndex) =>
    i.value ? `${i.value}${i.name ? ` (${i.name})` : ''}` : i.name || i.index_id || i.id || 'Unknown';
  const indexId = (i: LensIndex) => i.index_id || i.id || '';

  // ---- Data load -------------------------------------------------
  const load = async () => {
    setLoading(true);
    try {
      const [brandsR, indicesR, rangesR] = await Promise.all([
        adminLensApi.getLensBrands().catch(() => ({ brands: [] })),
        adminLensApi.getLensIndices().catch(() => ({ indices: [] })),
        adminLensApi.listLensPricingRanges({
          brand_id: filterBrand || undefined,
          index_id: filterIndex || undefined,
          category: filterCategory || undefined,
        }).catch(() => ({ ranges: [], total: 0 })),
      ]);
      setBrands((brandsR as { brands?: LensBrand[] })?.brands || (brandsR as LensBrand[]) || []);
      setIndices((indicesR as { indices?: LensIndex[] })?.indices || (indicesR as LensIndex[]) || []);
      setRanges((rangesR as { ranges?: RangeRow[] })?.ranges || []);
    } catch {
      toast.error('Could not load range pricing');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterBrand, filterIndex, filterCategory]);

  // ---- Row groups ------------------------------------------------
  const grouped = useMemo(() => {
    const out: Record<string, RangeRow[]> = {};
    for (const r of ranges) {
      const key = `${r.brand_id}|${r.index_id}|${r.category}|${r.parameter}`;
      (out[key] ||= []).push(r);
    }
    for (const k of Object.keys(out)) {
      out[k].sort((a, b) => Math.abs(a.min_value) - Math.abs(b.min_value));
    }
    return out;
  }, [ranges]);

  const groupKeys = useMemo(() => Object.keys(grouped).sort(), [grouped]);

  // ---- Create new row -------------------------------------------
  const handleCreate = async () => {
    if (!newRow.brand_id || !newRow.index_id) {
      toast.error('Pick a brand and index first');
      return;
    }
    if (Math.abs(newRow.min_value) > Math.abs(newRow.max_value)) {
      toast.error('|min| must be ≤ |max|');
      return;
    }
    if (newRow.base_price < 0) {
      toast.error('Base price cannot be negative');
      return;
    }
    setCreating(true);
    try {
      await adminLensApi.createLensPricingRange(newRow);
      toast.success('Range added');
      setNewRow((r) => ({ ...r, min_value: 0, max_value: 0, base_price: 0 }));
      load();
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Could not save range';
      toast.error(msg);
    } finally {
      setCreating(false);
    }
  };

  const handleSaveRow = async (r: RangeRow) => {
    try {
      await adminLensApi.updateLensPricingRange(r.range_id, {
        min_value: r.min_value,
        max_value: r.max_value,
        base_price: r.base_price,
      });
      toast.success('Range updated');
      load();
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Update failed';
      toast.error(msg);
    }
  };

  const handleDeactivate = async (r: RangeRow) => {
    if (!confirm(`Deactivate range ${r.parameter} ${r.min_value}..${r.max_value} (${r.base_price})?`)) return;
    try {
      await adminLensApi.deleteLensPricingRange(r.range_id);
      toast.success('Range deactivated');
      load();
    } catch {
      toast.error('Could not deactivate range');
    }
  };

  const updateRangeLocal = (rangeId: string, patch: Partial<RangeRow>) => {
    setRanges((rs) => rs.map((r) => (r.range_id === rangeId ? { ...r, ...patch } : r)));
  };

  // ---- Render ----------------------------------------------------
  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Lens range pricing</h2>
            <p className="text-sm text-gray-500 mt-1">
              Tier brackets like "SPH 0–2.00 = ₹1,200" replace per-SKU pricing.
              The POS resolver picks <em>exact match first</em>, then range, then falls back
              to a hint if nothing covers the Rx.
            </p>
            {!canEdit && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mt-2 inline-block">
                Read-only — ADMIN required
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={load}
            className="px-3 py-1.5 text-sm text-gray-600 border border-gray-300 rounded flex items-center gap-1.5 hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2 mb-4">
          <select
            value={filterBrand}
            onChange={(e) => setFilterBrand(e.target.value)}
            className="px-2 py-1.5 text-sm border border-gray-300 rounded"
          >
            <option value="">All brands</option>
            {brands.map((b) => (
              <option key={brandId(b)} value={brandId(b)}>
                {brandLabel(b)}
              </option>
            ))}
          </select>
          <select
            value={filterIndex}
            onChange={(e) => setFilterIndex(e.target.value)}
            className="px-2 py-1.5 text-sm border border-gray-300 rounded"
          >
            <option value="">All indices</option>
            {indices.map((i) => (
              <option key={indexId(i)} value={indexId(i)}>
                {indexLabel(i)}
              </option>
            ))}
          </select>
          <select
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            className="px-2 py-1.5 text-sm border border-gray-300 rounded"
          >
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
          </div>
        ) : groupKeys.length === 0 ? (
          <div className="text-center py-10 text-sm text-gray-500 border border-dashed border-gray-300 rounded-lg">
            No active ranges yet. Add one below.
          </div>
        ) : (
          <div className="space-y-4">
            {groupKeys.map((k) => {
              const rows = grouped[k];
              const sample = rows[0];
              const brandName =
                brands.find((b) => brandId(b) === sample.brand_id)?.name || sample.brand_id;
              const indexName =
                indices.find((i) => indexId(i) === sample.index_id)?.value || sample.index_id;
              return (
                <div key={k} className="border border-gray-200 rounded-lg overflow-hidden">
                  <div className="bg-gray-50 px-3 py-2 text-xs font-medium text-gray-700 flex flex-wrap gap-x-3">
                    <span>
                      <span className="text-gray-500">Brand:</span> {brandName}
                    </span>
                    <span>
                      <span className="text-gray-500">Index:</span> {indexName}
                    </span>
                    <span>
                      <span className="text-gray-500">Category:</span> {sample.category}
                    </span>
                    <span>
                      <span className="text-gray-500">Parameter:</span>{' '}
                      <span className="font-mono uppercase">{sample.parameter}</span>
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-white">
                      <tr className="text-xs text-gray-500">
                        <th className="text-left px-3 py-1.5 font-medium">Min (signed)</th>
                        <th className="text-left px-3 py-1.5 font-medium">Max (signed)</th>
                        <th className="text-left px-3 py-1.5 font-medium">Base price (₹)</th>
                        <th className="text-right px-3 py-1.5 font-medium">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.range_id} className="border-t border-gray-100">
                          <td className="px-3 py-1.5">
                            <input
                              type="number"
                              step={0.25}
                              value={r.min_value}
                              disabled={!canEdit}
                              onChange={(e) =>
                                updateRangeLocal(r.range_id, { min_value: parseFloat(e.target.value) || 0 })
                              }
                              className="w-24 px-2 py-1 border border-gray-300 rounded text-sm disabled:bg-gray-50"
                            />
                          </td>
                          <td className="px-3 py-1.5">
                            <input
                              type="number"
                              step={0.25}
                              value={r.max_value}
                              disabled={!canEdit}
                              onChange={(e) =>
                                updateRangeLocal(r.range_id, { max_value: parseFloat(e.target.value) || 0 })
                              }
                              className="w-24 px-2 py-1 border border-gray-300 rounded text-sm disabled:bg-gray-50"
                            />
                          </td>
                          <td className="px-3 py-1.5">
                            <input
                              type="number"
                              step={1}
                              value={r.base_price}
                              disabled={!canEdit}
                              onChange={(e) =>
                                updateRangeLocal(r.range_id, { base_price: parseFloat(e.target.value) || 0 })
                              }
                              className="w-32 px-2 py-1 border border-gray-300 rounded text-sm disabled:bg-gray-50"
                            />
                          </td>
                          <td className="px-3 py-1.5 text-right">
                            <button
                              type="button"
                              onClick={() => handleSaveRow(r)}
                              disabled={!canEdit}
                              className="px-2 py-1 text-xs bg-bv-red-600 hover:bg-bv-red-700 text-white rounded font-semibold mr-1.5 disabled:opacity-50"
                            >
                              <Save className="w-3 h-3 inline mr-1" />
                              Save
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDeactivate(r)}
                              disabled={!canEdit}
                              className="p-1 text-gray-400 hover:text-red-600 disabled:opacity-50"
                              title="Deactivate"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Add row */}
        {canEdit && (
          <div className="mt-6 border-t border-gray-200 pt-4">
            <p className="text-sm font-medium text-gray-700 mb-2">Add new range</p>
            <div className="grid grid-cols-1 md:grid-cols-7 gap-2">
              <select
                value={newRow.brand_id}
                onChange={(e) => setNewRow({ ...newRow, brand_id: e.target.value })}
                className="px-2 py-1.5 text-sm border border-gray-300 rounded md:col-span-1"
              >
                <option value="">Brand</option>
                {brands.map((b) => (
                  <option key={brandId(b)} value={brandId(b)}>
                    {brandLabel(b)}
                  </option>
                ))}
              </select>
              <select
                value={newRow.index_id}
                onChange={(e) => setNewRow({ ...newRow, index_id: e.target.value })}
                className="px-2 py-1.5 text-sm border border-gray-300 rounded"
              >
                <option value="">Index</option>
                {indices.map((i) => (
                  <option key={indexId(i)} value={indexId(i)}>
                    {indexLabel(i)}
                  </option>
                ))}
              </select>
              <select
                value={newRow.category}
                onChange={(e) => setNewRow({ ...newRow, category: e.target.value as Category })}
                className="px-2 py-1.5 text-sm border border-gray-300 rounded"
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <select
                value={newRow.parameter}
                onChange={(e) => setNewRow({ ...newRow, parameter: e.target.value as Parameter })}
                className="px-2 py-1.5 text-sm border border-gray-300 rounded"
              >
                {PARAMETERS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <input
                type="number"
                step={0.25}
                placeholder="Min"
                value={newRow.min_value}
                onChange={(e) => setNewRow({ ...newRow, min_value: parseFloat(e.target.value) || 0 })}
                className="px-2 py-1.5 text-sm border border-gray-300 rounded"
              />
              <input
                type="number"
                step={0.25}
                placeholder="Max"
                value={newRow.max_value}
                onChange={(e) => setNewRow({ ...newRow, max_value: parseFloat(e.target.value) || 0 })}
                className="px-2 py-1.5 text-sm border border-gray-300 rounded"
              />
              <input
                type="number"
                step={1}
                placeholder="Price ₹"
                value={newRow.base_price}
                onChange={(e) => setNewRow({ ...newRow, base_price: parseFloat(e.target.value) || 0 })}
                className="px-2 py-1.5 text-sm border border-gray-300 rounded"
              />
            </div>
            <button
              type="button"
              onClick={handleCreate}
              disabled={creating}
              className="mt-2 px-4 py-2 bg-bv-red-600 hover:bg-bv-red-700 text-white text-sm font-semibold rounded flex items-center gap-2 disabled:opacity-50"
            >
              {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              Add range
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default LensRangePricingSection;
