// ============================================================================
// IMS 2.0 - Create Purchase Order Modal
// ============================================================================

import { useState, useEffect, useRef } from 'react';
import {
  FileText,
  Plus,
  X as XIcon,
  Trash2,
  Loader2,
  Search,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { vendorsApi, productApi } from '../../services/api';
import type { Supplier, PurchaseOrder, POItem } from './purchaseTypes';

// A PO line now carries a REAL catalogued product_id (set by the picker), never
// a fabricated `new-<timestamp>` id. With the Hub Phase-2 PO catalog gate ON,
// the backend rejects any line whose product_id is not on the products spine
// (422 UNKNOWN_PRODUCT) -- so the form must hand it a genuine product.
interface POFormItem {
  productId: string;
  productName: string;
  sku: string;
  quantity: number;
  unitCost: number;
  taxRate: number;
}

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

export function PurchaseOrderForm({ suppliers, existingPOCount, onClose, onCreated }: PurchaseOrderFormProps) {
  const toast = useToast();
  const { user } = useAuth();

  const [supplierId, setSupplierId] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [expectedDelivery, setExpectedDelivery] = useState('');
  const [notes, setNotes] = useState('');
  const [items, setItems] = useState<POFormItem[]>([
    { productId: '', productName: '', sku: '', quantity: 1, unitCost: 0, taxRate: 18 },
  ]);

  const addItem = () => {
    setItems(prev => [...prev, { productId: '', productName: '', sku: '', quantity: 1, unitCost: 0, taxRate: 18 }]);
  };

  const removeItem = (index: number) => {
    setItems(prev => prev.filter((_, i) => i !== index));
  };

  const updateItem = (index: number, field: string, value: string | number) => {
    setItems(prev => prev.map((item, i) => i === index ? { ...item, [field]: value } : item));
  };

  const pickProduct = (index: number, p: PickedProduct) => {
    setItems(prev => prev.map((item, i) => i === index
      ? {
          ...item,
          productId: p.productId,
          productName: p.productName,
          sku: p.sku,
          // Prefill cost from the catalog only when the line still has none; the
          // buyer can always override the negotiated PO price.
          unitCost: item.unitCost > 0 ? item.unitCost : p.costPrice,
        }
      : item));
  };

  const clearProduct = (index: number) => {
    setItems(prev => prev.map((item, i) => i === index
      ? { ...item, productId: '', productName: '', sku: '' }
      : item));
  };

  const calcLineTotal = (item: POFormItem) => {
    return item.quantity * item.unitCost * (1 + item.taxRate / 100);
  };

  const calcSubtotal = () => items.reduce((sum, item) => sum + item.quantity * item.unitCost, 0);
  const calcTax = () => items.reduce((sum, item) => sum + item.quantity * item.unitCost * item.taxRate / 100, 0);
  const calcGrandTotal = () => calcSubtotal() + calcTax();

  const handleCreate = async () => {
    if (!supplierId) {
      toast.error('Please select a supplier');
      return;
    }
    if (!expectedDelivery) {
      toast.error('Please set an expected delivery date');
      return;
    }
    // A valid line must reference a REAL catalogued product (product_id set by
    // the picker) plus a positive qty + cost. Lines without a picked product are
    // dropped -- they would be rejected by the PO catalog gate anyway.
    const validItems = items.filter(item => item.productId && item.quantity > 0 && item.unitCost > 0);
    if (validItems.length === 0) {
      toast.error('Add at least one catalogued product with a quantity and unit cost');
      return;
    }
    const unpicked = items.filter(item => !item.productId && (item.quantity > 0 || item.unitCost > 0));
    if (unpicked.length > 0) {
      toast.error('Pick a catalogued product for every line (or remove the empty line)');
      return;
    }

    const storeId = user?.activeStoreId ?? 'default';
    const supplier = suppliers.find(s => s.id === supplierId);

    setIsSaving(true);
    try {
      const resp = await vendorsApi.createPurchaseOrder({
        vendor_id: supplierId,
        delivery_store_id: storeId,
        expected_date: expectedDelivery,
        notes: notes || undefined,
        items: validItems.map((item) => ({
          product_id: item.productId,
          product_name: item.productName,
          sku: item.sku || 'N/A',
          quantity: item.quantity,
          unit_price: item.unitCost,
        })),
      });

      const poItems: POItem[] = validItems.map((item) => ({
        productId: item.productId,
        productName: item.productName,
        sku: item.sku || 'N/A',
        quantity: item.quantity,
        unitCost: item.unitCost,
        taxRate: item.taxRate,
        total: calcLineTotal(item),
      }));

      const subtotal = validItems.reduce((sum, item) => sum + item.quantity * item.unitCost, 0);
      const taxAmount = validItems.reduce((sum, item) => sum + item.quantity * item.unitCost * item.taxRate / 100, 0);

      const newPO: PurchaseOrder = {
        id: resp.po_id ?? `po-${Date.now()}`,
        poNumber: resp.po_number ?? `PO-${String(existingPOCount + 1).padStart(3, '0')}`,
        supplierId,
        supplierName: supplier?.name ?? 'Unknown',
        date: new Date().toISOString().split('T')[0],
        expectedDelivery,
        status: 'DRAFT',
        items: poItems,
        subtotal,
        taxAmount,
        total: resp.total_amount ?? subtotal + taxAmount,
        notes: notes || undefined,
      };

      onCreated(newPO);
      toast.success(`Purchase Order ${newPO.poNumber} created as Draft`);
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Failed to create purchase order';
      toast.error(msg);
    } finally {
      setIsSaving(false);
    }
  };

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

        {/* Body */}
        <div className="p-6 space-y-6">
          {/* Supplier & Delivery Date */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Supplier *</label>
              <select
                value={supplierId}
                onChange={(e) => setSupplierId(e.target.value)}
                className="input-field"
              >
                <option value="">Select a supplier...</option>
                {suppliers.map(s => (
                  <option key={s.id} value={s.id}>{s.name} ({s.code})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Expected Delivery Date *</label>
              <input
                type="date"
                value={expectedDelivery}
                onChange={(e) => setExpectedDelivery(e.target.value)}
                className="input-field"
              />
            </div>
          </div>

          {/* Line Items */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <label className="block text-sm font-medium text-gray-700">Line Items *</label>
              <button
                onClick={addItem}
                className="text-sm text-blue-600 hover:text-blue-700 font-medium flex items-center gap-1"
              >
                <Plus className="w-4 h-4" />
                Add Item
              </button>
            </div>

            <div className="space-y-3">
              {items.map((item, index) => (
                <div key={index} className="p-3 bg-gray-50 rounded-lg border border-gray-200">
                  <div className="grid grid-cols-12 gap-2 items-end">
                    <div className="col-span-12 tablet:col-span-5">
                      <label className="block text-xs text-gray-600 mb-1">Product</label>
                      <ProductSearchSelect
                        picked={{ productId: item.productId, productName: item.productName, sku: item.sku }}
                        onPick={(p) => pickProduct(index, p)}
                        onClear={() => clearProduct(index)}
                      />
                    </div>
                    <div className="col-span-6 tablet:col-span-1">
                      <label className="block text-xs text-gray-600 mb-1">Qty</label>
                      <input
                        type="number"
                        min="1"
                        value={item.quantity}
                        onChange={(e) => updateItem(index, 'quantity', parseInt(e.target.value) || 0)}
                        className="input-field text-sm"
                      />
                    </div>
                    <div className="col-span-6 tablet:col-span-2">
                      <label className="block text-xs text-gray-600 mb-1">Unit Cost ({'₹'})</label>
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={item.unitCost}
                        onChange={(e) => updateItem(index, 'unitCost', parseFloat(e.target.value) || 0)}
                        className="input-field text-sm"
                      />
                    </div>
                    <div className="col-span-4 tablet:col-span-1">
                      <label className="block text-xs text-gray-600 mb-1">Tax %</label>
                      <input
                        type="number"
                        min="0"
                        max="28"
                        value={item.taxRate}
                        onChange={(e) => updateItem(index, 'taxRate', parseFloat(e.target.value) || 0)}
                        className="input-field text-sm"
                      />
                    </div>
                    <div className="col-span-6 tablet:col-span-2 text-right">
                      <label className="block text-xs text-gray-600 mb-1">Total</label>
                      <p className="text-sm font-semibold text-gray-900 py-2">{'₹'}{calcLineTotal(item).toLocaleString()}</p>
                    </div>
                    <div className="col-span-2 tablet:col-span-1 flex justify-end">
                      <button
                        onClick={() => removeItem(index)}
                        disabled={items.length === 1}
                        className="p-2 text-red-500 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Totals */}
          <div className="flex justify-end">
            <div className="w-64 space-y-2 p-4 bg-gray-50 rounded-lg">
              <div className="flex justify-between text-sm">
                <span className="text-gray-600">Subtotal</span>
                <span className="font-medium text-gray-900">{'₹'}{calcSubtotal().toLocaleString()}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-600">Tax</span>
                <span className="font-medium text-gray-900">{'₹'}{calcTax().toLocaleString()}</span>
              </div>
              <div className="flex justify-between text-sm font-bold border-t border-gray-300 pt-2">
                <span className="text-gray-900">Grand Total</span>
                <span className="text-gray-900">{'₹'}{calcGrandTotal().toLocaleString()}</span>
              </div>
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Any additional notes for this purchase order..."
              rows={3}
              className="input-field"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={isSaving}
            className="btn-primary flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {isSaving ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <FileText className="w-4 h-4" />
            )}
            {isSaving ? 'Creating...' : 'Create as Draft'}
          </button>
        </div>
      </div>
    </div>
  );
}
