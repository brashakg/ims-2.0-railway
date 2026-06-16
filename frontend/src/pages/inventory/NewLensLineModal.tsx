// ============================================================================
// IMS 2.0 - New Lens Line modal (Power Grid cataloguing, P1)
// ============================================================================
// Creates a typed lens_catalog line from inside the Power Grid, so a fresh
// deploy can actually populate the grid (previously the grid had NO create
// path and pointed at a non-existent Settings screen). Writes via
// lensCatalogApi.create -> POST /lens-catalog (already validated server-side).
//
// Scope (P1): the EXISTING backend line model. Identity tuple + MRP are
// required; SPH/CYL/ADD ranges define the matrix the grid will draw. The
// optional per-power price-band table (mrp_table) and a made-to-order toggle
// are deliberate follow-ups (they need a backend field) -- this form sets a
// single MRP/cost, which the owner confirmed is the default mode.

import { useMemo, useState } from 'react';
import { X, Loader2, Plus } from 'lucide-react';
import {
  lensCatalogApi,
  type LensCatalogMetaOptions,
  type LensLine,
  type LensLineCreatePayload,
} from '../../services/api/lensCatalog';
import { useToast } from '../../context/ToastContext';

interface Props {
  meta: LensCatalogMetaOptions | null;
  onClose: () => void;
  onCreated: (line: LensLine) => void;
}

// Optical lenses bill at 5% GST (HSN 9001); seed those defaults.
const DEFAULT_GST = 5;
const DEFAULT_HSN = '9001';

interface RangeForm {
  min: string;
  max: string;
  step: string;
}

const DEFAULT_SPH: RangeForm = { min: '-6.00', max: '+6.00', step: '0.25' };
const DEFAULT_CYL: RangeForm = { min: '-2.00', max: '0.00', step: '0.25' };
const DEFAULT_ADD: RangeForm = { min: '+0.75', max: '+3.00', step: '0.25' };

function toRange(r: RangeForm) {
  const min = Number(r.min);
  const max = Number(r.max);
  const step = Number(r.step);
  if (!Number.isFinite(min) || !Number.isFinite(max) || !Number.isFinite(step) || step <= 0) {
    return null;
  }
  return { min, max, step };
}

export default function NewLensLineModal({ meta, onClose, onCreated }: Props) {
  const toast = useToast();

  const [brand, setBrand] = useState('');
  const [series, setSeries] = useState('');
  const [index, setIndex] = useState('');
  const [material, setMaterial] = useState('');
  const [lensType, setLensType] = useState('');
  const [coating, setCoating] = useState('');
  const [hasAdd, setHasAdd] = useState(false);
  const [sph, setSph] = useState<RangeForm>(DEFAULT_SPH);
  const [cyl, setCyl] = useState<RangeForm>(DEFAULT_CYL);
  const [add, setAdd] = useState<RangeForm>(DEFAULT_ADD);
  const [mrp, setMrp] = useState('');
  const [cost, setCost] = useState('');
  const [gst, setGst] = useState(String(DEFAULT_GST));
  const [hsn, setHsn] = useState(DEFAULT_HSN);
  const [notes, setNotes] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const brands = useMemo<string[]>(() => (meta?.enums?.brands || []) as string[], [meta]);
  const indexes = useMemo<number[]>(() => (meta?.enums?.indexes || []) as number[], [meta]);
  const materials = useMemo<string[]>(() => (meta?.enums?.materials || []) as string[], [meta]);
  const lensTypes = useMemo<string[]>(() => (meta?.enums?.lens_types || []) as string[], [meta]);
  const coatings = useMemo<string[]>(() => (meta?.enums?.coatings || []) as string[], [meta]);

  const validate = (): boolean => {
    const e: Record<string, string> = {};
    if (!brand.trim()) e.brand = 'Brand is required';
    if (!series.trim()) e.series = 'Series is required';
    if (!index.trim() || !Number.isFinite(Number(index))) e.index = 'Index is required';
    if (!material.trim()) e.material = 'Material is required';
    if (!lensType.trim()) e.lens_type = 'Lens type is required';
    if (!coating.trim()) e.coating = 'Coating is required';
    if (!mrp.trim() || !(Number(mrp) >= 0)) e.mrp = 'MRP is required';
    if (cost.trim() && !(Number(cost) >= 0)) e.cost = 'Cost must be a number';
    if (!toRange(sph)) e.sph = 'Valid SPH range required (min / max / step)';
    if (!toRange(cyl)) e.cyl = 'Valid CYL range required';
    if (hasAdd && !toRange(add)) e.add = 'Valid ADD range required';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const onSubmit = async () => {
    if (!validate()) return;
    setSubmitting(true);
    try {
      const payload: LensLineCreatePayload = {
        brand: brand.trim(),
        series: series.trim(),
        index: Number(index),
        material: material.trim(),
        lens_type: lensType.trim(),
        coating: coating.trim(),
        sph_range: toRange(sph),
        cyl_range: toRange(cyl),
        has_add: hasAdd,
        add_range: hasAdd ? toRange(add) : null,
        mrp: Number(mrp),
        cost_price: cost.trim() ? Number(cost) : null,
        gst_rate: gst.trim() ? Number(gst) : DEFAULT_GST,
        hsn_code: hsn.trim() || null,
        notes: notes.trim() || null,
      };
      const res = await lensCatalogApi.create(payload);
      toast.success('Lens line created. Now seed its power cells in the grid.');
      onCreated(res.lens_line);
    } catch (err: unknown) {
      // 409 = duplicate identity tuple; surface the server message.
      const anyErr = err as { response?: { data?: { detail?: string }; status?: number }; message?: string };
      const detail = anyErr?.response?.data?.detail;
      const msg =
        anyErr?.response?.status === 409
          ? 'A lens line with this exact brand/series/index/material/type/coating already exists.'
          : detail || anyErr?.message || 'Failed to create lens line';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const field = (label: string, key: string, node: React.ReactNode) => (
    <div>
      <label className="block text-xs font-medium text-ink-3 mb-1">{label}</label>
      {node}
      {errors[key] && <p className="text-err text-xs mt-1">{errors[key]}</p>}
    </div>
  );

  const rangeRow = (label: string, r: RangeForm, set: (r: RangeForm) => void, key: string) =>
    field(
      label,
      key,
      <div className="grid grid-cols-3 gap-2">
        {(['min', 'max', 'step'] as const).map((p) => (
          <input
            key={p}
            type="number"
            step="0.25"
            value={r[p]}
            onChange={(e) => set({ ...r, [p]: e.target.value })}
            placeholder={p}
            aria-label={`${label} ${p}`}
            className="border border-line rounded-lg px-2 py-1.5 text-sm w-full"
          />
        ))}
      </div>,
    );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between border-b border-line px-5 py-3 sticky top-0 bg-white z-10">
          <h2 className="text-base font-semibold text-ink flex items-center gap-2">
            <Plus className="w-4 h-4 text-bv" /> New lens line
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-ink-4 hover:text-ink">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <p className="text-xs text-ink-4">
            Define the lens design once. Its SPH×CYL ranges set the grid you'll then stock + reorder.
            Optical lenses default to 5% GST.
          </p>

          <div className="grid grid-cols-2 gap-3">
            {field('Brand *', 'brand', (
              <input list="ll-brands" value={brand} onChange={(e) => setBrand(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" placeholder="e.g. Essilor" />
            ))}
            {field('Series *', 'series', (
              <input value={series} onChange={(e) => setSeries(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" placeholder="e.g. Crizal" />
            ))}
            {field('Index *', 'index', (
              <input list="ll-indexes" type="number" step="0.01" value={index} onChange={(e) => setIndex(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" placeholder="e.g. 1.50" />
            ))}
            {field('Material *', 'material', (
              <input list="ll-materials" value={material} onChange={(e) => setMaterial(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" placeholder="e.g. CR-39" />
            ))}
            {field('Lens type *', 'lens_type', (
              <input list="ll-types" value={lensType} onChange={(e) => setLensType(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" placeholder="Single Vision / Progressive" />
            ))}
            {field('Coating *', 'coating', (
              <input list="ll-coatings" value={coating} onChange={(e) => setCoating(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" placeholder="e.g. ARC / Blue Cut" />
            ))}
          </div>

          {/* Datalists -- pick a known value or type a new one. */}
          <datalist id="ll-brands">{brands.map((b) => <option key={b} value={b} />)}</datalist>
          <datalist id="ll-indexes">{indexes.map((i) => <option key={i} value={i} />)}</datalist>
          <datalist id="ll-materials">{materials.map((m) => <option key={m} value={m} />)}</datalist>
          <datalist id="ll-types">{lensTypes.map((t) => <option key={t} value={t} />)}</datalist>
          <datalist id="ll-coatings">{coatings.map((c) => <option key={c} value={c} />)}</datalist>

          <div className="border-t border-line pt-3 space-y-3">
            <div className="grid grid-cols-1 gap-3">
              {rangeRow('SPH range (min / max / step)', sph, setSph, 'sph')}
              {rangeRow('CYL range (min / max / step)', cyl, setCyl, 'cyl')}
            </div>
            <label className="flex items-center gap-2 text-sm text-ink-3">
              <input type="checkbox" checked={hasAdd} onChange={(e) => setHasAdd(e.target.checked)} />
              This is a progressive / bifocal lens (has an ADD power)
            </label>
            {hasAdd && rangeRow('ADD range (min / max / step)', add, setAdd, 'add')}
          </div>

          <div className="border-t border-line pt-3 grid grid-cols-2 gap-3">
            {field('MRP (₹) *', 'mrp', (
              <input type="number" step="0.01" value={mrp} onChange={(e) => setMrp(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" placeholder="0.00" />
            ))}
            {field('Cost price (₹)', 'cost', (
              <input type="number" step="0.01" value={cost} onChange={(e) => setCost(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" placeholder="optional" />
            ))}
            {field('GST %', 'gst', (
              <input type="number" step="0.5" value={gst} onChange={(e) => setGst(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" />
            ))}
            {field('HSN code', 'hsn', (
              <input value={hsn} onChange={(e) => setHsn(e.target.value)}
                className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" />
            ))}
          </div>

          {field('Notes', 'notes', (
            <input value={notes} onChange={(e) => setNotes(e.target.value)}
              className="border border-line rounded-lg px-2 py-1.5 text-sm w-full" placeholder="optional" />
          ))}
          <p className="text-xs text-ink-4">
            Per-power price bands (high powers cost more) are coming as a follow-up; for now this is one MRP for the range.
          </p>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3 sticky bottom-0 bg-white">
          <button type="button" onClick={onClose} disabled={submitting}
            className="text-sm text-ink-3 hover:bg-bg-sunk rounded-lg px-3 py-1.5">
            Cancel
          </button>
          <button type="button" onClick={onSubmit} disabled={submitting}
            className="inline-flex items-center gap-1.5 text-sm bg-bv text-white rounded-lg px-4 py-1.5 disabled:opacity-60">
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Create lens line
          </button>
        </div>
      </div>
    </div>
  );
}
