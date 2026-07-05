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
import { PurchaseOrderComposer } from '../../components/purchase/PurchaseOrderComposer';
import type { ComposerVendorOption } from '../../components/purchase/PurchaseOrderComposer';
import type { Supplier, PurchaseOrder, POItem } from './purchaseTypes';

interface PickedProduct {
  productId: string;
  productName: string;
  sku: string;
  costPrice: number;
}

// Minimal shape we read off a /products row -- the endpoint returns full docs.
interface ProductHit {
  product_id?: string;
  productId?: string;
  sku?: string;
  name?: string;
  brand?: string;
  cost_price?: number;
  catalog_status?: string;
}

function hitToPicked(hit: ProductHit): PickedProduct {
  const brand = (hit.brand || '').trim();
  const name = (hit.name || '').trim();
  const display = [brand, name].filter(Boolean).join(' ') || name || (hit.sku || '');
  return {
    productId: String(hit.product_id || hit.productId || ''),
    productName: display,
    sku: hit.sku || '',
    costPrice: Number(hit.cost_price) > 0 ? Number(hit.cost_price) : 0,
  };
}

// ---------------------------------------------------------------------------
// Product search-select. Debounced typeahead against GET /products?search=.
// Once a product is picked it shows as a locked chip (real product_id behind
// it); "change" clears the pick and re-opens the search. This is what lets the
// PO catalog gate be turned ON without breaking the manual Create-PO flow.
// ---------------------------------------------------------------------------
function ProductSearchSelect({
  picked,
  onPick,
  onClear,
}: {
  picked: { productId: string; productName: string; sku: string };
  onPick: (p: PickedProduct) => void;
  onClear: () => void;
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

  if (picked.productId) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 bg-white border border-gray-300 rounded-lg text-sm">
        <span className="font-medium text-gray-900 truncate">{picked.productName}</span>
        <span className="text-xs text-gray-500 shrink-0">{picked.sku}</span>
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
      {open && (
        <div className="absolute z-20 mt-1 w-full max-h-60 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg">
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
                  className="w-full text-left px-3 py-2 hover:bg-gray-50 border-b border-gray-100 last:border-0"
                >
                  <div className="text-sm font-medium text-gray-900 truncate">{p.productName}</div>
                  <div className="text-xs text-gray-500 flex items-center gap-2">
                    <span>{p.sku}</span>
                    {p.costPrice > 0 && <span>{'₹'}{p.costPrice.toLocaleString()} cost</span>}
                    {hit.catalog_status && hit.catalog_status !== 'ACTIVE' && (
                      <span
                        className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-medium"
                        title="Draft product -- can be added to a PO, but the PO can't be sent until cataloguing is complete"
                      >
                        Draft
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
            allowAddLine
            allowRemoveLine
            renderProductCell={({ line, pickProduct, clearProduct }) => (
              <ProductSearchSelect
                picked={{ productId: line.productId, productName: line.productName, sku: line.sku }}
                onPick={(p) =>
                  pickProduct({
                    productId: p.productId,
                    productName: p.productName,
                    sku: p.sku,
                    costPrice: p.costPrice,
                  })
                }
                onClear={clearProduct}
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
                  quantity: it.quantity,
                  unit_price: it.unit_price,
                })),
              });

              const poItems: POItem[] = payload.items.map((it) => ({
                productId: it.product_id,
                productName: it.product_name,
                sku: it.sku,
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
            }}
          />
        </div>
      </div>
    </div>
  );
}
