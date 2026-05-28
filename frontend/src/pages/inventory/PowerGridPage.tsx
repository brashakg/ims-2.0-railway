// ============================================================================
// IMS 2.0 - Power Grid v2 (Branch B' sub-PR 2)
// ============================================================================
// Consumes the Branch B' sub-PR 1 typed-lens-catalog endpoints
// (/lens-catalog, /lens-stock, /lens-enums) instead of the old
// products-based grid.
//
// Flow:
//   1. Owner picks filters (brand, index, material, coating, lens_type).
//   2. We fetch matching lens_lines from /lens-catalog.
//   3. If exactly one line matches -> render its SPH x CYL[, ADD] matrix.
//   4. If multiple lines match -> render a list; click drills into a matrix.
//   5. Click a populated cell -> side drawer with stock detail + history.
//
// BV brand tokens only (bv / bv-600 / bv-50 / bv-soft). No bv-100/200/700.
// No mock data. Empty/loading/error states for all three layers.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Grid3x3,
  Loader2,
  RefreshCw,
  AlertTriangle,
  X,
  Filter as FilterIcon,
  Layers,
  ChevronRight,
  History,
} from 'lucide-react';
import { lensCatalogApi, type LensLine, type LensCatalogMetaOptions } from '../../services/api/lensCatalog';
import {
  lensStockApi,
  type LensStockCell,
  type LensStockAuditRow,
} from '../../services/api/lensStock';
import { storeApi } from '../../services/api/stores';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

// ----------------------------------------------------------------------------
// Local types
// ----------------------------------------------------------------------------

interface StoreOpt {
  store_id: string;
  store_name?: string;
  store_code?: string;
}

interface FilterState {
  brand: string;
  index: string;        // kept as string in the form; coerced to number when sent
  material: string;
  coating: string;
  lens_type: string;
  q: string;
}

const EMPTY_FILTERS: FilterState = {
  brand: '',
  index: '',
  material: '',
  coating: '',
  lens_type: '',
  q: '',
};

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

/** Heat class for a power cell. on_hand drives the saturation. Available =
 *  on_hand - reserved; we colour by on_hand to keep the heatmap stable while
 *  reservations move. */
function cellHeat(onHand: number): string {
  if (onHand <= 0) return 'bg-gray-50 text-gray-300';
  if (onHand <= 2) return 'bg-bv-soft text-bv-600';
  if (onHand <= 5) return 'bg-bv-50 text-bv-600';
  if (onHand <= 10) return 'bg-bv-50 text-bv';
  return 'bg-bv text-white';
}

/** Format a power value for grid headers / cell tooltips. +/- sign + 2-decimal. */
function fmtPower(n: number): string {
  const abs = Math.abs(n).toFixed(2);
  if (n > 0) return `+${abs}`;
  if (n < 0) return `-${abs}`;
  return abs;
}

/** Format a (sph, cyl) tuple as a single string. The matrix view filters
 *  by ADD upstream so the cellMap only ever needs (sph, cyl) -- adding ADD
 *  to the key would just bloat the lookup. */
function sphCylKey(sph: number, cyl: number): string {
  return `${sph.toFixed(2)}|${cyl.toFixed(2)}`;
}

/** Sort unique numbers ascending. Tolerant of floats. */
function uniqAsc(values: number[]): number[] {
  const seen = new Map<string, number>();
  values.forEach((v) => {
    const k = v.toFixed(4);
    if (!seen.has(k)) seen.set(k, v);
  });
  return Array.from(seen.values()).sort((a, b) => a - b);
}

// ----------------------------------------------------------------------------
// Top-level page
// ----------------------------------------------------------------------------

export default function PowerGridPage() {
  const { user } = useAuth();
  const toast = useToast();

  // Stores + active scope. Default to the user's active store; superadmin can
  // pick "All stores" (empty string). validate_store_access enforces this on
  // the backend so we just pass through.
  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [storeId, setStoreId] = useState<string>(user?.activeStoreId || '');

  // Enum config -> filter dropdowns. From /lens-catalog/meta/options.
  const [meta, setMeta] = useState<LensCatalogMetaOptions | null>(null);
  const [metaLoading, setMetaLoading] = useState(true);
  const [metaError, setMetaError] = useState<string | null>(null);

  // Active filter form. Changes here -> trigger /lens-catalog re-fetch.
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);

  // Lens lines matching the filter. Drives the line picker / direct matrix.
  const [lines, setLines] = useState<LensLine[]>([]);
  const [linesLoading, setLinesLoading] = useState(false);
  const [linesError, setLinesError] = useState<string | null>(null);

  // The line whose matrix is currently open (null when on the line picker).
  const [activeLineId, setActiveLineId] = useState<string | null>(null);

  // Cell drawer -- opens when the user clicks a cell in the matrix.
  const [drawerCell, setDrawerCell] = useState<LensStockCell | null>(null);

  // ---- effects ----

  // Stores list (one-shot).
  useEffect(() => {
    storeApi.getStores()
      .then((r) => setStores(r?.stores || []))
      .catch(() => setStores([]));
  }, []);

  // Enum config (one-shot). The filter strip needs this before it can render.
  useEffect(() => {
    let alive = true;
    setMetaLoading(true);
    setMetaError(null);
    lensCatalogApi.metaOptions()
      .then((r) => { if (alive) setMeta(r); })
      .catch((e) => {
        if (alive) setMetaError(e instanceof Error ? e.message : 'Failed to load filter options');
      })
      .finally(() => { if (alive) setMetaLoading(false); });
    return () => { alive = false; };
  }, []);

  // Lines list (re-fetches on filter change).
  const loadLines = useCallback(async () => {
    setLinesLoading(true);
    setLinesError(null);
    try {
      const indexNum = filters.index ? Number(filters.index) : undefined;
      const r = await lensCatalogApi.list({
        brand: filters.brand || undefined,
        index: indexNum !== undefined && !Number.isNaN(indexNum) ? indexNum : undefined,
        material: filters.material || undefined,
        coating: filters.coating || undefined,
        lens_type: filters.lens_type || undefined,
        q: filters.q || undefined,
        active: true,
        limit: 200,
      });
      setLines(r.lens_lines || []);
    } catch (e) {
      setLinesError(e instanceof Error ? e.message : 'Failed to load lens lines');
      setLines([]);
    } finally {
      setLinesLoading(false);
    }
  }, [filters]);

  useEffect(() => { loadLines(); }, [loadLines]);

  // When the filter result narrows to exactly one line, auto-open its matrix.
  // When it widens past one line, drop back to the picker so the user can
  // see all matches.
  useEffect(() => {
    if (linesLoading) return;
    if (lines.length === 1) {
      setActiveLineId(lines[0].lens_line_id);
    } else if (lines.length !== 1 && activeLineId) {
      // Active line still in the list? Keep it. Otherwise drop.
      if (!lines.find((l) => l.lens_line_id === activeLineId)) {
        setActiveLineId(null);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines, linesLoading]);

  const activeLine = useMemo(
    () => lines.find((l) => l.lens_line_id === activeLineId) || null,
    [lines, activeLineId],
  );

  // ---- handlers ----

  const onFilterChange = (k: keyof FilterState, v: string) => {
    setFilters((p) => ({ ...p, [k]: v }));
  };

  const onClearFilters = () => setFilters(EMPTY_FILTERS);

  const onRefresh = () => {
    loadLines();
    // If a matrix is open, refresh it too.
    if (activeLineId) {
      // re-fetch matrix by toggling a key on the matrix component (see below)
      setMatrixRefreshKey((k) => k + 1);
    }
  };

  // Bump this number to force the matrix component to re-fetch.
  const [matrixRefreshKey, setMatrixRefreshKey] = useState(0);

  // ---- render ----

  return (
    <div className="p-6 max-w-full">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h1 className="text-xl font-semibold text-ink flex items-center gap-2">
            <Grid3x3 className="w-5 h-5 text-bv" /> Power Grid
            <span className="text-xs font-normal text-ink-4">v2 - lens catalog</span>
          </h1>
          <p className="text-xs text-ink-4 mt-0.5">
            Filter by brand / index / coating / material / lens type to drill into a SPH x CYL matrix.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={storeId}
            onChange={(e) => setStoreId(e.target.value)}
            className="border border-line rounded-lg px-3 py-1.5 text-sm bg-white"
            aria-label="Store"
          >
            <option value="">All stores</option>
            {stores.map((s) => (
              <option key={s.store_id} value={s.store_id}>
                {s.store_name || s.store_code || s.store_id}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={onRefresh}
            className="inline-flex items-center gap-1.5 text-sm text-ink-3 hover:bg-bg-sunk rounded-lg px-3 py-1.5"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      </div>

      {/* ---- Filter strip ---- */}
      <FilterStrip
        meta={meta}
        metaLoading={metaLoading}
        metaError={metaError}
        filters={filters}
        onChange={onFilterChange}
        onClear={onClearFilters}
      />

      {/* ---- Body ---- */}
      {linesError ? (
        <ErrorBox
          msg={linesError}
          onRetry={loadLines}
        />
      ) : linesLoading ? (
        <LoadingBox />
      ) : lines.length === 0 ? (
        <EmptyBox
          msg={
            anyFiltersSet(filters)
              ? 'No lens lines match these filters. Try widening the filters or seed the lens catalog from Settings.'
              : 'No lens lines in the catalog yet. Seed lens lines from Settings -> Lens Catalog, or paste a stock matrix to a new line.'
          }
          onClear={anyFiltersSet(filters) ? onClearFilters : undefined}
        />
      ) : activeLine ? (
        <MatrixView
          line={activeLine}
          storeId={storeId}
          onBack={lines.length > 1 ? () => setActiveLineId(null) : undefined}
          onCellClick={(cell) => setDrawerCell(cell)}
          refreshKey={matrixRefreshKey}
          onErrorToast={(m) => toast.error(m)}
        />
      ) : (
        <LineListView
          lines={lines}
          onPick={(id) => setActiveLineId(id)}
        />
      )}

      {/* ---- Cell drawer (placeholder for B'3 edits) ---- */}
      {drawerCell && activeLine ? (
        <CellDrawer
          cell={drawerCell}
          line={activeLine}
          onClose={() => setDrawerCell(null)}
        />
      ) : null}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Filter strip
// ----------------------------------------------------------------------------

interface FilterStripProps {
  meta: LensCatalogMetaOptions | null;
  metaLoading: boolean;
  metaError: string | null;
  filters: FilterState;
  onChange: (k: keyof FilterState, v: string) => void;
  onClear: () => void;
}

function FilterStrip({ meta, metaLoading, metaError, filters, onChange, onClear }: FilterStripProps) {
  // Normalise the brand list. enums.brands may be empty on a fresh deploy;
  // surface that with a hint instead of an empty dropdown.
  const brands = useMemo<string[]>(() => {
    return (meta?.enums?.brands || []) as string[];
  }, [meta]);
  const indexes = useMemo<number[]>(() => {
    return (meta?.enums?.indexes || []) as number[];
  }, [meta]);
  const materials = useMemo<string[]>(() => {
    return (meta?.enums?.materials || []) as string[];
  }, [meta]);
  const coatings = useMemo<string[]>(() => {
    return (meta?.enums?.coatings || []) as string[];
  }, [meta]);
  const lensTypes = useMemo<string[]>(() => {
    return (meta?.enums?.lens_types || []) as string[];
  }, [meta]);

  const hasAnyFilter = anyFiltersSet(filters);

  if (metaLoading) {
    return (
      <div className="border border-line rounded-lg bg-surface p-3 mb-4 flex items-center gap-2 text-sm text-ink-4">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading filter options...
      </div>
    );
  }
  if (metaError) {
    return (
      <div className="border border-err-50 rounded-lg bg-err-50 p-3 mb-4 flex items-start gap-2 text-sm text-err">
        <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
        <span>Couldn't load filter options ({metaError}). Refresh, or seed lens enums from Settings -&gt; Lens Catalog.</span>
      </div>
    );
  }

  return (
    <div className="border border-line rounded-lg bg-surface p-3 mb-4">
      <div className="flex items-center gap-2 mb-2">
        <FilterIcon className="w-4 h-4 text-ink-4" />
        <span className="text-xs font-semibold text-ink-3 uppercase tracking-wide">Filters</span>
        {hasAnyFilter ? (
          <button
            type="button"
            onClick={onClear}
            className="ml-auto text-xs text-bv hover:text-bv-600 underline-offset-2 hover:underline"
          >
            Clear all
          </button>
        ) : null}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-2">
        <FilterSelect
          label="Brand"
          value={filters.brand}
          onChange={(v) => onChange('brand', v)}
          options={brands.map((b) => ({ value: b, label: b }))}
          emptyHint="No brands yet"
        />
        <FilterSelect
          label="Index"
          value={filters.index}
          onChange={(v) => onChange('index', v)}
          options={indexes.map((i) => ({ value: String(i), label: i.toFixed(2) }))}
          emptyHint="No indexes seeded"
        />
        <FilterSelect
          label="Material"
          value={filters.material}
          onChange={(v) => onChange('material', v)}
          options={materials.map((m) => ({ value: m, label: m }))}
          emptyHint="No materials seeded"
        />
        <FilterSelect
          label="Coating"
          value={filters.coating}
          onChange={(v) => onChange('coating', v)}
          options={coatings.map((c) => ({ value: c, label: c }))}
          emptyHint="No coatings seeded"
        />
        <FilterSelect
          label="Lens type"
          value={filters.lens_type}
          onChange={(v) => onChange('lens_type', v)}
          options={lensTypes.map((l) => ({ value: l, label: l }))}
          emptyHint="No lens types seeded"
        />
        <div className="flex flex-col">
          <label className="text-[11px] font-medium text-ink-4 mb-1">Search</label>
          <input
            type="text"
            value={filters.q}
            onChange={(e) => onChange('q', e.target.value)}
            placeholder="Brand or series..."
            className="border border-line rounded px-2 py-1.5 text-sm bg-white focus:outline-none focus:border-bv focus:ring-1 focus:ring-bv-50"
          />
        </div>
      </div>

      {/* Active filter pills */}
      {hasAnyFilter ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {Object.entries(filters).map(([k, v]) => {
            if (!v) return null;
            return (
              <span
                key={k}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-bv-soft text-bv text-xs font-medium"
              >
                {humanFilterLabel(k as keyof FilterState)}: {v}
                <button
                  type="button"
                  onClick={() => onChange(k as keyof FilterState, '')}
                  className="hover:text-bv-600"
                  aria-label={`Clear ${k}`}
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

interface FilterSelectProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Array<{ value: string; label: string }>;
  emptyHint: string;
}

function FilterSelect({ label, value, onChange, options, emptyHint }: FilterSelectProps) {
  const disabled = options.length === 0;
  return (
    <div className="flex flex-col">
      <label className="text-[11px] font-medium text-ink-4 mb-1">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="border border-line rounded px-2 py-1.5 text-sm bg-white focus:outline-none focus:border-bv focus:ring-1 focus:ring-bv-50 disabled:bg-bg-sunk disabled:text-ink-5"
      >
        <option value="">{disabled ? emptyHint : `All ${label.toLowerCase()}s`}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

function humanFilterLabel(k: keyof FilterState): string {
  switch (k) {
    case 'brand': return 'Brand';
    case 'index': return 'Index';
    case 'material': return 'Material';
    case 'coating': return 'Coating';
    case 'lens_type': return 'Lens type';
    case 'q': return 'Search';
    default: return k;
  }
}

function anyFiltersSet(filters: FilterState): boolean {
  return Object.values(filters).some((v) => v.trim().length > 0);
}

// ----------------------------------------------------------------------------
// Line list (when multiple lines match the filter)
// ----------------------------------------------------------------------------

interface LineListViewProps {
  lines: LensLine[];
  onPick: (id: string) => void;
}

function LineListView({ lines, onPick }: LineListViewProps) {
  return (
    <div className="border border-line rounded-lg bg-surface overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-xs font-semibold text-ink-3 uppercase tracking-wide">
        <Layers className="w-4 h-4 text-bv" />
        {lines.length} lens line{lines.length === 1 ? '' : 's'} match
      </div>
      <div className="divide-y divide-line-soft">
        {lines.map((l) => (
          <button
            key={l.lens_line_id}
            type="button"
            onClick={() => onPick(l.lens_line_id)}
            className="w-full flex items-center justify-between px-3 py-2.5 text-left hover:bg-bv-50 transition"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-sm font-medium text-ink">
                <span className="truncate">{l.brand}</span>
                <span className="text-ink-4">/</span>
                <span className="truncate text-ink-2">{l.series}</span>
              </div>
              <div className="flex items-center gap-2 text-xs text-ink-4 mt-0.5">
                <span className="font-mono">{l.index.toFixed(2)}</span>
                <span>{l.material}</span>
                <span>{l.lens_type}</span>
                <span className="px-1.5 py-0.5 rounded bg-bv-soft text-bv">{l.coating}</span>
                {l.has_add ? <span className="px-1.5 py-0.5 rounded bg-bg-sunk text-ink-3">+ADD</span> : null}
              </div>
            </div>
            <ChevronRight className="w-4 h-4 text-ink-5 flex-shrink-0" />
          </button>
        ))}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Matrix view (SPH x CYL[, ADD] grid for one lens line)
// ----------------------------------------------------------------------------

interface MatrixViewProps {
  line: LensLine;
  storeId: string;
  onBack?: () => void;
  onCellClick: (cell: LensStockCell) => void;
  refreshKey: number;
  onErrorToast: (msg: string) => void;
}

function MatrixView({ line, storeId, onBack, onCellClick, refreshKey, onErrorToast }: MatrixViewProps) {
  const [cells, setCells] = useState<LensStockCell[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // For bifocal/progressive lines, the matrix is split per ADD value.
  // null = no add (SV lines); we render one matrix.
  const [activeAdd, setActiveAdd] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await lensStockApi.matrix(line.lens_line_id, storeId || undefined);
      setCells(r.cells || []);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load matrix';
      setError(msg);
      onErrorToast(msg);
      setCells([]);
    } finally {
      setLoading(false);
    }
  }, [line.lens_line_id, storeId, onErrorToast]);

  useEffect(() => { load(); }, [load, refreshKey]);

  // Available ADD values within the stock (drives the ADD tab strip).
  const addValues = useMemo<Array<number | null>>(() => {
    if (!line.has_add) return [null];
    const set = new Set<number>();
    cells.forEach((c) => {
      if (c.add !== null && c.add !== undefined) set.add(c.add);
    });
    const sorted = Array.from(set).sort((a, b) => a - b);
    return sorted.length > 0 ? sorted : [null];
  }, [line.has_add, cells]);

  // Pick a default ADD when the tabs appear.
  useEffect(() => {
    if (addValues.length === 0) return;
    if (activeAdd === null && addValues[0] !== null) setActiveAdd(addValues[0]);
  }, [addValues, activeAdd]);

  // Filter cells to the active ADD (if any).
  const cellsForActiveAdd = useMemo(() => {
    if (activeAdd === null) return cells.filter((c) => c.add === null || c.add === undefined);
    return cells.filter((c) => c.add === activeAdd);
  }, [cells, activeAdd]);

  // Build SPH x CYL grid from the filtered cells.
  const sphList = useMemo(
    () => uniqAsc(cellsForActiveAdd.map((c) => c.sph)),
    [cellsForActiveAdd],
  );
  const cylList = useMemo(
    () => uniqAsc(cellsForActiveAdd.map((c) => c.cyl)),
    [cellsForActiveAdd],
  );
  const cellMap = useMemo(() => {
    const m = new Map<string, LensStockCell>();
    cellsForActiveAdd.forEach((c) => m.set(sphCylKey(c.sph, c.cyl), c));
    return m;
  }, [cellsForActiveAdd]);

  // ---- render branches ----

  return (
    <div>
      <MatrixHeader line={line} onBack={onBack} />

      {/* ADD tab strip (bifocal/progressive only) */}
      {line.has_add && addValues.length > 0 && addValues[0] !== null ? (
        <div className="flex gap-1 mb-2">
          {addValues.map((a) => {
            const active = a === activeAdd;
            const label = a === null ? 'SV' : `ADD +${a.toFixed(2)}`;
            return (
              <button
                key={String(a)}
                type="button"
                onClick={() => setActiveAdd(a)}
                className={
                  `px-3 py-1 text-xs font-medium rounded-full border transition ${active
                    ? 'bg-bv text-white border-bv'
                    : 'bg-white text-ink-3 border-line hover:bg-bv-50 hover:text-bv'}`
                }
              >
                {label}
              </button>
            );
          })}
        </div>
      ) : null}

      {error ? (
        <ErrorBox msg={error} onRetry={load} />
      ) : loading ? (
        <LoadingBox />
      ) : cellsForActiveAdd.length === 0 ? (
        <EmptyBox
          msg={
            cells.length === 0
              ? 'No stock cells for this lens line at the selected store. Seed cells via Lens Stock (bulk import) or POS purchases.'
              : 'No cells for this ADD value. Pick a different ADD tab above.'
          }
        />
      ) : (
        <MatrixTable
          sphList={sphList}
          cylList={cylList}
          cellMap={cellMap}
          onCellClick={onCellClick}
        />
      )}
    </div>
  );
}

function MatrixHeader({ line, onBack }: { line: LensLine; onBack?: () => void }) {
  return (
    <div className="flex items-center justify-between mb-2">
      <div className="flex items-center gap-2 text-sm">
        {onBack ? (
          <button
            type="button"
            onClick={onBack}
            className="text-ink-4 hover:text-bv text-xs"
          >
            &larr; Back to lines
          </button>
        ) : null}
        <span className="font-semibold text-ink">{line.brand} / {line.series}</span>
        <span className="text-ink-4 font-mono text-xs">{line.index.toFixed(2)}</span>
        <span className="text-ink-4 text-xs">{line.material}</span>
        <span className="text-ink-4 text-xs">{line.lens_type}</span>
        <span className="px-1.5 py-0.5 rounded bg-bv-soft text-bv text-xs">{line.coating}</span>
      </div>
    </div>
  );
}

interface MatrixTableProps {
  sphList: number[];
  cylList: number[];
  cellMap: Map<string, LensStockCell>;
  onCellClick: (cell: LensStockCell) => void;
}

function MatrixTable({ sphList, cylList, cellMap, onCellClick }: MatrixTableProps) {
  // Totals strip
  const totals = useMemo(() => {
    let on_hand = 0;
    let reserved = 0;
    let belowReorder = 0;
    cellMap.forEach((c) => {
      on_hand += c.on_hand || 0;
      reserved += c.reserved || 0;
      const rp = c.reorder_point || 0;
      if (rp > 0 && (c.on_hand || 0) <= rp) belowReorder += 1;
    });
    return { on_hand, reserved, cells: cellMap.size, belowReorder };
  }, [cellMap]);

  return (
    <>
      <p className="text-xs text-ink-4 mb-2 flex items-center gap-3">
        <span><span className="font-semibold text-ink-2">{totals.on_hand}</span> units on hand</span>
        <span>&middot;</span>
        <span><span className="font-semibold text-ink-2">{totals.reserved}</span> reserved</span>
        <span>&middot;</span>
        <span>{totals.cells} cells</span>
        {totals.belowReorder > 0 ? (
          <>
            <span>&middot;</span>
            <span className="text-warn">{totals.belowReorder} at/below reorder</span>
          </>
        ) : null}
      </p>
      <div className="overflow-auto border border-line rounded-lg bg-surface">
        <table className="text-xs border-collapse">
          <thead className="sticky top-0 bg-bg-sunk z-10">
            <tr>
              <th className="sticky left-0 bg-bg-sunk px-2 py-1 text-ink-4 font-medium border-b border-r border-line">
                SPH \ CYL
              </th>
              {cylList.map((c) => (
                <th key={c} className="bg-bg-sunk px-2 py-1 text-ink-3 font-medium border-b border-line whitespace-nowrap">
                  {fmtPower(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sphList.map((s) => (
              <tr key={s}>
                <td className="sticky left-0 bg-surface-2 px-2 py-1 text-ink-3 font-medium border-r border-line whitespace-nowrap z-10">
                  {fmtPower(s)}
                </td>
                {cylList.map((c) => {
                  // cellMap is already filtered to the active ADD upstream,
                  // so the (sph, cyl) lookup is sufficient.
                  const actualCell = cellMap.get(sphCylKey(s, c));
                  const onHand = actualCell?.on_hand ?? 0;
                  const reserved = actualCell?.reserved ?? 0;
                  const rp = actualCell?.reorder_point ?? 0;
                  const belowRp = rp > 0 && onHand <= rp;
                  const heat = cellHeat(onHand);
                  const isEmpty = !actualCell;
                  return (
                    <td
                      key={c}
                      className={`relative border border-line-soft text-center ${heat} ${isEmpty ? '' : 'cursor-pointer hover:ring-2 hover:ring-bv-50 hover:ring-inset'}`}
                    >
                      {actualCell ? (
                        <button
                          type="button"
                          onClick={() => onCellClick(actualCell)}
                          className="w-full h-full px-2 py-1 block"
                          title={`SPH ${fmtPower(s)} / CYL ${fmtPower(c)}: ${onHand} on hand, ${reserved} reserved${rp > 0 ? `, reorder@${rp}` : ''}`}
                        >
                          <span className="font-medium">{onHand || ''}</span>
                          {reserved > 0 ? (
                            <span className="absolute top-0 right-0 text-[9px] font-bold bg-info-50 text-info px-1 rounded-bl">
                              {reserved}r
                            </span>
                          ) : null}
                          {belowRp ? (
                            <span className="absolute bottom-0 left-0 text-[8px] text-warn">!</span>
                          ) : null}
                        </button>
                      ) : (
                        <span className="px-2 py-1 block opacity-30">-</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-ink-4 mt-1.5 flex items-center gap-3">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded bg-info-50 border border-info" />
          reserved count (top-right)
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded bg-bv text-white text-center font-bold leading-3">!</span>
          at/below reorder point
        </span>
        <span className="inline-flex items-center gap-1 ml-auto">
          Click a cell for details
        </span>
      </p>
    </>
  );
}

// ----------------------------------------------------------------------------
// Cell drawer (B'3 wires the actual edits; B'2 just shows the detail)
// ----------------------------------------------------------------------------

interface CellDrawerProps {
  cell: LensStockCell;
  line: LensLine;
  onClose: () => void;
}

function CellDrawer({ cell, line, onClose }: CellDrawerProps) {
  const [audit, setAudit] = useState<LensStockAuditRow[]>([]);
  const [auditLoading, setAuditLoading] = useState(true);
  const [auditError, setAuditError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setAuditLoading(true);
    setAuditError(null);
    lensStockApi.audit(cell.line_stock_id, 20)
      .then((r) => { if (alive) setAudit(r.audit || []); })
      .catch((e) => {
        if (alive) setAuditError(e instanceof Error ? e.message : 'Failed to load history');
      })
      .finally(() => { if (alive) setAuditLoading(false); });
    return () => { alive = false; };
  }, [cell.line_stock_id]);

  // Esc-key dismiss. Owner-requested per the coordinator note: click outside,
  // Esc, or X all close the drawer. Listener is window-level so focus on
  // the drawer body doesn't swallow it.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 bg-black/30 flex justify-end z-50" onClick={onClose}>
      <div className="bg-surface w-full max-w-xl h-full overflow-y-auto shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3 sticky top-0 bg-surface z-10">
          <div>
            <h3 className="font-semibold text-ink text-sm">
              {line.brand} / {line.series}
            </h3>
            <p className="text-[11px] text-ink-4">
              SPH {fmtPower(cell.sph)} / CYL {fmtPower(cell.cyl)}
              {cell.add !== null && cell.add !== undefined ? ` / ADD +${cell.add.toFixed(2)}` : ''}
              <span className="mx-1">&middot;</span>
              {cell.store_id}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-ink-4 hover:text-ink"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* Stock numbers */}
          <div className="grid grid-cols-3 gap-2">
            <DrawerStat label="On hand" value={cell.on_hand} tone="primary" />
            <DrawerStat label="Reserved" value={cell.reserved} tone="info" />
            <DrawerStat label="Available" value={cell.available} tone="ok" />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <DrawerStat label="Reorder point" value={cell.reorder_point ?? 0} />
            <DrawerStat label="Safety stock" value={cell.safety_stock ?? 0} />
          </div>

          {/* Identity. lens_stock_lines is qty-tracked (not serialized),
              so there are no per-unit IDs to list -- the cell IS the unit
              of accounting. Frames + CL keep serialization (stock_units). */}
          <div className="border border-line rounded-lg p-3 text-xs text-ink-3 space-y-1">
            <div className="flex justify-between"><span>Lens line ID</span><span className="font-mono text-[11px] text-ink-2">{cell.lens_line_id}</span></div>
            <div className="flex justify-between"><span>Stock row ID</span><span className="font-mono text-[11px] text-ink-2">{cell.line_stock_id}</span></div>
            <div className="flex justify-between"><span>Last counted</span><span>{cell.last_counted_at ? cell.last_counted_at.slice(0, 10) : 'Never'}</span></div>
            <div className="flex justify-between"><span>Last movement</span><span>{cell.last_movement_at ? cell.last_movement_at.slice(0, 10) : '-'}</span></div>
          </div>

          {/* Adjust controls (B'3 wires the actual writes) */}
          <div className="border border-bv-50 bg-bv-soft rounded-lg p-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-semibold text-bv uppercase tracking-wide">Manual adjust</span>
              <span className="text-[10px] text-ink-4">(read-only in B'2; edits land in B'3)</span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <button
                type="button"
                disabled
                className="border border-line bg-white rounded px-2 py-1.5 text-ink-4 disabled:cursor-not-allowed"
                title="Wired in B'3"
              >
                Adjust on-hand
              </button>
              <button
                type="button"
                disabled
                className="border border-line bg-white rounded px-2 py-1.5 text-ink-4 disabled:cursor-not-allowed"
                title="Wired in B'3"
              >
                Set reorder point
              </button>
            </div>
          </div>

          {/* Audit history */}
          <div>
            <h4 className="text-xs font-semibold text-ink-3 uppercase tracking-wide mb-2 flex items-center gap-1.5">
              <History className="w-3.5 h-3.5" /> Movement history
            </h4>
            {auditLoading ? (
              <div className="text-xs text-ink-4 flex items-center gap-2">
                <Loader2 className="w-3 h-3 animate-spin" /> Loading...
              </div>
            ) : auditError ? (
              <div className="text-xs text-err">{auditError}</div>
            ) : audit.length === 0 ? (
              <div className="text-xs text-ink-4">No movement history yet.</div>
            ) : (
              <div className="space-y-1">
                {audit.map((row, i) => (
                  <div
                    key={row.audit_id || `${row.at}-${i}`}
                    className="flex items-start gap-2 text-xs border-b border-line-soft pb-1"
                  >
                    <span className="font-medium text-ink-2 w-20 flex-shrink-0">{row.action}</span>
                    <span className="text-ink-3 flex-1">
                      {row.delta_on_hand !== 0 ? (
                        <span className={row.delta_on_hand > 0 ? 'text-ok' : 'text-err'}>
                          {row.delta_on_hand > 0 ? '+' : ''}{row.delta_on_hand}{' '}
                        </span>
                      ) : null}
                      {row.delta_reserved !== 0 ? (
                        <span className="text-info">
                          ({row.delta_reserved > 0 ? '+' : ''}{row.delta_reserved}r){' '}
                        </span>
                      ) : null}
                      <span className="text-ink-4">{row.by_user_name || row.by_user_id || ''}</span>
                    </span>
                    <span className="text-ink-5 text-[10px] flex-shrink-0">
                      {row.at ? row.at.slice(0, 16).replace('T', ' ') : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function DrawerStat({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: number;
  tone?: 'default' | 'primary' | 'info' | 'ok';
}) {
  const toneClass =
    tone === 'primary' ? 'text-bv' :
    tone === 'info' ? 'text-info' :
    tone === 'ok' ? 'text-ok' :
    'text-ink-2';
  return (
    <div className="border border-line rounded-lg p-2 bg-surface">
      <div className="text-[10px] uppercase tracking-wide text-ink-4 font-medium">{label}</div>
      <div className={`text-lg font-semibold ${toneClass}`}>{value}</div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Shared states
// ----------------------------------------------------------------------------

function LoadingBox() {
  return (
    <div className="border border-line rounded-lg bg-surface p-6 flex items-center justify-center gap-2 text-sm text-ink-4">
      <Loader2 className="w-4 h-4 animate-spin" /> Loading...
    </div>
  );
}

function EmptyBox({ msg, onClear }: { msg: string; onClear?: () => void }) {
  return (
    <div className="border border-dashed border-line-strong rounded-lg bg-surface p-8 text-center flex flex-col items-center gap-2">
      <AlertTriangle className="w-5 h-5 text-ink-5" />
      <p className="text-sm text-ink-3 max-w-md">{msg}</p>
      {onClear ? (
        <button
          type="button"
          onClick={onClear}
          className="text-xs text-bv hover:text-bv-600 underline-offset-2 hover:underline mt-1"
        >
          Clear filters
        </button>
      ) : null}
    </div>
  );
}

function ErrorBox({ msg, onRetry }: { msg: string; onRetry: () => void }) {
  return (
    <div className="border border-err-50 rounded-lg bg-err-50 p-4 flex items-start gap-2 text-sm text-err">
      <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <div className="flex-1">
        <p className="font-medium">{msg}</p>
        <button
          type="button"
          onClick={onRetry}
          className="text-xs underline-offset-2 hover:underline mt-1"
        >
          Retry
        </button>
      </div>
    </div>
  );
}
