// ============================================================================
// IMS 2.0 - Rapid Grid (Phase B of the product-add redesign)
// ============================================================================
// A spreadsheet-style grid for adding MANY products fast, in-app. Each row is
// one SKU with ~7 core columns (Category, Brand, Model, SKU, MRP, Offer, Qty);
// a per-row expand drawer reveals the FULL per-category fields (CATEGORY_FIELDS
// from productAddShared, shared with Quick Add + the Guided wizard). Two
// actions: "Validate all" (client-side per-row check, status chips) and
// "Save N valid" (POST to /products/bulk-create, per-row results back).
//
// REUSE, don't duplicate: a grid row is mapped to the same ProductFormValues
// shape Quick Add uses, then run through validateProductForm + buildProductPayload
// so the POST body is byte-identical to single-create.
//
// NO CSV / file import — in-app only (the owner forbade file import). Optical
// Lens (LS) rows can't be saved here (lenses go through the Power Grid); such
// rows show a note + a link and are reported as skipped.

import { useCallback, useMemo, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import {
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  Save,
  ListChecks,
  Sparkles,
  ClipboardPaste,
} from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { productApi } from '../../services/api/products';
import type { CreateProductPayload } from '../../services/api/products';
import {
  CATEGORIES,
  CATEGORY_FIELDS,
  validateProductForm,
  buildProductPayload,
  resolveHsnGst,
  type CategoryField,
  type ProductFormValues,
} from './productAddShared';

// ----------------------------------------------------------------------------
// Row model
// ----------------------------------------------------------------------------
// A row keeps the core cells as discrete fields for the grid + a free-form
// `attributes` bag for everything the expand drawer collects (the per-category
// fields). At map time the core cells are folded into `attributes` under the
// names buildProductPayload reads (brand_name / model_no / sku).

type RowStatus =
  | { kind: 'idle' }
  | { kind: 'ok' }
  | { kind: 'error'; messages: string[] }
  | { kind: 'created'; sku: string }
  | { kind: 'skipped'; reason: string };

interface GridRow {
  id: string;
  category: string;
  brand: string;
  model: string;
  sku: string;
  mrp: string;
  offerPrice: string;
  initialQuantity: string;
  // Full per-category fields (drawer). Core cells are NOT stored here; they are
  // merged in at map time so the grid stays the single source for them.
  attributes: Record<string, string>;
  status: RowStatus;
}

let _rowSeq = 0;
const newRow = (seed?: Partial<GridRow>): GridRow => ({
  id: `r${Date.now().toString(36)}-${_rowSeq++}`,
  category: '',
  brand: '',
  model: '',
  sku: '',
  mrp: '',
  offerPrice: '',
  initialQuantity: '0',
  attributes: {},
  status: { kind: 'idle' },
  ...seed,
});

const LENS_CODE = 'LS';
// Lens stock-power fields live in the Power Grid, not here (same as Quick Add).
const LENS_POWER_FIELDS = new Set(['sph', 'cyl', 'axis', 'add']);
// Core columns are surfaced as dedicated grid cells; don't repeat them as
// drawer inputs (they map to these attribute names).
const CORE_ATTR_NAMES = new Set([
  'brand_name',
  'model_no',
  'model_name',
  'sku',
  'barcode',
]);

// Build the ProductFormValues for a row (same shape Quick Add assembles). Folds
// the core grid cells into `attributes` so buildProductPayload / validateProductForm
// see them, and auto-resolves HSN + GST from the category (mirrors Quick Add).
function rowToFormValues(row: GridRow): ProductFormValues {
  const { hsnCode, gstRate } = row.category
    ? resolveHsnGst(row.category, false)
    : { hsnCode: '', gstRate: '18' };

  const attributes: Record<string, string> = { ...row.attributes };
  if (row.brand) attributes.brand_name = row.brand;
  // The model cell feeds whichever model key the category uses. Setting both is
  // harmless — buildProductPayload reads model_no first, then model_name.
  if (row.model) {
    attributes.model_no = row.model;
    attributes.model_name = row.model;
  }
  if (row.sku) attributes.sku = row.sku;

  return {
    category: row.category,
    attributes,
    description: undefined,
    hsnCode,
    gstRate,
    weight: undefined,
    mrp: row.mrp,
    offerPrice: row.offerPrice || undefined,
    costPrice: undefined,
    discountCategory: 'MASS',
    syncToShopify: false,
    shopifyTags: [],
    publishPOS: true,
  };
}

// A row is a lens row (handled by the Power Grid, not saveable here).
const isLensRow = (row: GridRow) => row.category === LENS_CODE;

// Validate a single row client-side. Returns [] when OK. Lens rows return a
// single "use the Power Grid" message (they are skipped, not saved).
function validateRow(row: GridRow): string[] {
  if (isLensRow(row)) return ['Optical Lens stock is managed in the Power Grid'];
  const errors = validateProductForm(rowToFormValues(row));
  return Object.values(errors);
}

// A row is "empty" (an untouched spare row) when no core cell has content. Empty
// rows are ignored by Validate/Save so trailing blanks don't error.
const isRowEmpty = (row: GridRow): boolean =>
  !row.category &&
  !row.brand.trim() &&
  !row.model.trim() &&
  !row.sku.trim() &&
  !row.mrp.trim() &&
  !row.offerPrice.trim();

export function RapidGridPage() {
  const { hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();

  const canAddProduct = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);

  const [rows, setRows] = useState<GridRow[]>(() => [newRow(), newRow(), newRow()]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [isSaving, setIsSaving] = useState(false);
  const [lastRun, setLastRun] = useState<{ created: number; failed: number; skipped: number } | null>(null);

  // -------- row mutation helpers -------------------------------------------
  const patchRow = useCallback((id: string, patch: Partial<GridRow>) => {
    setRows((prev) =>
      prev.map((r) =>
        r.id === id ? { ...r, ...patch, status: { kind: 'idle' } } : r,
      ),
    );
  }, []);

  const patchAttr = useCallback((id: string, name: string, value: string) => {
    setRows((prev) =>
      prev.map((r) =>
        r.id === id
          ? { ...r, attributes: { ...r.attributes, [name]: value }, status: { kind: 'idle' } }
          : r,
      ),
    );
  }, []);

  const addRows = useCallback((n = 1) => {
    setRows((prev) => [...prev, ...Array.from({ length: n }, () => newRow())]);
  }, []);

  const removeRow = useCallback((id: string) => {
    setRows((prev) => (prev.length > 1 ? prev.filter((r) => r.id !== id) : prev));
    setExpanded((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const toggleExpand = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Paste-friendly: paste TSV/CSV (e.g. from a spreadsheet) into the first
  // cell of a row to fill Category?/Brand/Model/SKU/MRP/Offer/Qty across columns
  // and spill extra pasted lines into following rows. This is in-app paste of
  // already-typed text — NOT a file import.
  const handlePaste = useCallback(
    (rowId: string, e: React.ClipboardEvent<HTMLInputElement>, startCol: number) => {
      const text = e.clipboardData.getData('text/plain');
      if (!text || (!text.includes('\t') && !text.includes('\n'))) return; // single value -> normal paste
      e.preventDefault();
      const lines = text.replace(/\r/g, '').split('\n').filter((l) => l.length > 0);
      // Column order for the editable cells (index 0 = brand, since category is
      // a dropdown that paste skips). startCol lets a paste into MRP start there.
      const COLS: (keyof GridRow)[] = ['brand', 'model', 'sku', 'mrp', 'offerPrice', 'initialQuantity'];
      setRows((prev) => {
        const next = [...prev];
        const baseIdx = next.findIndex((r) => r.id === rowId);
        if (baseIdx < 0) return prev;
        lines.forEach((line, li) => {
          const cells = line.split('\t');
          const targetIdx = baseIdx + li;
          // Grow the grid if the paste is taller than the current rows.
          while (next.length <= targetIdx) next.push(newRow());
          const target = { ...next[targetIdx], status: { kind: 'idle' as const } };
          cells.forEach((cell, ci) => {
            const col = COLS[startCol + ci];
            if (col) (target as Record<string, unknown>)[col] = cell.trim();
          });
          next[targetIdx] = target;
        });
        return next;
      });
    },
    [],
  );

  // -------- validation -----------------------------------------------------
  const runValidation = useCallback(() => {
    let okCount = 0;
    let errCount = 0;
    let lensCount = 0;
    setRows((prev) =>
      prev.map((r) => {
        if (isRowEmpty(r)) return { ...r, status: { kind: 'idle' } };
        if (isLensRow(r)) {
          lensCount += 1;
          return { ...r, status: { kind: 'skipped', reason: 'Use the Power Grid' } };
        }
        const msgs = validateRow(r);
        if (msgs.length === 0) {
          okCount += 1;
          return { ...r, status: { kind: 'ok' } };
        }
        errCount += 1;
        return { ...r, status: { kind: 'error', messages: msgs } };
      }),
    );
    if (okCount === 0 && errCount === 0 && lensCount === 0) {
      toast.info('Add some rows first.');
    } else {
      toast.success(`${okCount} valid · ${errCount} with errors${lensCount ? ` · ${lensCount} lens (Power Grid)` : ''}`);
    }
  }, [toast]);

  // Rows that are eligible to save (non-empty, non-lens, client-valid).
  const validPayloads = useMemo((): { row: GridRow; payload: CreateProductPayload }[] => {
    const out: { row: GridRow; payload: CreateProductPayload }[] = [];
    rows.forEach((r) => {
      if (isRowEmpty(r) || isLensRow(r)) return;
      if (validateRow(r).length > 0) return;
      out.push({ row: r, payload: buildProductPayload(rowToFormValues(r)) });
    });
    return out;
  }, [rows]);

  const counts = useMemo(() => {
    let nonEmpty = 0;
    let lens = 0;
    rows.forEach((r) => {
      if (isRowEmpty(r)) return;
      nonEmpty += 1;
      if (isLensRow(r)) lens += 1;
    });
    return { nonEmpty, lens, valid: validPayloads.length };
  }, [rows, validPayloads]);

  // -------- save -----------------------------------------------------------
  const handleSave = useCallback(async () => {
    // Always validate first so the chips reflect the latest edits.
    const toSave = validPayloads;
    if (toSave.length === 0) {
      runValidation();
      toast.error('No valid rows to save. Fix the highlighted rows first.');
      return;
    }

    setIsSaving(true);
    try {
      const res = await productApi.bulkCreateProducts(toSave.map((t) => t.payload));
      // Map per-row results (indexed into the SAVED subset) back onto grid rows.
      const resultByIndex = new Map(res.results.map((r) => [r.index, r]));
      setRows((prev) =>
        prev.map((r) => {
          const pos = toSave.findIndex((t) => t.row.id === r.id);
          if (pos < 0) {
            // Not part of this save (empty / lens / invalid). Tag lens rows as skipped.
            if (!isRowEmpty(r) && isLensRow(r)) {
              return { ...r, status: { kind: 'skipped', reason: 'Use the Power Grid' } };
            }
            return r;
          }
          const result = resultByIndex.get(pos);
          if (result && result.ok) {
            return { ...r, status: { kind: 'created', sku: result.sku } };
          }
          return {
            ...r,
            status: { kind: 'error', messages: result?.errors ?? ['Failed to create'] },
          };
        }),
      );

      const skipped = counts.lens;
      setLastRun({ created: res.summary.created, failed: res.summary.failed, skipped });
      if (res.summary.created > 0) {
        toast.success(`Created ${res.summary.created} product${res.summary.created === 1 ? '' : 's'}.`);
      }
      if (res.summary.failed > 0) {
        toast.warning(`${res.summary.failed} row${res.summary.failed === 1 ? '' : 's'} failed — see the chips.`);
      }
    } catch {
      toast.error('Bulk save failed. Please try again.');
    } finally {
      setIsSaving(false);
    }
  }, [validPayloads, runValidation, toast, counts.lens]);

  if (!canAddProduct) {
    return (
      <div className="inv-body">
        <div className="card text-center py-12">
          <h2 className="text-xl font-semibold text-gray-700">Access Denied</h2>
          <p className="text-gray-500 mt-1">You don't have permission to add products.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="inv-body">
      {/* Editorial header (mode toggle is rendered by the route shell) */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Catalog · Rapid Grid</div>
          <h1>Many SKUs. One grid.</h1>
          <div className="hint">
            Type or paste rows, expand any row for the full category fields, then
            <strong> Validate</strong> and <strong>Save</strong>. Category sets HSN + GST automatically.
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <button type="button" onClick={() => addRows(1)} className="btn-secondary flex items-center gap-2">
          <Plus className="w-4 h-4" /> Add row
        </button>
        <button type="button" onClick={() => addRows(5)} className="btn-secondary flex items-center gap-2">
          <Plus className="w-4 h-4" /> Add 5
        </button>
        <button type="button" onClick={runValidation} className="btn-secondary flex items-center gap-2">
          <ListChecks className="w-4 h-4" /> Validate all
        </button>
        <div className="flex-1" />
        <span className="text-sm text-gray-500">
          {counts.valid} valid · {counts.nonEmpty} filled
          {counts.lens > 0 && <> · {counts.lens} lens</>}
        </span>
        <button
          type="button"
          onClick={handleSave}
          disabled={isSaving || counts.valid === 0}
          className="btn-primary flex items-center gap-2"
        >
          {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          Save {counts.valid} valid
        </button>
      </div>

      {/* Paste hint */}
      <div className="flex items-start gap-2 text-xs text-gray-500 mb-3">
        <ClipboardPaste className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>
          Tip: copy cells from a spreadsheet and paste into the <strong>Brand</strong> cell to fill a row
          (Brand · Model · SKU · MRP · Offer · Qty) and spill extra lines into following rows. Leave SKU blank to auto-generate.
        </span>
      </div>

      {/* Last-run summary */}
      {lastRun && (
        <div className="card !py-3 mb-4 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span className="flex items-center gap-1.5 text-green-700">
            <CheckCircle2 className="w-4 h-4" /> {lastRun.created} created
          </span>
          {lastRun.failed > 0 && (
            <span className="flex items-center gap-1.5 text-red-600">
              <AlertTriangle className="w-4 h-4" /> {lastRun.failed} failed
            </span>
          )}
          {lastRun.skipped > 0 && (
            <span className="text-gray-500">{lastRun.skipped} lens skipped (Power Grid)</span>
          )}
          <button
            type="button"
            onClick={() => navigate('/inventory')}
            className="ml-auto text-bv font-medium hover:underline"
          >
            View inventory →
          </button>
        </div>
      )}

      {/* Grid */}
      <div className="card !p-0 overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <th className="w-8 px-2 py-2.5" />
              <th className="px-3 py-2.5 min-w-[140px]">Category</th>
              <th className="px-3 py-2.5 min-w-[120px]">Brand</th>
              <th className="px-3 py-2.5 min-w-[120px]">Model</th>
              <th className="px-3 py-2.5 min-w-[120px]">SKU</th>
              <th className="px-3 py-2.5 w-28">MRP</th>
              <th className="px-3 py-2.5 w-28">Offer</th>
              <th className="px-3 py-2.5 w-20">Qty</th>
              <th className="px-3 py-2.5 min-w-[120px]">Status</th>
              <th className="w-10 px-2 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => {
              const open = expanded.has(row.id);
              const lens = isLensRow(row);
              return (
                <RowView
                  key={row.id}
                  row={row}
                  index={idx}
                  open={open}
                  lens={lens}
                  onToggle={() => toggleExpand(row.id)}
                  onPatch={(patch) => patchRow(row.id, patch)}
                  onPatchAttr={(name, value) => patchAttr(row.id, name, value)}
                  onRemove={() => removeRow(row.id)}
                  onPaste={(e, startCol) => handlePaste(row.id, e, startCol)}
                />
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-4">
        <button type="button" onClick={() => addRows(1)} className="btn-secondary flex items-center gap-2">
          <Plus className="w-4 h-4" /> Add row
        </button>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Row + expand drawer
// ----------------------------------------------------------------------------

interface RowViewProps {
  row: GridRow;
  index: number;
  open: boolean;
  lens: boolean;
  onToggle: () => void;
  onPatch: (patch: Partial<GridRow>) => void;
  onPatchAttr: (name: string, value: string) => void;
  onRemove: () => void;
  onPaste: (e: React.ClipboardEvent<HTMLInputElement>, startCol: number) => void;
}

function RowView({
  row, index, open, lens, onToggle, onPatch, onPatchAttr, onRemove, onPaste,
}: RowViewProps) {
  const drawerFields: CategoryField[] = useMemo(() => {
    if (!row.category) return [];
    const fields = CATEGORY_FIELDS[row.category] || [];
    return fields.filter((f) => {
      if (CORE_ATTR_NAMES.has(f.name)) return false; // shown as core cells
      if (lens && LENS_POWER_FIELDS.has(f.name)) return false; // Power Grid
      return true;
    });
  }, [row.category, lens]);

  return (
    <>
      <tr className={clsx('border-b border-gray-100', open && 'bg-bv-soft/40')}>
        {/* expand toggle */}
        <td className="px-2 py-1.5 text-center align-middle">
          <button
            type="button"
            onClick={onToggle}
            disabled={!row.category}
            className="text-gray-400 hover:text-gray-700 disabled:opacity-30"
            aria-label={open ? 'Collapse row' : 'Expand row'}
            aria-expanded={open ? "true" : "false"}
          >
            {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
        </td>

        {/* Category */}
        <td className="px-3 py-1.5">
          <select
            value={row.category}
            onChange={(e) => onPatch({ category: e.target.value })}
            className="input-field w-full !py-1.5 text-sm"
            aria-label={`Row ${index + 1} category`}
          >
            <option value="">Select…</option>
            {CATEGORIES.map((c) => (
              <option key={c.code} value={c.code}>{c.name}</option>
            ))}
          </select>
        </td>

        {/* Brand (paste anchor: startCol 0) */}
        <td className="px-3 py-1.5">
          <input
            value={row.brand}
            onChange={(e) => onPatch({ brand: e.target.value })}
            onPaste={(e) => onPaste(e, 0)}
            className="input-field w-full !py-1.5 text-sm"
            placeholder="Brand"
            aria-label={`Row ${index + 1} brand`}
          />
        </td>

        {/* Model */}
        <td className="px-3 py-1.5">
          <input
            value={row.model}
            onChange={(e) => onPatch({ model: e.target.value })}
            onPaste={(e) => onPaste(e, 1)}
            className="input-field w-full !py-1.5 text-sm"
            placeholder="Model"
            aria-label={`Row ${index + 1} model`}
          />
        </td>

        {/* SKU */}
        <td className="px-3 py-1.5">
          <input
            value={row.sku}
            onChange={(e) => onPatch({ sku: e.target.value })}
            onPaste={(e) => onPaste(e, 2)}
            className="input-field w-full !py-1.5 text-sm font-mono"
            placeholder="Auto"
            aria-label={`Row ${index + 1} SKU`}
          />
        </td>

        {/* MRP */}
        <td className="px-3 py-1.5">
          <input
            type="number"
            value={row.mrp}
            onChange={(e) => onPatch({ mrp: e.target.value })}
            onPaste={(e) => onPaste(e, 3)}
            className="input-field w-full !py-1.5 text-sm"
            placeholder="0"
            aria-label={`Row ${index + 1} MRP`}
          />
        </td>

        {/* Offer */}
        <td className="px-3 py-1.5">
          <input
            type="number"
            value={row.offerPrice}
            onChange={(e) => onPatch({ offerPrice: e.target.value })}
            onPaste={(e) => onPaste(e, 4)}
            className="input-field w-full !py-1.5 text-sm"
            placeholder="= MRP"
            aria-label={`Row ${index + 1} offer price`}
          />
        </td>

        {/* Qty */}
        <td className="px-3 py-1.5">
          <input
            type="number"
            value={row.initialQuantity}
            onChange={(e) => onPatch({ initialQuantity: e.target.value })}
            onPaste={(e) => onPaste(e, 5)}
            className="input-field w-full !py-1.5 text-sm"
            min="0"
            aria-label={`Row ${index + 1} quantity`}
          />
        </td>

        {/* Status chip */}
        <td className="px-3 py-1.5">
          <StatusChip status={row.status} />
        </td>

        {/* remove */}
        <td className="px-2 py-1.5 text-center">
          <button
            type="button"
            onClick={onRemove}
            className="text-gray-300 hover:text-red-500"
            aria-label={`Remove row ${index + 1}`}
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </td>
      </tr>

      {/* Expand drawer */}
      {open && row.category && (
        <tr className="border-b border-gray-100 bg-gray-50/60">
          <td />
          <td colSpan={9} className="px-4 py-4">
            {lens && (
              <div className="mb-3 flex items-start gap-2 rounded-lg border border-bv-50 bg-bv-soft p-3 text-sm">
                <Sparkles className="w-4 h-4 text-bv mt-0.5 shrink-0" />
                <div className="text-gray-700">
                  Optical-lens stock power (SPH × CYL) is managed in the{' '}
                  <Link to="/inventory/power-grid" className="font-medium text-bv underline">Power Grid</Link>{' '}
                  — this row will be <strong>skipped</strong> on save. Use the grid to enter per-power on-hand.
                </div>
              </div>
            )}
            {drawerFields.length === 0 ? (
              <p className="text-sm text-gray-500">No extra fields for this category — the core columns cover it.</p>
            ) : (
              <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-3 gap-3">
                {drawerFields.map((field) => (
                  <DrawerField
                    key={field.name}
                    field={field}
                    value={row.attributes[field.name] || ''}
                    onChange={(v) => onPatchAttr(field.name, v)}
                  />
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function DrawerField({
  field, value, onChange,
}: {
  field: CategoryField;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">
        {field.label}
        {field.required && <span className="text-red-500 ml-1">*</span>}
      </label>
      {field.type === 'select' ? (
        <select value={value} onChange={(e) => onChange(e.target.value)} className="input-field w-full !py-1.5 text-sm">
          <option value="">Select…</option>
          {field.options?.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      ) : (
        <input
          type={field.type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          className="input-field w-full !py-1.5 text-sm"
        />
      )}
    </div>
  );
}

function StatusChip({ status }: { status: RowStatus }) {
  switch (status.kind) {
    case 'ok':
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2 py-0.5 text-xs font-medium text-green-700">
          <CheckCircle2 className="w-3 h-3" /> Valid
        </span>
      );
    case 'created':
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-bv-50 px-2 py-0.5 text-xs font-medium text-bv-600">
          <CheckCircle2 className="w-3 h-3" /> Created
        </span>
      );
    case 'skipped':
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
          {status.reason}
        </span>
      );
    case 'error':
      return (
        <span
          className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-600"
          title={status.messages.join('\n')}
        >
          <AlertTriangle className="w-3 h-3" /> {status.messages[0]}
        </span>
      );
    default:
      return <span className="text-xs text-gray-300">—</span>;
  }
}

export default RapidGridPage;
