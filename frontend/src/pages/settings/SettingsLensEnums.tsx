// ============================================================================
// IMS 2.0 - Settings: Lens Catalog Enums (Branch B' sub-PR 3)
// ============================================================================
// Owner-editable enum lists that drive the typed lens catalog (lens_catalog +
// lens_stock_lines). Distinct from the legacy "Lens Master" tab (which feeds
// the old products-based lens picker). Here the owner edits the value lists
// the new power-grid + lens-line master validate against:
//   brands / coatings / indexes / materials / lens_types
//
// Each value supports: add, rename (CASCADE), delete (blocked if in use).
// Rename hits POST /lens-enums/{type}/rename which atomically rewrites every
// lens_line + stamps every dependent stock row, then audit-logs the cascade.
// Delete hits DELETE /lens-enums/{type}/items/{item} which 409s with the
// in-use count when an active lens line still references the value.
//
// `series` is intentionally excluded -- it is a per-brand list edited
// wholesale, out of scope for this row-level editor.
//
// BV brand tokens only (bv / bv-600 / bv-50 / bv-soft). No mock data.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, Pencil, Trash2, Check, X, Loader2, AlertTriangle, Layers } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { lensEnumsApi, type LensEnumType } from '../../services/api/lensCatalog';

// The five editable enum types + their display labels. `series` is excluded.
const ENUM_TABS: Array<{ type: LensEnumType; label: string; numeric?: boolean; hint: string }> = [
  { type: 'brands', label: 'Brands', hint: 'Lens manufacturer / house brands (e.g. Essilor, Zeiss).' },
  { type: 'coatings', label: 'Coatings', hint: 'Single coating codes. Combos (DUAL_COAT) are their own code.' },
  { type: 'indexes', label: 'Indices', numeric: true, hint: 'Refractive indices (1.50, 1.56, 1.60, ...). Must be > 1.' },
  { type: 'materials', label: 'Materials', hint: 'Lens material codes (CR39, POLY, MR8, ...).' },
  { type: 'lens_types', label: 'Lens types', hint: 'SV / BIFOCAL / PROGRESSIVE / OFFICE / READING / ...' },
];

type EnumItem = string | number;

interface EnumState {
  brands: string[];
  coatings: string[];
  indexes: number[];
  materials: string[];
  lens_types: string[];
}

const EMPTY_STATE: EnumState = {
  brands: [],
  coatings: [],
  indexes: [],
  materials: [],
  lens_types: [],
};

export function LensCatalogEnumsSection() {
  const toast = useToast();

  const [enums, setEnums] = useState<EnumState>(EMPTY_STATE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeType, setActiveType] = useState<LensEnumType>('brands');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await lensEnumsApi.list();
      const e = r.enums || {};
      setEnums({
        brands: (e.brands || []) as string[],
        coatings: (e.coatings || []) as string[],
        indexes: (e.indexes || []) as number[],
        materials: (e.materials || []) as string[],
        lens_types: (e.lens_types || []) as string[],
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load lens enums');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const activeTab = useMemo(
    () => ENUM_TABS.find((t) => t.type === activeType) || ENUM_TABS[0],
    [activeType],
  );
  const items: EnumItem[] = enums[activeType] || [];

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-ink mb-1">Lens Catalog Enums</h2>
        <p className="text-sm text-ink-4 mb-4">
          Editable value lists that drive the typed lens catalog + power grid. Renaming a value
          cascades to every lens line and stock row that uses it (audit-logged). A value cannot be
          deleted while an active lens line still references it.
        </p>

        {/* Enum-type tabs */}
        <div className="flex flex-wrap gap-1.5 mb-4">
          {ENUM_TABS.map((t) => {
            const on = t.type === activeType;
            const count = (enums[t.type] || []).length;
            return (
              <button
                key={t.type}
                type="button"
                onClick={() => setActiveType(t.type)}
                className={
                  'px-3 py-1.5 text-sm font-medium rounded-lg border transition ' +
                  (on
                    ? 'bg-bv text-white border-bv'
                    : 'bg-white text-ink-3 border-line hover:bg-bv-50 hover:text-bv')
                }
              >
                {t.label}
                <span className={'ml-1.5 text-xs ' + (on ? 'text-white/80' : 'text-ink-4')}>
                  {count}
                </span>
              </button>
            );
          })}
        </div>

        {error ? (
          <div className="border border-err-50 bg-err-50 rounded-lg p-3 flex items-start gap-2 text-sm text-err">
            <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <p className="font-medium">{error}</p>
              <button type="button" onClick={load} className="text-xs underline-offset-2 hover:underline mt-1">
                Retry
              </button>
            </div>
          </div>
        ) : loading ? (
          <div className="flex items-center gap-2 text-sm text-ink-4 py-6 justify-center">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading lens enums...
          </div>
        ) : (
          <EnumEditor
            key={activeType}
            enumType={activeType}
            label={activeTab.label}
            hint={activeTab.hint}
            numeric={!!activeTab.numeric}
            items={items}
            onChanged={load}
            onError={(m) => toast.error(m)}
            onSuccess={(m) => toast.success(m)}
          />
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Per-enum-type editor
// ----------------------------------------------------------------------------

interface EnumEditorProps {
  enumType: LensEnumType;
  label: string;
  hint: string;
  numeric: boolean;
  items: EnumItem[];
  onChanged: () => void;
  onError: (msg: string) => void;
  onSuccess: (msg: string) => void;
}

function EnumEditor({ enumType, label, hint, numeric, items, onChanged, onError, onSuccess }: EnumEditorProps) {
  const [addValue, setAddValue] = useState('');
  const [adding, setAdding] = useState(false);
  // Which item (by string key) is in rename mode + its draft value.
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const keyOf = (it: EnumItem) => String(it);

  const coerce = (raw: string): EnumItem | null => {
    const t = raw.trim();
    if (!t) return null;
    if (numeric) {
      const n = Number(t);
      if (!Number.isFinite(n) || n <= 1) return null;
      return n;
    }
    return t;
  };

  const onAdd = async () => {
    const val = coerce(addValue);
    if (val === null) {
      onError(numeric ? 'Enter a number greater than 1.' : 'Enter a non-empty value.');
      return;
    }
    setAdding(true);
    try {
      await lensEnumsApi.addItem(enumType, val);
      onSuccess(`Added "${val}" to ${label.toLowerCase()}.`);
      setAddValue('');
      onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Failed to add value.');
    } finally {
      setAdding(false);
    }
  };

  const startRename = (it: EnumItem) => {
    setRenaming(keyOf(it));
    setRenameDraft(String(it));
  };

  const cancelRename = () => {
    setRenaming(null);
    setRenameDraft('');
  };

  const onRename = async (oldItem: EnumItem) => {
    const next = coerce(renameDraft);
    if (next === null) {
      onError(numeric ? 'Enter a number greater than 1.' : 'Enter a non-empty value.');
      return;
    }
    if (String(next) === String(oldItem)) {
      cancelRename();
      return;
    }
    setBusyKey(keyOf(oldItem));
    try {
      const res = await lensEnumsApi.rename(enumType, oldItem, next);
      const c = res.cascade;
      onSuccess(
        `Renamed to "${next}". Updated ${c.catalog_rows_updated} lens line` +
          `${c.catalog_rows_updated === 1 ? '' : 's'}` +
          (c.stock_rows_stamped ? ` and ${c.stock_rows_stamped} stock row${c.stock_rows_stamped === 1 ? '' : 's'}` : '') +
          '.',
      );
      cancelRename();
      onChanged();
    } catch (e) {
      // Cascade collision (409) + validation (400) come through here with a
      // human message from the backend.
      onError(e instanceof Error ? e.message : 'Failed to rename value.');
    } finally {
      setBusyKey(null);
    }
  };

  const onDelete = async (it: EnumItem) => {
    if (!window.confirm(`Delete "${it}" from ${label.toLowerCase()}? This is blocked if any active lens line still uses it.`)) {
      return;
    }
    setBusyKey(keyOf(it));
    try {
      await lensEnumsApi.deleteItem(enumType, it);
      onSuccess(`Deleted "${it}".`);
      onChanged();
    } catch (e) {
      // 409 carries the in-use count in the message.
      onError(e instanceof Error ? e.message : 'Failed to delete value.');
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div>
      <p className="text-xs text-ink-4 mb-3">{hint}</p>

      {/* Add row */}
      <div className="flex items-center gap-2 mb-4">
        <input
          type={numeric ? 'number' : 'text'}
          step={numeric ? '0.01' : undefined}
          value={addValue}
          disabled={adding}
          onChange={(e) => setAddValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') onAdd(); }}
          placeholder={numeric ? 'e.g. 1.59' : `New ${label.toLowerCase().replace(/s$/, '')}...`}
          className="flex-1 max-w-xs border border-line rounded-lg px-3 py-1.5 text-sm bg-white focus:outline-none focus:border-bv focus:ring-1 focus:ring-bv-50 disabled:bg-bg-sunk"
        />
        <button
          type="button"
          onClick={onAdd}
          disabled={adding || !addValue.trim()}
          className="inline-flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-lg bg-bv text-white hover:bg-bv-600 disabled:opacity-50 disabled:cursor-not-allowed transition"
        >
          {adding ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
          Add
        </button>
      </div>

      {/* Value list */}
      {items.length === 0 ? (
        <div className="border border-dashed border-line-strong rounded-lg bg-surface p-6 text-center flex flex-col items-center gap-2">
          <Layers className="w-5 h-5 text-ink-5" />
          <p className="text-sm text-ink-3">
            No {label.toLowerCase()} configured yet. Add one above so lens lines can use it.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {items.map((it) => {
            const k = keyOf(it);
            const isRenaming = renaming === k;
            const busy = busyKey === k;
            return (
              <div
                key={k}
                className="flex items-center justify-between gap-2 px-3 py-2 bg-surface-2 border border-line rounded-lg"
              >
                {isRenaming ? (
                  <input
                    type={numeric ? 'number' : 'text'}
                    step={numeric ? '0.01' : undefined}
                    value={renameDraft}
                    autoFocus
                    disabled={busy}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') onRename(it);
                      if (e.key === 'Escape') cancelRename();
                    }}
                    className="flex-1 min-w-0 border border-bv rounded px-2 py-1 text-sm bg-white focus:outline-none focus:ring-1 focus:ring-bv-50"
                  />
                ) : (
                  <span className="text-sm text-ink truncate flex-1 min-w-0">{String(it)}</span>
                )}

                <div className="flex items-center gap-1 flex-shrink-0">
                  {isRenaming ? (
                    <>
                      <button
                        type="button"
                        onClick={() => onRename(it)}
                        disabled={busy}
                        className="p-1 rounded text-bv hover:bg-bv-50 disabled:opacity-50"
                        title="Save rename (cascades)"
                        aria-label="Save rename"
                      >
                        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                      </button>
                      <button
                        type="button"
                        onClick={cancelRename}
                        disabled={busy}
                        className="p-1 rounded text-ink-4 hover:bg-bg-sunk disabled:opacity-50"
                        title="Cancel"
                        aria-label="Cancel rename"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => startRename(it)}
                        disabled={busy}
                        className="p-1 rounded text-ink-4 hover:text-bv hover:bg-bv-50 disabled:opacity-50"
                        title="Rename (cascades to lens lines + stock)"
                        aria-label={`Rename ${String(it)}`}
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => onDelete(it)}
                        disabled={busy}
                        className="p-1 rounded text-ink-4 hover:text-err hover:bg-err-50 disabled:opacity-50"
                        title="Delete (blocked if in use)"
                        aria-label={`Delete ${String(it)}`}
                      >
                        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default LensCatalogEnumsSection;
