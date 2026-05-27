// ============================================================================
// IMS 2.0 - Lens & Contact-lens Power Grid (on-hand availability heatmaps)
// ============================================================================
// Spectacle lenses: SPH (rows) x CYL (cols). Contact lenses: power (rows) x
// base-curve (cols), with a near-expiry flag. Counts = on-hand serialized stock.

import { useCallback, useEffect, useState } from 'react';
import { Grid3x3, Loader2, RefreshCw, AlertTriangle } from 'lucide-react';
import { powerGridApi, type LensGrid, type ClGrid } from '../../services/api/powerGrid';
import { storeApi } from '../../services/api/stores';
import { useToast } from '../../context/ToastContext';

interface StoreOpt { store_id: string; store_name?: string; store_code?: string; }

function heatClass(count: number): string {
  if (count <= 0) return 'bg-gray-50 text-gray-300';
  if (count <= 2) return 'bg-green-100 text-green-900';
  if (count <= 5) return 'bg-green-200 text-green-900';
  if (count <= 10) return 'bg-green-300 text-green-900';
  return 'bg-green-500 text-white';
}

export default function PowerGridPage() {
  const toast = useToast();
  const [tab, setTab] = useState<'lens' | 'cl'>('lens');
  const [storeId, setStoreId] = useState('');
  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [lens, setLens] = useState<LensGrid | null>(null);
  const [cl, setCl] = useState<ClGrid | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    storeApi.getStores().then((r) => setStores(r?.stores || [])).catch(() => setStores([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [l, c] = await Promise.all([
        powerGridApi.lens(storeId || undefined),
        powerGridApi.contactLens(storeId || undefined),
      ]);
      setLens(l); setCl(c);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load power grid');
    } finally { setLoading(false); }
  }, [storeId, toast]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="p-6 max-w-full">
      <div className="flex items-center justify-between mb-3">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Grid3x3 className="w-5 h-5" /> Power Grid
        </h1>
        <div className="flex items-center gap-2">
          <select value={storeId} onChange={(e) => setStoreId(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm">
            <option value="">All stores</option>
            {stores.map((s) => <option key={s.store_id} value={s.store_id}>{s.store_name || s.store_code || s.store_id}</option>)}
          </select>
          <button type="button" onClick={load} className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5">
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      </div>

      <div className="flex gap-1 border-b border-gray-200 mb-4">
        {(['lens', 'cl'] as const).map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${tab === t ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t === 'lens' ? 'Spectacle lenses (SPH x CYL)' : 'Contact lenses (Power x BC)'}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading...</div>
      ) : tab === 'lens' ? (
        <LensGridView grid={lens} />
      ) : (
        <ClGridView grid={cl} />
      )}
    </div>
  );
}

function LensGridView({ grid }: { grid: LensGrid | null }) {
  if (!grid) return null;
  const skus = grid.lens_skus || 0;
  const totalUnits = grid.total_units || 0;
  if (skus === 0) {
    return <Empty msg="No spectacle-lens SKUs with power (SPH/CYL) set yet. Add lens products with SPH/CYL to populate the grid." />;
  }
  if (totalUnits === 0) {
    return (
      <Empty
        msg={`${skus} spectacle-lens SKU${skus === 1 ? '' : 's'} in the catalog, but 0 units on hand for the current scope. Add stock via Inventory or pick another store.`}
      />
    );
  }
  return (
    <>
      <p className="text-xs text-gray-500 mb-2">
        {grid.total_units} units on hand &middot; {grid.lens_skus} lens SKUs
        {grid.out_of_range_units ? ` · ${grid.out_of_range_units} units outside the displayed range` : ''}
      </p>
      <div className="overflow-auto border border-gray-200 rounded-lg">
        <table className="text-xs border-collapse">
          <thead className="sticky top-0 bg-white">
            <tr>
              <th className="sticky left-0 bg-gray-100 px-2 py-1 text-gray-500 font-medium border-b border-r border-gray-200">SPH \ CYL</th>
              {grid.cyl_range.map((c) => (
                <th key={c} className="bg-gray-100 px-2 py-1 text-gray-600 font-medium border-b border-gray-200 whitespace-nowrap">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.sph_range.map((s) => (
              <tr key={s}>
                <td className="sticky left-0 bg-gray-50 px-2 py-1 text-gray-600 font-medium border-r border-gray-200 whitespace-nowrap">{s}</td>
                {grid.cyl_range.map((c) => {
                  const cell = grid.grid[s]?.[c];
                  const count = cell?.count || 0;
                  return (
                    <td key={c} title={`SPH ${s} / CYL ${c}: ${count} unit(s), ${cell?.skus || 0} SKU(s)`}
                      className={`px-2 py-1 text-center border border-gray-100 ${heatClass(count)}`}>
                      {count || ''}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ClGridView({ grid }: { grid: ClGrid | null }) {
  if (!grid) return null;
  const skus = grid.cl_skus || 0;
  const totalUnits = grid.total_units || 0;
  const hasPowers = (grid.power_range || []).length > 0;
  if (!hasPowers) {
    return <Empty msg="No contact-lens SKUs with power set yet. Add CL products with power/base-curve to populate the grid." />;
  }
  if (totalUnits === 0) {
    return (
      <Empty
        msg={`${skus} contact-lens SKU${skus === 1 ? '' : 's'} with power set, but 0 units on hand for the current scope. Add stock via Inventory or pick another store.`}
      />
    );
  }
  return (
    <>
      <p className="text-xs text-gray-500 mb-2">
        {grid.total_units} units on hand &middot; {grid.cl_skus} CL SKUs &middot;
        <span className="inline-flex items-center gap-1 ml-1"><span className="inline-block w-3 h-3 rounded ring-2 ring-amber-400" /> near-expiry (&le;{grid.near_expiry_days}d)</span>
      </p>
      <div className="overflow-auto border border-gray-200 rounded-lg">
        <table className="text-xs border-collapse">
          <thead className="sticky top-0 bg-white">
            <tr>
              <th className="sticky left-0 bg-gray-100 px-2 py-1 text-gray-500 font-medium border-b border-r border-gray-200">Power \ BC</th>
              {grid.curve_range.map((c) => (
                <th key={c} className="bg-gray-100 px-2 py-1 text-gray-600 font-medium border-b border-gray-200 whitespace-nowrap">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.power_range.map((p) => (
              <tr key={p}>
                <td className="sticky left-0 bg-gray-50 px-2 py-1 text-gray-600 font-medium border-r border-gray-200 whitespace-nowrap">{p}</td>
                {grid.curve_range.map((c) => {
                  const cell = grid.grid[p]?.[c];
                  const count = cell?.count || 0;
                  return (
                    <td key={c} title={`Power ${p} / BC ${c}: ${count} unit(s)`}
                      className={`px-2 py-1 text-center border border-gray-100 ${heatClass(count)} ${cell?.near_expiry ? 'ring-2 ring-amber-400 ring-inset' : ''}`}>
                      {count || ''}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Empty({ msg }: { msg: string }) {
  return (
    <div className="text-sm text-gray-500 border border-dashed border-gray-300 rounded-lg p-8 text-center flex flex-col items-center gap-2">
      <AlertTriangle className="w-5 h-5 text-gray-400" />
      {msg}
    </div>
  );
}
