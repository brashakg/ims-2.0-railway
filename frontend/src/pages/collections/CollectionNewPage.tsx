// ============================================================================
// IMS 2.0 - New Collection (Phase 1) — governed chip builder
// ============================================================================
// ONE screen, no wizard: compose a SMART collection from GOVERNED chips —
// Category (registry), Brand (Brand Master), any dictionary-governed attribute
// of that category — plus free tag chips and a price band. A sticky live
// preview (debounced POST /collections/preview) shows match count, on-hand
// units and sample products while you compose. Target: "Ray-Ban Black
// Polarized Women" in ~30 seconds.
//
// Rule compilation contract (Track 2): each chip group = ONE rule; multiple
// values on the same field -> {relation:'IN', value:[...]}; a single value ->
// EQUALS; groups AND together (disjunctive:false). Price bounds compile to
// GREATER_THAN / LESS_THAN on the ecom rule field 'price'.
//
// Save goes through the EXISTING admin CRUD (POST /online-store/collections)
// as SMART + published:false ALWAYS — publishing online stays in the
// Online Store editor.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Layers,
  Loader2,
  Save,
  Sparkles,
  Tag,
  X,
  IndianRupee,
  ImageOff,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useDebounce } from '../../hooks/useDebounce';
import { productApi, type CategoryRegistryEntry } from '../../services/api/products';
import { resolveApiAssetUrl } from '../../services/api/client';
import {
  collectionsInsightsApi,
  type InsightRule,
  type CollectionPreview,
} from '../../services/api/collectionsInsights';
import { rupee, fmtInt } from './collectionsShared';

// Fields whose vocabulary is owned by a dedicated chip group (or that make no
// sense as attribute chips) — excluded from the generic attribute groups.
const EXCLUDED_ATTR_FIELDS = new Set(['brand_name', 'sub_brand', 'model_no', 'model']);

interface BrandOption {
  name: string;
  subbrands: string[];
  tier?: string;
}

function errMessage(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } } | undefined)?.response
    ?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return e instanceof Error && e.message ? e.message : 'Request failed';
}

/** Toggleable governed chip. */
function Chip({
  label,
  selected,
  onClick,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={selected ? 'ims-chip ims-chip--on' : 'ims-chip'}
    >
      {label}
    </button>
  );
}

/** Sample product tile with an image fallback box. */
function SampleTile({
  sku,
  mrp,
  image,
  title,
}: {
  sku: string;
  mrp?: number | null;
  image?: string | null;
  title?: string | null;
}) {
  const [broken, setBroken] = useState(false);
  const src = image ? resolveApiAssetUrl(image) : null;
  return (
    <div className="border border-gray-100 rounded-lg p-2 bg-white" title={title || sku}>
      {src && !broken ? (
        <img
          src={src}
          alt={title || sku}
          referrerPolicy="no-referrer"
          onError={() => setBroken(true)}
          className="w-full h-16 object-contain mb-1.5"
        />
      ) : (
        <div className="w-full h-16 mb-1.5 rounded bg-gray-50 flex items-center justify-center text-gray-300">
          <ImageOff size={18} />
        </div>
      )}
      <div className="text-[11px] text-gray-700 truncate">{sku}</div>
      <div className="text-[11px] text-gray-500">{rupee(mrp)}</div>
    </div>
  );
}

export default function CollectionNewPage() {
  const toast = useToast();
  const navigate = useNavigate();

  // ---- Governed vocabulary --------------------------------------------------
  const [registry, setRegistry] = useState<CategoryRegistryEntry[]>([]);
  const [loadingRegistry, setLoadingRegistry] = useState(true);
  const [brands, setBrands] = useState<BrandOption[]>([]);
  const [loadingBrands, setLoadingBrands] = useState(false);

  // ---- Chip selections ------------------------------------------------------
  const [category, setCategory] = useState<string>(''); // canonical code, e.g. 'SUNGLASS'
  const [selectedBrands, setSelectedBrands] = useState<string[]>([]);
  const [attrSelections, setAttrSelections] = useState<Record<string, string[]>>({});
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');
  const [priceMin, setPriceMin] = useState('');
  const [priceMax, setPriceMax] = useState('');

  // ---- Title (auto-composed, editable) --------------------------------------
  const [title, setTitle] = useState('');
  const [titleTouched, setTitleTouched] = useState(false);

  // ---- Preview / save -------------------------------------------------------
  const [preview, setPreview] = useState<CollectionPreview | null>(null);
  const [previewState, setPreviewState] = useState<'idle' | 'loading' | 'ready' | 'unavailable'>(
    'idle',
  );
  const [saving, setSaving] = useState(false);
  const previewSeq = useRef(0);

  useEffect(() => {
    let alive = true;
    productApi
      .getCategoryRegistry()
      .then((res) => {
        if (!alive) return;
        setRegistry(res?.categories ?? []);
      })
      .catch(() => {
        if (alive) toast.error('Could not load the category registry');
      })
      .finally(() => {
        if (alive) setLoadingRegistry(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeEntry = useMemo(
    () => registry.find((c) => c.code === category) || null,
    [registry, category],
  );

  // Dictionary-governed attribute chip groups for the picked category.
  const attrFields = useMemo(
    () =>
      (activeEntry?.fields ?? []).filter(
        (f) => !EXCLUDED_ATTR_FIELDS.has(f.name) && (f.options?.length ?? 0) > 0,
      ),
    [activeEntry],
  );

  // Brand vocabulary follows the category (Brand Master applicability).
  useEffect(() => {
    if (!category) {
      setBrands([]);
      return;
    }
    let alive = true;
    setLoadingBrands(true);
    productApi
      .getBrandOptions(category)
      .then((res) => {
        if (alive) setBrands(res?.brands ?? []);
      })
      .catch(() => {
        if (alive) setBrands([]);
      })
      .finally(() => {
        if (alive) setLoadingBrands(false);
      });
    return () => {
      alive = false;
    };
  }, [category]);

  const pickCategory = (code: string) => {
    setCategory((prev) => (prev === code ? '' : code));
    // New category = new governed vocabulary — clear brand/attribute chips
    // (tags + price band survive, they're category-agnostic).
    setSelectedBrands([]);
    setAttrSelections({});
  };

  const toggleBrand = (name: string) =>
    setSelectedBrands((prev) =>
      prev.includes(name) ? prev.filter((b) => b !== name) : [...prev, name],
    );

  const toggleAttr = (field: string, value: string) =>
    setAttrSelections((prev) => {
      const cur = prev[field] || [];
      const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
      const out = { ...prev, [field]: next };
      if (next.length === 0) delete out[field];
      return out;
    });

  const addTag = () => {
    const t = tagInput.trim();
    if (!t) return;
    setTags((prev) => (prev.includes(t) ? prev : [...prev, t]));
    setTagInput('');
  };

  // ---- Rule compilation (the Track 2 contract) ------------------------------
  const rules = useMemo<InsightRule[]>(() => {
    const out: InsightRule[] = [];
    const one = (field: string, values: string[]) => {
      if (values.length === 1) out.push({ field, relation: 'EQUALS', value: values[0] });
      else if (values.length > 1) out.push({ field, relation: 'IN', value: [...values] });
    };
    if (category) out.push({ field: 'category', relation: 'EQUALS', value: category });
    one('brand', selectedBrands);
    for (const f of attrFields) {
      const vals = attrSelections[f.name];
      if (vals?.length) one(f.name, vals);
    }
    one('tag', tags);
    const min = Number(priceMin);
    const max = Number(priceMax);
    if (priceMin.trim() !== '' && Number.isFinite(min)) {
      out.push({ field: 'price', relation: 'GREATER_THAN', value: min });
    }
    if (priceMax.trim() !== '' && Number.isFinite(max)) {
      out.push({ field: 'price', relation: 'LESS_THAN', value: max });
    }
    return out;
  }, [category, selectedBrands, attrFields, attrSelections, tags, priceMin, priceMax]);

  // ---- Auto-composed title ("Ray-Ban Black Polarized Women") ----------------
  const autoTitle = useMemo(() => {
    const parts: string[] = [...selectedBrands];
    for (const f of attrFields) parts.push(...(attrSelections[f.name] || []));
    parts.push(...tags);
    let t = parts.join(' ');
    if (!t && activeEntry) t = activeEntry.name;
    const min = priceMin.trim();
    const max = priceMax.trim();
    if (min && max) t = `${t} ₹${min}–₹${max}`.trim();
    else if (max) t = `${t} under ₹${max}`.trim();
    else if (min) t = `${t} over ₹${min}`.trim();
    return t.trim();
  }, [selectedBrands, attrFields, attrSelections, tags, activeEntry, priceMin, priceMax]);

  useEffect(() => {
    if (!titleTouched) setTitle(autoTitle);
  }, [autoTitle, titleTouched]);

  // ---- Debounced live preview ------------------------------------------------
  const rulesKey = useDebounce(JSON.stringify(rules), 450);
  useEffect(() => {
    const parsed = JSON.parse(rulesKey) as InsightRule[];
    if (!parsed.length) {
      setPreview(null);
      setPreviewState('idle');
      return;
    }
    const seq = ++previewSeq.current;
    setPreviewState('loading');
    collectionsInsightsApi.preview(parsed, false).then((res) => {
      if (seq !== previewSeq.current) return; // a newer request superseded us
      if (res === null) {
        setPreview(null);
        setPreviewState('unavailable');
      } else {
        setPreview(res);
        setPreviewState('ready');
      }
    });
  }, [rulesKey]);

  // ---- Save -------------------------------------------------------------------
  const onSave = useCallback(async () => {
    const t = title.trim();
    if (!t) {
      toast.error('Give the collection a title');
      return;
    }
    if (rules.length === 0) {
      toast.error('Pick at least one chip — the collection needs a rule');
      return;
    }
    setSaving(true);
    try {
      const { id } = await collectionsInsightsApi.createSmart({
        title: t,
        rules,
        disjunctive: false,
      });
      toast.success('Collection created (internal — publish online from the Online Store editor)');
      navigate(`/collections/${encodeURIComponent(id)}`);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  }, [title, rules, toast, navigate]);

  const sample = (preview?.sample ?? []).slice(0, 12);

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/collections"
          className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-1"
        >
          <ArrowLeft size={14} /> Collections
        </Link>
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Layers size={20} /> New collection
        </h1>
        <p className="text-sm text-gray-500">
          Tap chips to compose a rule-based collection — the preview updates live.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_24rem] gap-6 items-start">
        {/* ------------------------------ Left: chip composer ------------------ */}
        <div className="space-y-4 min-w-0">
          {/* Category */}
          <section className="card p-4">
            <h2 className="text-sm font-medium text-gray-700 mb-3">Category</h2>
            {loadingRegistry ? (
              <div className="text-gray-400 text-sm flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" /> Loading…
              </div>
            ) : registry.length === 0 ? (
              <p className="text-sm text-gray-400">Category registry unavailable.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {registry.map((c) => (
                  <Chip
                    key={c.code}
                    label={c.name}
                    selected={category === c.code}
                    onClick={() => pickCategory(c.code)}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Brand */}
          <section className="card p-4">
            <h2 className="text-sm font-medium text-gray-700 mb-3">
              Brand{' '}
              <span className="font-normal text-gray-400">(from Brand Master — pick any)</span>
            </h2>
            {!category ? (
              <p className="text-sm text-gray-400">Pick a category first.</p>
            ) : loadingBrands ? (
              <div className="text-gray-400 text-sm flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" /> Loading brands…
              </div>
            ) : brands.length === 0 ? (
              <p className="text-sm text-gray-400">No brands configured for this category.</p>
            ) : (
              <div className="flex flex-wrap gap-2 max-h-44 overflow-auto pr-1">
                {brands.map((b) => (
                  <Chip
                    key={b.name}
                    label={b.name}
                    selected={selectedBrands.includes(b.name)}
                    onClick={() => toggleBrand(b.name)}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Governed attributes (Catalog Dictionary) */}
          {category && attrFields.length > 0 && (
            <section className="card p-4">
              <h2 className="text-sm font-medium text-gray-700 mb-1">
                Attributes{' '}
                <span className="font-normal text-gray-400">(Catalog Dictionary values)</span>
              </h2>
              <div className="divide-y divide-gray-50">
                {attrFields.map((f) => (
                  <div key={f.name} className="py-3">
                    <div className="text-xs font-medium text-gray-500 mb-2">{f.label}</div>
                    <div className="flex flex-wrap gap-2 max-h-36 overflow-auto pr-1">
                      {(f.options || []).map((opt) => (
                        <Chip
                          key={opt}
                          label={opt}
                          selected={(attrSelections[f.name] || []).includes(opt)}
                          onClick={() => toggleAttr(f.name, opt)}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Free chips: tags */}
          <section className="card p-4">
            <h2 className="text-sm font-medium text-gray-700 mb-3">
              Tags <span className="font-normal text-gray-400">(free text)</span>
            </h2>
            <div className="flex items-center gap-2 mb-2">
              <div className="relative flex-1 max-w-xs">
                <Tag size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addTag();
                    }
                  }}
                  placeholder="e.g. bestseller"
                  className="input-field pl-9 w-full"
                />
              </div>
              <button type="button" onClick={addTag} className="btn-secondary text-sm">
                Add
              </button>
            </div>
            {tags.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {tags.map((t) => (
                  <span
                    key={t}
                    className="inline-flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-full bg-gray-100 text-gray-800 text-sm"
                  >
                    {t}
                    <button
                      type="button"
                      onClick={() => setTags((prev) => prev.filter((x) => x !== t))}
                      className="p-0.5 rounded-full hover:bg-gray-200 text-gray-400 hover:text-red-600"
                      aria-label={`Remove tag ${t}`}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </section>

          {/* Free chips: price band */}
          <section className="card p-4">
            <h2 className="text-sm font-medium text-gray-700 mb-3">
              Price band <span className="font-normal text-gray-400">(MRP, ₹)</span>
            </h2>
            <div className="flex items-center gap-3">
              <div className="relative w-36">
                <IndianRupee
                  size={13}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
                />
                <input
                  type="number"
                  min="0"
                  value={priceMin}
                  onChange={(e) => setPriceMin(e.target.value)}
                  placeholder="Min"
                  className="input-field pl-8 w-full"
                />
              </div>
              <span className="text-gray-400 text-sm">to</span>
              <div className="relative w-36">
                <IndianRupee
                  size={13}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
                />
                <input
                  type="number"
                  min="0"
                  value={priceMax}
                  onChange={(e) => setPriceMax(e.target.value)}
                  placeholder="Max"
                  className="input-field pl-8 w-full"
                />
              </div>
            </div>
          </section>
        </div>

        {/* ------------------------------ Right: sticky title + preview -------- */}
        <div className="lg:sticky lg:top-4 space-y-4">
          <section className="card p-4">
            <label htmlFor="collection-title" className="block text-sm font-medium text-gray-700 mb-1.5">
              Title{' '}
              <span className="font-normal text-gray-400">
                {titleTouched ? '(edited)' : '(auto-composed from chips)'}
              </span>
            </label>
            <input
              id="collection-title"
              type="text"
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                setTitleTouched(true);
              }}
              placeholder="Ray-Ban Black Polarized Women"
              className="input-field w-full mb-3"
            />
            <button
              type="button"
              onClick={onSave}
              disabled={saving || rules.length === 0 || !title.trim()}
              className="btn-primary w-full inline-flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
              Create collection
            </button>
            <p className="text-[11px] text-gray-400 mt-2">
              Created as <span className="font-medium">Internal</span> (not published online).
              Rules: {rules.length} · all conditions must match.
            </p>
          </section>

          <section className="card p-4">
            <h2 className="text-sm font-medium text-gray-700 mb-3 flex items-center gap-2">
              <Sparkles size={14} /> Live preview
            </h2>
            {previewState === 'idle' && (
              <p className="text-sm text-gray-400">Pick chips to see matching products.</p>
            )}
            {previewState === 'loading' && (
              <div className="text-gray-400 text-sm flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" /> Matching…
              </div>
            )}
            {previewState === 'unavailable' && (
              <p className="text-sm text-amber-600">
                Preview isn't available yet (the insights service may not be deployed). You can
                still create the collection.
              </p>
            )}
            {previewState === 'ready' && preview && (
              <>
                <div className="grid grid-cols-2 gap-3 mb-4">
                  <div className="rounded-lg bg-gray-50 p-3">
                    <div className="text-[11px] uppercase tracking-wide text-gray-400">
                      Products
                    </div>
                    <div className="text-lg font-semibold text-gray-900">
                      {fmtInt(preview.match_count)}
                    </div>
                  </div>
                  <div className="rounded-lg bg-gray-50 p-3">
                    <div className="text-[11px] uppercase tracking-wide text-gray-400">
                      Units on hand
                    </div>
                    <div className="text-lg font-semibold text-gray-900">
                      {fmtInt(preview.units_on_hand)}
                    </div>
                  </div>
                </div>
                {sample.length === 0 ? (
                  <p className="text-sm text-gray-400">No matching products.</p>
                ) : (
                  <div className="grid grid-cols-3 gap-2">
                    {sample.map((s) => (
                      <SampleTile
                        key={s.sku}
                        sku={s.sku}
                        mrp={s.mrp}
                        image={s.image}
                        title={s.title}
                      />
                    ))}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
