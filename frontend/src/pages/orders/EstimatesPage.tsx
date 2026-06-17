// ============================================================================
// IMS 2.0 - Estimates / Quotations
// ============================================================================
// Create + list non-binding estimates. An estimate reserves NO stock, gets NO
// invoice serial, and charges NO GST against itself -- it is a priced quote the
// customer can hold (with an estimated GST breakup + validity date) before
// committing. The print action opens the server-rendered HTML challan-style
// document ("ESTIMATE / QUOTATION - not a tax invoice").

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { formatDateIST } from '../../utils/datetime';
// Direct import: barrel re-export can fail to resolve for newly added modules.
import {
  estimatesApi,
  type EstimateDocument,
  type EstimateItemInput,
} from '../../services/api/estimates';
import {
  FileText, Plus, Trash2, Printer, X, RefreshCw, Calculator, Search,
} from 'lucide-react';

const GST_CATEGORIES: { value: string; label: string }[] = [
  { value: 'FRAME', label: 'Frame (5%)' },
  { value: 'OPTICAL_LENS', label: 'Optical Lens (5%)' },
  { value: 'CONTACT_LENS', label: 'Contact Lens (5%)' },
  { value: 'READING_GLASSES', label: 'Reading Glasses (5%)' },
  { value: 'SUNGLASS', label: 'Sunglass (18%)' },
  { value: 'WATCH', label: 'Watch (18%)' },
  { value: 'ACCESSORIES', label: 'Accessories (18%)' },
  { value: 'SERVICES', label: 'Services (18%)' },
];

function money(n: number | undefined | null): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 2,
  }).format(Number(n || 0));
}

interface DraftLine extends EstimateItemInput {
  _key: string;
}

function newLine(): DraftLine {
  return {
    _key: Math.random().toString(36).slice(2),
    description: '',
    category: 'FRAME',
    quantity: 1,
    mrp: undefined,
    offer_price: 0,
    discount_percent: 0,
  };
}

export function EstimatesPage() {
  const { user } = useAuth();
  const toast = useToast();

  const [estimates, setEstimates] = useState<EstimateDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  const [showCreate, setShowCreate] = useState(false);
  const [customerName, setCustomerName] = useState('');
  const [customerPhone, setCustomerPhone] = useState('');
  const [customerAddress, setCustomerAddress] = useState('');
  const [validityDays, setValidityDays] = useState(15);
  const [terms, setTerms] = useState('');
  const [lines, setLines] = useState<DraftLine[]>([newLine()]);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await estimatesApi.list();
      setEstimates(res.estimates || []);
    } catch {
      toast.error('Could not load estimates');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const resetForm = () => {
    setCustomerName('');
    setCustomerPhone('');
    setCustomerAddress('');
    setValidityDays(15);
    setTerms('');
    setLines([newLine()]);
  };

  const previewTotal = lines.reduce((sum, l) => {
    const unit = Number(l.offer_price || 0);
    const disc = Math.max(0, Math.min(100, Number(l.discount_percent || 0)));
    const qty = Math.max(1, Number(l.quantity || 1));
    return sum + Math.round(unit * (1 - disc / 100) * qty * 100) / 100;
  }, 0);

  const handleSave = async () => {
    const valid = lines.filter((l) => l.description.trim() && Number(l.offer_price) >= 0);
    if (valid.length === 0) {
      toast.error('Add at least one line item with a description and price');
      return;
    }
    setSaving(true);
    try {
      const created = await estimatesApi.create({
        customer_name: customerName,
        customer_phone: customerPhone,
        customer_address: customerAddress,
        store_id: user?.activeStoreId || undefined,
        validity_days: validityDays,
        terms,
        items: valid.map((l) => ({
          description: l.description.trim(),
          category: l.category,
          quantity: Math.max(1, Number(l.quantity || 1)),
          mrp: l.mrp !== undefined && l.mrp !== null ? Number(l.mrp) : undefined,
          offer_price: Number(l.offer_price || 0),
          discount_percent: Number(l.discount_percent || 0),
        })),
      });
      toast.success(`Estimate ${created.estimate_number} created`);
      setShowCreate(false);
      resetForm();
      load();
    } catch {
      toast.error('Could not create estimate');
    } finally {
      setSaving(false);
    }
  };

  const print = async (e: EstimateDocument) => {
    try {
      await estimatesApi.openPrint(e.estimate_id);
    } catch {
      toast.error('Could not open estimate');
    }
  };

  const filtered = estimates.filter((e) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      (e.estimate_number || '').toLowerCase().includes(q) ||
      (e.customer_name || '').toLowerCase().includes(q)
    );
  });

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <FileText className="w-6 h-6 text-bv-red-600" />
            Estimates / Quotations
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Non-binding priced quotes with an estimated GST breakup. Not a tax invoice.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className="btn-outline flex items-center gap-2" title="Refresh">
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => { resetForm(); setShowCreate(true); }}
            className="btn-primary flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Create Estimate
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="relative mb-4">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by estimate number or customer..."
          className="input-field pl-10 w-full"
        />
      </div>

      {/* List */}
      <div className="card">
        {loading ? (
          <div className="p-8 text-center text-gray-500">Loading...</div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            No estimates yet. Click "Create Estimate" to make one.
          </div>
        ) : (
          <div className="divide-y divide-gray-100">
            {filtered.map((e) => (
              <div key={e.estimate_id} className="flex items-center justify-between p-4 hover:bg-gray-50">
                <div>
                  <div className="font-medium text-gray-900">{e.estimate_number}</div>
                  <div className="text-sm text-gray-500">
                    {e.customer_name || 'Walk-in'} &middot; {(e.items || []).length} item(s)
                    {e.valid_until ? ` · valid until ${formatDateIST(e.valid_until)}` : ''}
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <div className="font-bold text-gray-900">{money(e.totals?.grand_total)}</div>
                    <div className="text-xs text-gray-400">{e.created_at ? formatDateIST(e.created_at) : ''}</div>
                  </div>
                  <button
                    onClick={() => print(e)}
                    className="btn-outline flex items-center gap-2 text-sm"
                    title="Print estimate"
                  >
                    <Printer className="w-4 h-4" />
                    Print
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[92vh] overflow-y-auto">
            <div className="flex items-center justify-between p-5 border-b border-gray-200 sticky top-0 bg-white z-10">
              <h2 className="text-lg font-bold text-gray-900">Create Estimate</h2>
              <button onClick={() => setShowCreate(false)} className="p-2 hover:bg-gray-100 rounded-lg" aria-label="Close">
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>

            <div className="p-5 space-y-5">
              {/* Customer */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <label className="block text-sm text-gray-600 mb-1">Customer Name</label>
                  <input value={customerName} onChange={(e) => setCustomerName(e.target.value)} className="input-field w-full" placeholder="Walk-in" />
                </div>
                <div>
                  <label className="block text-sm text-gray-600 mb-1">Phone</label>
                  <input value={customerPhone} onChange={(e) => setCustomerPhone(e.target.value)} className="input-field w-full" />
                </div>
                <div>
                  <label className="block text-sm text-gray-600 mb-1">Valid for (days)</label>
                  <input type="number" min={1} max={365} value={validityDays} onChange={(e) => setValidityDays(Number(e.target.value))} className="input-field w-full" />
                </div>
              </div>
              <div>
                <label className="block text-sm text-gray-600 mb-1">Address (optional)</label>
                <input value={customerAddress} onChange={(e) => setCustomerAddress(e.target.value)} className="input-field w-full" />
              </div>

              {/* Line items */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-semibold text-gray-900">Line Items</h3>
                  <button onClick={() => setLines((p) => [...p, newLine()])} className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1">
                    <Plus className="w-4 h-4" /> Add Line
                  </button>
                </div>
                <div className="space-y-2">
                  {lines.map((l, idx) => (
                    <div key={l._key} className="grid grid-cols-12 gap-2 items-end border border-gray-200 rounded-lg p-2">
                      <div className="col-span-12 md:col-span-4">
                        <label className="block text-xs text-gray-500 mb-1">Description</label>
                        <input
                          value={l.description}
                          onChange={(e) => setLines((p) => p.map((x, i) => i === idx ? { ...x, description: e.target.value } : x))}
                          className="input-field w-full text-sm" placeholder="e.g. Ray-Ban Frame RB1234"
                        />
                      </div>
                      <div className="col-span-6 md:col-span-2">
                        <label className="block text-xs text-gray-500 mb-1">Category</label>
                        <select
                          value={l.category}
                          onChange={(e) => setLines((p) => p.map((x, i) => i === idx ? { ...x, category: e.target.value } : x))}
                          className="input-field w-full text-sm"
                        >
                          {GST_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                        </select>
                      </div>
                      <div className="col-span-3 md:col-span-1">
                        <label className="block text-xs text-gray-500 mb-1">Qty</label>
                        <input type="number" min={1} value={l.quantity}
                          onChange={(e) => setLines((p) => p.map((x, i) => i === idx ? { ...x, quantity: Number(e.target.value) } : x))}
                          className="input-field w-full text-sm" />
                      </div>
                      <div className="col-span-3 md:col-span-2">
                        <label className="block text-xs text-gray-500 mb-1">MRP</label>
                        <input type="number" min={0} value={l.mrp ?? ''}
                          onChange={(e) => setLines((p) => p.map((x, i) => i === idx ? { ...x, mrp: e.target.value === '' ? undefined : Number(e.target.value) } : x))}
                          className="input-field w-full text-sm" />
                      </div>
                      <div className="col-span-4 md:col-span-2">
                        <label className="block text-xs text-gray-500 mb-1">Offer Price</label>
                        <input type="number" min={0} value={l.offer_price}
                          onChange={(e) => setLines((p) => p.map((x, i) => i === idx ? { ...x, offer_price: Number(e.target.value) } : x))}
                          className="input-field w-full text-sm" />
                      </div>
                      <div className="col-span-1 flex justify-end">
                        <button
                          onClick={() => setLines((p) => p.length > 1 ? p.filter((_, i) => i !== idx) : p)}
                          className="p-2 text-gray-400 hover:text-red-600"
                          title="Remove line"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Terms */}
              <div>
                <label className="block text-sm text-gray-600 mb-1">Terms &amp; Conditions (optional)</label>
                <textarea value={terms} onChange={(e) => setTerms(e.target.value)} rows={2} className="input-field w-full" />
              </div>

              {/* Preview total (server recomputes the exact GST split) */}
              <div className="flex items-center justify-between bg-gray-50 rounded-lg p-3">
                <span className="text-sm text-gray-600 flex items-center gap-2">
                  <Calculator className="w-4 h-4" /> Estimated gross (GST-inclusive)
                </span>
                <span className="text-lg font-bold text-gray-900">{money(previewTotal)}</span>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 p-5 border-t border-gray-200 sticky bottom-0 bg-white">
              <button onClick={() => setShowCreate(false)} className="btn-outline">Cancel</button>
              <button onClick={handleSave} disabled={saving} className="btn-primary flex items-center gap-2">
                {saving ? 'Saving...' : 'Create Estimate'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default EstimatesPage;
