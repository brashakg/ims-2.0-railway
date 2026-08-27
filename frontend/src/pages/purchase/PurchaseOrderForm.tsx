// ============================================================================
// IMS 2.0 - Create Purchase Order (manual full form)
// ============================================================================
// Thin wrapper: page chrome (overlay + header + close) around the shared
// <PurchaseOrderComposer/> so the manual form and the Buy Desk quick draft use
// ONE PO body with identical fields, labels, cost pre-fill and validation
// (owner ruling, procurement Phase 2C). What's local to this door: the
// searchable product picker per line (ProductSearchSelect) and the ability to
// add/remove lines -- both fed to the composer via props.

import { useState, useEffect, useRef } from 'react';
import { FileText, X as XIcon, Loader2, Search } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { vendorsApi, productApi } from '../../services/api';
import { storeApi } from '../../services/api/stores';
import { isInterStateSupply } from '../../constants/gst';
import { useGstStateCodes } from '../../hooks/useGstStateCodes';
import { PurchaseOrderComposer } from '../../components/purchase/PurchaseOrderComposer';
import type {
  ComposerVendorOption,
  ComposerNewProduct,
} from '../../components/purchase/PurchaseOrderComposer';
import { CATEGORIES } from '../catalog/productAddShared';
import type { Supplier, PurchaseOrder, POItem } from './purchaseTypes';

interface PickedProduct {
  productId: string;
  productName: string;
  sku: string;
  costPrice: number;
  gstRate: number | null;
  hsn: string | null;
  /** The one-line identity a buyer scans: colour, size, MRP -- what actually
   *  separates two frames of the same model. */
  detail: string;
  hasCost: boolean;
}

// Shape we read off a /products row -- the endpoint returns full docs. The
// identity fields live either flat (legacy rows) or under `attributes`
// (canonical), so both are read.
interface ProductHit {
  product_id?: string;
  productId?: string;
  sku?: string;
  name?: string;
  brand?: string;
  model?: string;
  color?: string;
  colour?: string;
  size?: string;
  category?: string;
  mrp?: number;
  cost_price?: number;
  hsn_code?: string | null;
  gst_rate?: number | null;
  catalog_status?: string;
  /** What the catalogue still lacks on this product, straight off the product
   *  spine. A missing cost is the ONE gap that does not stop the order going
   *  out (the rate typed here becomes the cost); anything else does. */
  done_gaps?: string[];
  attributes?: Record<string, unknown>;
}

const GAP_LABELS: Record<string, string> = {
  category: 'category',
  brand_name: 'brand',
  model_no: 'model number',
  colour_code: 'colour code',
  size: 'size',
  mrp: 'MRP',
  offer_price: 'selling price',
  MRP_BELOW_OFFER: 'MRP below the selling price',
  hsn_code: 'HSN number',
  gst_rate: 'GST rate',
};

/** The gaps that will actually stop this PO being SENT. A missing cost is
 *  excluded on purpose (owner ruling 2026-08-26): the rate typed on this order
 *  becomes the cost, so it can no longer be a blocker. */
export function blockingGaps(hit: ProductHit): string[] {
  return (hit.done_gaps || [])
    .filter((g) => g !== 'cost_price')
    .map((g) => GAP_LABELS[g] || g.replace(/_/g, ' '));
}

function attr(hit: ProductHit, ...keys: string[]): string {
  const bag = (hit.attributes || {}) as Record<string, unknown>;
  for (const k of keys) {
    const flat = (hit as unknown as Record<string, unknown>)[k];
    const val = bag[k] ?? flat;
    if (val !== undefined && val !== null && String(val).trim()) return String(val).trim();
  }
  return '';
}

/** Brand + model is the headline; a bare product `name` is the fallback for a
 *  legacy row that has no model. */
export function productHeadline(hit: ProductHit): string {
  const brand = attr(hit, 'brand', 'brand_name');
  const model = attr(hit, 'model', 'model_no', 'model_name');
  const headline = [brand, model].filter(Boolean).join(' ');
  return headline || (hit.name || '').trim() || (hit.sku || '');
}

/** Colour, size and MRP -- the fields that tell two near-identical frames
 *  apart on a 40-line purchase order. */
export function productDetail(hit: ProductHit): string {
  const bits: string[] = [];
  const colour = attr(hit, 'color', 'colour', 'colour_code', 'frame_color');
  const size = attr(hit, 'size', 'lens_size');
  if (colour) bits.push(colour);
  if (size) bits.push(size);
  if (Number(hit.mrp) > 0) bits.push(`MRP ${'₹'}${Number(hit.mrp).toLocaleString('en-IN')}`);
  return bits.join(' · ');
}

function hitToPicked(hit: ProductHit): PickedProduct {
  const rate = hit.gst_rate;
  return {
    productId: String(hit.product_id || hit.productId || ''),
    productName: productHeadline(hit),
    sku: hit.sku || '',
    costPrice: Number(hit.cost_price) > 0 ? Number(hit.cost_price) : 0,
    gstRate: typeof rate === 'number' && rate >= 0 ? rate : null,
    hsn: (hit.hsn_code || '').trim() || null,
    detail: productDetail(hit),
    hasCost: Number(hit.cost_price) > 0,
  };
}

// ---------------------------------------------------------------------------
// Owner ruling 13 -- "not in the catalogue?". The buyer orders off the vendor's
// list before the item exists in IMS, so these are the fields he named: brand,
// model no, colour code, size and MRP. The line's unit cost is the cost price.
// There is deliberately no SELLING price here: the server creates the product
// provisional (inactive, no offer price) so it cannot be sold until a
// cataloguer finishes it.
// ---------------------------------------------------------------------------
const blankNewProduct = (): ComposerNewProduct => ({
  category: 'FR',
  brand: '',
  model: '',
  colour: '',
  size: '',
  mrp: 0,
});

function NewProductFields({
  value,
  onChange,
  onCancel,
}: {
  value: ComposerNewProduct;
  onChange: (np: ComposerNewProduct) => void;
  onCancel: () => void;
}) {
  const set = (patch: Partial<ComposerNewProduct>) => onChange({ ...value, ...patch });
  return (
    <div className="p-2 bg-amber-50 border border-amber-200 rounded-lg space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-amber-800">New item — not catalogued yet</span>
        <button
          type="button"
          onClick={onCancel}
          className="p-1 text-amber-700 hover:text-red-600"
          title="Search the catalogue instead"
        >
          <XIcon className="w-3.5 h-3.5" />
        </button>
      </div>
      <select
        value={value.category}
        onChange={(e) => set({ category: e.target.value })}
        className="input-field text-sm"
        aria-label="New item category"
      >
        {CATEGORIES.map((c) => (
          <option key={c.code} value={c.code}>
            {c.name}
          </option>
        ))}
      </select>
      <div className="grid grid-cols-2 gap-2">
        <input
          type="text"
          value={value.brand}
          onChange={(e) => set({ brand: e.target.value })}
          placeholder="Brand *"
          aria-label="New item brand"
          className="input-field text-sm"
        />
        <input
          type="text"
          value={value.model}
          onChange={(e) => set({ model: e.target.value })}
          placeholder="Model no *"
          aria-label="New item model number"
          className="input-field text-sm"
        />
        <input
          type="text"
          value={value.colour}
          onChange={(e) => set({ colour: e.target.value })}
          placeholder="Colour code"
          aria-label="New item colour code"
          className="input-field text-sm"
        />
        <input
          type="text"
          value={value.size}
          onChange={(e) => set({ size: e.target.value })}
          placeholder="Size"
          aria-label="New item size"
          className="input-field text-sm"
        />
      </div>
      <input
        type="number"
        min="0"
        step="0.01"
        value={value.mrp || ''}
        onChange={(e) => set({ mrp: parseFloat(e.target.value) || 0 })}
        placeholder="MRP *"
        aria-label="New item MRP"
        className="input-field text-sm"
      />
      <p className="text-[11px] text-amber-700">
        It will be ordered now and finished in the catalogue later. It cannot be sold until then.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Product search-select. Debounced typeahead against GET /products?search=.
// Once a product is picked it shows as a locked chip (real product_id behind
// it); "change" clears the pick and re-opens the search. This is what lets the
// PO catalog gate be turned ON without breaking the manual Create-PO flow.
// ---------------------------------------------------------------------------
function ProductSearchSelect({
  picked,
  newProduct,
  onPick,
  onClear,
  onSetNew,
}: {
  picked: { productId: string; productName: string; sku: string; detail?: string };
  newProduct?: ComposerNewProduct | null;
  onPick: (p: PickedProduct) => void;
  onClear: () => void;
  onSetNew: (np: ComposerNewProduct | null) => void;
}) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<ProductHit[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  // Debounced search; skip while a product is already picked.
  useEffect(() => {
    if (picked.productId) return;
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      setOpen(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const data = await productApi.getProducts({ search: q });
        if (cancelled) return;
        const rows: ProductHit[] = (data?.products || []).slice(0, 20);
        setResults(rows);
        setOpen(true);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query, picked.productId]);

  // Close dropdown on outside click.
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  if (newProduct) {
    return (
      <NewProductFields value={newProduct} onChange={onSetNew} onCancel={() => onSetNew(null)} />
    );
  }

  if (picked.productId) {
    return (
      <div className="flex items-start gap-2 px-3 py-2 bg-white border border-gray-300 rounded-lg">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-gray-900 truncate">{picked.productName}</p>
          <p className="text-xs text-gray-500 truncate">
            {picked.sku}
            {picked.detail ? ` · ${picked.detail}` : ''}
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            onClear();
            setQuery('');
            setResults([]);
          }}
          className="ml-auto p-1 text-gray-400 hover:text-red-500 shrink-0"
          title="Change product"
        >
          <XIcon className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  }

  return (
    <div className="relative" ref={boxRef}>
      <div className="relative">
        <Search className="w-4 h-4 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
        <input
          type="text"
          placeholder="Search catalogued product..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          className="input-field text-sm pl-8"
        />
        {loading && (
          <Loader2 className="w-4 h-4 text-gray-400 animate-spin absolute right-2.5 top-1/2 -translate-y-1/2" />
        )}
      </div>
      <button
        type="button"
        onClick={() => {
          setOpen(false);
          onSetNew({ ...blankNewProduct(), brand: query.trim() });
        }}
        className="mt-1 text-xs text-blue-600 hover:text-blue-700 font-medium"
      >
        Not in the catalogue? Enter the item&apos;s details
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-full max-h-80 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg">
          {results.length === 0 ? (
            <div className="px-3 py-2 text-sm text-gray-500">
              {query.trim().length < 2 ? 'Type to search...' : 'No catalogued products match.'}
            </div>
          ) : (
            results.map((hit, i) => {
              const p = hitToPicked(hit);
              if (!p.productId) return null;
              return (
                <button
                  type="button"
                  key={`${p.productId}-${i}`}
                  onClick={() => {
                    onPick(p);
                    setOpen(false);
                  }}
                  className="w-full text-left px-3 py-2.5 hover:bg-gray-50 border-b border-gray-100 last:border-0"
                >
                  {/* Line 1 -- WHICH product: brand + model, given the room to
                      be read in full. */}
                  <div className="text-sm font-semibold text-gray-900 truncate">{p.productName}</div>
                  {/* Line 2 -- WHICH ONE of them: colour, size, MRP. This is
                      what separates two frames of the same model. */}
                  {p.detail ? (
                    <div className="text-xs text-gray-600 truncate">{p.detail}</div>
                  ) : null}
                  {/* Line 3 -- what the buyer needs to price it. */}
                  <div className="text-xs text-gray-500 flex flex-wrap items-center gap-x-2 gap-y-1 mt-0.5">
                    <span className="font-mono">{p.sku}</span>
                    {hit.category ? <span>{String(hit.category).replace(/_/g, ' ').toLowerCase()}</span> : null}
                    {/* The product's own catalogued rate -- which the
                        cataloguing door derived from this very HSN, server-side,
                        so it is the rate the purchase order will be stored at.
                        This list keeps no HSN -> rate table of its own. A
                        missing HSN is still flagged on its own, because a
                        product can carry a rate and have no HSN, which a GST
                        purchase document may not. */}
                    {p.gstRate !== null ? (
                      <span>
                        {p.gstRate}% GST{p.hsn ? ` · HSN ${p.hsn}` : ''}
                      </span>
                    ) : (
                      <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-medium">
                        GST rate unknown
                      </span>
                    )}
                    {!p.hsn ? (
                      <span
                        className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-medium"
                        title="A purchase order must carry an HSN for every line. Add it on the product."
                      >
                        No HSN
                      </span>
                    ) : null}
                    {p.hasCost ? (
                      <span>
                        {'₹'}
                        {p.costPrice.toLocaleString('en-IN')} cost
                      </span>
                    ) : (
                      <span
                        className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-medium"
                        title="No cost recorded yet. The rate you type on this order becomes the cost, and the order can still be sent."
                      >
                        No cost yet
                      </span>
                    )}
                    {/* The honest blocker. The old chip said EVERY draft
                        product stopped the PO being sent -- which made all 68
                        live products look blocked when only their cost was
                        missing, and a missing cost has never stopped a send. */}
                    {blockingGaps(hit).length > 0 && (
                      <span
                        className="px-1.5 py-0.5 rounded bg-red-100 text-red-700 text-[10px] font-medium"
                        title={`This order cannot be sent until the product has its ${blockingGaps(hit).join(', ')}.`}
                      >
                        Missing {blockingGaps(hit).join(', ')}
                      </span>
                    )}
                  </div>
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

interface PurchaseOrderFormProps {
  suppliers: Supplier[];
  existingPOCount: number;
  onClose: () => void;
  onCreated: (po: PurchaseOrder) => void;
}

// Map the purchase module's Supplier down to the composer's vendor option.
function supplierToVendor(s: Supplier): ComposerVendorOption {
  return { id: s.id, name: s.name, code: s.code };
}

export function PurchaseOrderForm({ suppliers, existingPOCount, onClose, onCreated }: PurchaseOrderFormProps) {
  const toast = useToast();
  const { user } = useAuth();

  const vendorOptions = suppliers.map(supplierToVendor);

  // Which of OUR GSTINs is receiving decides CGST+SGST vs IGST. With 3 legal
  // entities over 4 GSTINs in 2 states, that is a per-shop fact, so read the
  // shop rather than assume a home state. Fail-soft: no shop -> the composer
  // says the split could not be told rather than showing a wrong one.
  const storeId = user?.activeStoreId ?? '';
  const [store, setStore] = useState<{ gstin?: string; state?: string } | null>(null);
  const [vendorId, setVendorId] = useState('');
  useEffect(() => {
    if (!storeId) return;
    let cancelled = false;
    storeApi
      .getStore(storeId)
      .then((doc) => {
        if (!cancelled) setStore(doc || null);
      })
      .catch(() => {
        if (!cancelled) setStore(null);
      });
    return () => {
      cancelled = true;
    };
  }, [storeId]);

  const vendor = suppliers.find((s) => s.id === vendorId);
  // Server-fed state list: a GSTIN prefix it does not contain ("88...") is not
  // a state, so the composer says "cannot tell" instead of quoting IGST off a
  // registration the engine reads no state from. Fail-closed until it loads.
  const stateNames = useGstStateCodes();
  const interstate =
    vendor && store
      ? isInterStateSupply(
          { gstin: vendor.gstNumber, state: vendor.state },
          { gstin: store.gstin, state: store.state },
          stateNames,
        )
      : null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-8">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <FileText className="w-5 h-5 text-blue-600" />
            Create Purchase Order
          </h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <XIcon className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Body — shared composer */}
        <div className="p-6">
          <PurchaseOrderComposer
            mode="page"
            vendors={vendorOptions}
            interstate={interstate}
            onVendorChange={setVendorId}
            allowAddLine
            allowRemoveLine
            renderProductCell={({ line, pickProduct, clearProduct, setNewProduct }) => (
              <ProductSearchSelect
                picked={{
                  productId: line.productId,
                  productName: line.productName,
                  sku: line.sku,
                  detail: line.productDetail ?? '',
                }}
                newProduct={line.newProduct}
                onPick={(p) =>
                  pickProduct({
                    productId: p.productId,
                    productName: p.productName,
                    sku: p.sku,
                    costPrice: p.costPrice,
                    gstRate: p.gstRate,
                    hsn: p.hsn,
                    detail: p.detail,
                  })
                }
                onClear={clearProduct}
                onSetNew={setNewProduct}
              />
            )}
            submitLabel="Create as Draft"
            submittingLabel="Creating..."
            onCancel={onClose}
            onSubmit={async (payload) => {
              const storeId = user?.activeStoreId ?? 'default';
              const resp = await vendorsApi.createPurchaseOrder({
                vendor_id: payload.vendorId,
                delivery_store_id: storeId,
                expected_date: payload.expectedDate || undefined,
                notes: payload.notes || undefined,
                items: payload.items.map((it) => ({
                  product_id: it.product_id,
                  product_name: it.product_name,
                  sku: it.sku,
                  new_product: it.new_product,
                  quantity: it.quantity,
                  unit_price: it.unit_price,
                })),
              });

              const poItems: POItem[] = payload.items.map((it) => ({
                productId: it.product_id ?? '',
                productName:
                  it.product_name ??
                  `${it.new_product?.brand ?? ''} ${it.new_product?.model ?? ''}`.trim(),
                sku: it.sku ?? '',
                quantity: it.quantity,
                unitCost: it.unit_price,
                taxRate: it.taxRate,
                total: it.lineTotal,
              }));

              const newPO: PurchaseOrder = {
                id: resp.po_id ?? `po-${Date.now()}`,
                poNumber: resp.po_number ?? `PO-${String(existingPOCount + 1).padStart(3, '0')}`,
                supplierId: payload.vendorId,
                supplierName: payload.vendorName,
                date: new Date().toISOString().split('T')[0],
                expectedDelivery: payload.expectedDate,
                status: 'DRAFT',
                items: poItems,
                subtotal: payload.subtotal,
                taxAmount: payload.taxAmount,
                total: resp.total_amount ?? payload.grandTotal,
                notes: payload.notes || undefined,
              };

              onCreated(newPO);
              toast.success(`Purchase Order ${newPO.poNumber} created as Draft`);
              // Say what is missing rather than letting a short tax total pass
              // unnoticed -- the server is the authority on the rate, so this
              // reports what it actually stored, including the lines it taxed
              // off a catalogue rate but could not tie to an HSN.
              const warnings = resp.gst_warnings ?? [];
              if (warnings.length > 0) {
                toast.warning(
                  `GST needs attention on ${warnings
                    .map((w) => `${w.product_name || w.product_id} (${w.missing})`)
                    .join(', ')} — add the HSN number on the product.`,
                );
              }
              const filled = resp.cost_filled ?? [];
              if (filled.length > 0) {
                toast.info(
                  `Cost recorded for ${filled.length} ${filled.length === 1 ? 'product' : 'products'} from the rates on this order.`,
                );
              }
            }}
          />
        </div>
      </div>
    </div>
  );
}
