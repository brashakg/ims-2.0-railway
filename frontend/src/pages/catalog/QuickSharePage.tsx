// ============================================================================
// IMS 2.0 - Quick Share: pick products -> share as PDF / save a temporary set
// ============================================================================
// A lightweight surface for staff helping a customer: search the catalogue,
// multi-select individual products into a selection tray, then either
//   (a) "Generate PDF" — feed the selection into the CataloguePdfModal, or
//   (b) "Save as temporary set" — a hand-picked, auto-expiring (<=7d) collection
//       that shows up below with a "Temporary — expires in N days" badge and can
//       itself be shared as a PDF.
// Server-side product search reuses GET /products (paginated).

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Search,
  Plus,
  X,
  Loader2,
  FileDown,
  Save,
  Trash2,
  Clock,
  PackageSearch,
} from 'lucide-react';
import { productApi } from '../../services/api/products';
import { resolveApiAssetUrl } from '../../services/api/client';
import {
  cataloguePdfApi,
  type TempCollection,
} from '../../services/api/cataloguePdf';
import CataloguePdfModal from '../../components/catalogue/CataloguePdfModal';
import { useToast } from '../../context/ToastContext';

interface PickerProduct {
  product_id: string;
  sku?: string | null;
  name?: string | null;
  title?: string | null;
  model?: string | null;
  brand?: string | null;
  category?: string | null;
  image_url?: string | null;
  images?: string[] | null;
  pricing?: { mrp?: number | null; offer_price?: number | null } | null;
  mrp?: number | null;
  [k: string]: unknown;
}

const SEARCH_LIMIT = 40;

function productName(p: PickerProduct): string {
  return (p.name || p.title || p.model || p.sku || 'Product') as string;
}

function productImage(p: PickerProduct): string | null {
  const raw =
    p.image_url ||
    (Array.isArray(p.images) && p.images.length ? p.images[0] : null) ||
    null;
  return raw ? resolveApiAssetUrl(raw) : null;
}

function daysUntil(iso: string | null): number {
  if (!iso) return 0;
  const ms = new Date(iso).getTime() - Date.now();
  return Math.max(0, Math.ceil(ms / (24 * 60 * 60 * 1000)));
}

export default function QuickSharePage() {
  const toast = useToast();

  const [query, setQuery] = useState('');
  const [results, setResults] = useState<PickerProduct[]>([]);
  const [searching, setSearching] = useState(false);

  // Selection tray keyed by product_id (preserves insertion order via array).
  const [selected, setSelected] = useState<PickerProduct[]>([]);
  const selectedIds = useMemo(
    () => new Set(selected.map((p) => p.product_id)),
    [selected],
  );

  const [pdfOpen, setPdfOpen] = useState(false);
  const [pdfTarget, setPdfTarget] = useState<
    { productIds?: string[]; collectionId?: string; title?: string } | null
  >(null);

  const [tempName, setTempName] = useState('');
  const [tempDays, setTempDays] = useState(7);
  const [savingTemp, setSavingTemp] = useState(false);

  const [temps, setTemps] = useState<TempCollection[]>([]);

  const loadTemps = useCallback(async () => {
    setTemps(await cataloguePdfApi.listTempCollections());
  }, []);

  useEffect(() => {
    void loadTemps();
  }, [loadTemps]);

  // Debounced search.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const handle = window.setTimeout(async () => {
      try {
        const data = await productApi.getProducts({ search: q, limit: SEARCH_LIMIT });
        const list = (data?.products ?? []) as PickerProduct[];
        setResults(list.filter((p) => p && p.product_id));
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => window.clearTimeout(handle);
  }, [query]);

  const addProduct = (p: PickerProduct) => {
    if (selectedIds.has(p.product_id)) return;
    setSelected((prev) => [...prev, p]);
  };
  const removeProduct = (id: string) => {
    setSelected((prev) => prev.filter((p) => p.product_id !== id));
  };
  const clearSelection = () => setSelected([]);

  const openPdfForSelection = () => {
    if (!selected.length) return;
    setPdfTarget({
      productIds: selected.map((p) => p.product_id),
      title: tempName.trim() || 'Product Selection',
    });
    setPdfOpen(true);
  };

  const openPdfForTemp = (t: TempCollection) => {
    setPdfTarget({ collectionId: t.collection_id, title: t.name });
    setPdfOpen(true);
  };

  const saveTemp = async () => {
    const name = tempName.trim();
    if (!name) {
      toast.error('Give the set a name.');
      return;
    }
    if (!selected.length) {
      toast.error('Add at least one product.');
      return;
    }
    setSavingTemp(true);
    try {
      await cataloguePdfApi.createTempCollection({
        name,
        productIds: selected.map((p) => p.product_id),
        validityDays: tempDays,
      });
      toast.success(`Saved "${name}" — expires in ${tempDays} day${tempDays === 1 ? '' : 's'}.`);
      setTempName('');
      clearSelection();
      void loadTemps();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save the set.');
    } finally {
      setSavingTemp(false);
    }
  };

  const deleteTemp = async (t: TempCollection) => {
    try {
      await cataloguePdfApi.deleteTempCollection(t.collection_id);
      setTemps((prev) => prev.filter((x) => x.collection_id !== t.collection_id));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not remove the set.');
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <PackageSearch size={20} /> Share Catalogue
        </h1>
        <p className="text-sm text-gray-500">
          Pick products and share them with a customer as a branded PDF — or save the
          selection as a temporary set that cleans itself up after up to a week.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Search + results */}
        <div className="lg:col-span-2 space-y-4">
          <div className="relative">
            <Search
              size={15}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
            />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name, brand, SKU or category…"
              className="input-field pl-9 w-full"
            />
          </div>

          {searching ? (
            <div className="card p-8 text-center text-gray-400">
              <Loader2 size={18} className="animate-spin mx-auto mb-2" />
              Searching…
            </div>
          ) : query.trim() && results.length === 0 ? (
            <div className="card p-8 text-center text-sm text-gray-500">
              No products match “{query.trim()}”.
            </div>
          ) : results.length > 0 ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {results.map((p) => {
                const img = productImage(p);
                const inTray = selectedIds.has(p.product_id);
                return (
                  <div key={p.product_id} className="card p-3 flex flex-col">
                    <div className="aspect-square mb-2 rounded bg-gray-50 overflow-hidden flex items-center justify-center">
                      {img ? (
                        // eslint-disable-next-line jsx-a11y/img-redundant-alt
                        <img
                          src={img}
                          alt={productName(p)}
                          className="max-h-full max-w-full object-contain"
                          loading="lazy"
                        />
                      ) : (
                        <span className="text-[11px] text-gray-400">No image</span>
                      )}
                    </div>
                    {p.brand && (
                      <span className="text-[11px] font-medium text-bv-red-600 truncate">
                        {p.brand}
                      </span>
                    )}
                    <span className="text-xs text-gray-900 line-clamp-2 leading-snug mb-2">
                      {productName(p)}
                    </span>
                    <button
                      type="button"
                      onClick={() => addProduct(p)}
                      disabled={inTray}
                      className={`mt-auto inline-flex items-center justify-center gap-1 rounded px-2 py-1 text-xs font-medium ${
                        inTray
                          ? 'bg-gray-100 text-gray-400'
                          : 'bg-bv-red-600 text-white hover:bg-bv-red-700'
                      }`}
                    >
                      {inTray ? 'Added' : (<><Plus size={13} /> Add</>)}
                    </button>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="card p-8 text-center text-sm text-gray-400">
              Start typing to search the catalogue.
            </div>
          )}
        </div>

        {/* Selection tray + actions */}
        <div className="space-y-4">
          <div className="card p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-900">
                Selection ({selected.length})
              </h2>
              {selected.length > 0 && (
                <button
                  type="button"
                  onClick={clearSelection}
                  className="text-xs text-gray-500 hover:text-gray-800"
                >
                  Clear
                </button>
              )}
            </div>

            {selected.length === 0 ? (
              <p className="text-xs text-gray-400 py-4 text-center">
                No products yet. Add some from the search results.
              </p>
            ) : (
              <ul className="space-y-2 max-h-64 overflow-y-auto mb-3">
                {selected.map((p) => (
                  <li
                    key={p.product_id}
                    className="flex items-center gap-2 text-xs"
                  >
                    <span className="flex-1 truncate">
                      {p.brand ? `${p.brand} · ` : ''}
                      {productName(p)}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeProduct(p.product_id)}
                      className="text-gray-400 hover:text-bv-red-600"
                      aria-label="Remove"
                    >
                      <X size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <button
              type="button"
              onClick={openPdfForSelection}
              disabled={!selected.length}
              className="btn-primary w-full inline-flex items-center justify-center gap-2 text-sm disabled:opacity-50"
            >
              <FileDown size={16} /> Generate PDF
            </button>
          </div>

          {/* Save as temporary set */}
          <div className="card p-4 space-y-3">
            <h2 className="text-sm font-semibold text-gray-900">Save as temporary set</h2>
            <input
              type="text"
              value={tempName}
              onChange={(e) => setTempName(e.target.value)}
              placeholder="Set name (e.g. Mr Sharma — sunglasses)"
              className="input-field w-full text-sm"
            />
            <label className="flex items-center justify-between text-xs text-gray-600">
              <span>Expires in</span>
              <select
                value={tempDays}
                onChange={(e) => setTempDays(Number(e.target.value))}
                className="input-field text-sm py-1 w-28"
              >
                {[1, 2, 3, 4, 5, 6, 7].map((d) => (
                  <option key={d} value={d}>
                    {d} day{d === 1 ? '' : 's'}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={saveTemp}
              disabled={savingTemp || !selected.length || !tempName.trim()}
              className="btn-secondary w-full inline-flex items-center justify-center gap-2 text-sm disabled:opacity-50"
            >
              {savingTemp ? (
                <Loader2 size={15} className="animate-spin" />
              ) : (
                <Save size={15} />
              )}
              Save temporary set
            </button>
            <p className="text-[11px] text-gray-400">
              Temporary sets auto-delete after their validity (max 7 days) and are
              never published online.
            </p>
          </div>
        </div>
      </div>

      {/* Temporary / shared sets */}
      {temps.length > 0 && (
        <div className="mt-8">
          <h2 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
            <Clock size={16} /> Temporary / Shared sets
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {temps.map((t) => {
              const d = daysUntil(t.expires_at);
              return (
                <div key={t.collection_id} className="card p-4">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <span className="text-sm font-medium text-gray-900 line-clamp-2">
                      {t.name}
                    </span>
                    <button
                      type="button"
                      onClick={() => deleteTemp(t)}
                      className="shrink-0 text-gray-400 hover:text-bv-red-600"
                      aria-label="Delete set"
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                  <div className="flex items-center gap-2 mb-3">
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                      <Clock size={10} /> Temporary — expires in {d} day{d === 1 ? '' : 's'}
                    </span>
                    <span className="text-[11px] text-gray-400">
                      {t.products_count} item{t.products_count === 1 ? '' : 's'}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => openPdfForTemp(t)}
                    className="btn-secondary w-full inline-flex items-center justify-center gap-2 text-sm"
                  >
                    <FileDown size={15} /> Share as PDF
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <CataloguePdfModal
        open={pdfOpen}
        onClose={() => setPdfOpen(false)}
        collectionId={pdfTarget?.collectionId}
        productIds={pdfTarget?.productIds}
        title={pdfTarget?.title}
      />
    </div>
  );
}
