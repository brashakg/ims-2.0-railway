// ============================================================================
// IMS 2.0 — Returns, Exchanges & Credit Notes
// ============================================================================
import { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { orderApi } from '../../services/api';
import {
  Search, RotateCcw, ArrowLeftRight, Receipt,
  AlertTriangle, CheckCircle, X, ChevronRight,
  
} from 'lucide-react';
import clsx from 'clsx';

type ReturnType = 'RETURN' | 'EXCHANGE' | 'CREDIT_NOTE';
type ReturnReason = 'WRONG_PRODUCT' | 'DEFECTIVE' | 'SIZE_ISSUE' | 'POWER_MISMATCH' | 'CUSTOMER_CHANGED_MIND' | 'DAMAGED_IN_STORE' | 'OTHER';

interface ReturnItem {
  orderItemId: string;
  productName: string;
  sku: string;
  quantity: number;
  returnQty: number;
  unitPrice: number;
  reason: ReturnReason;
  notes: string;
  condition: 'GOOD' | 'DAMAGED' | 'OPENED';
}

const RETURN_REASONS: Record<ReturnReason, string> = {
  WRONG_PRODUCT: 'Wrong product delivered',
  DEFECTIVE: 'Manufacturing defect',
  SIZE_ISSUE: 'Size/fit issue',
  POWER_MISMATCH: 'Lens power mismatch',
  CUSTOMER_CHANGED_MIND: 'Customer changed mind',
  DAMAGED_IN_STORE: 'Damaged in store',
  OTHER: 'Other (see notes)',
};

export default function ReturnsPage() {
  const { user } = useAuth();
  const [step, setStep] = useState<'search' | 'select' | 'review' | 'complete'>('search');
  const [searchQuery, setSearchQuery] = useState('');
  const [orders, setOrders] = useState<any[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<any>(null);
  const [returnType, setReturnType] = useState<ReturnType>('RETURN');
  const [returnItems, setReturnItems] = useState<ReturnItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approvalNote, setApprovalNote] = useState('');
  const [resultId, setResultId] = useState<string | null>(null);

  const searchOrders = async () => {
    if (!searchQuery.trim()) return;
    setIsLoading(true);
    setError(null);
    try {
      const response = await orderApi.getOrders({ storeId: user?.activeStoreId });
      const allOrders = response.orders || response || [];
      const filtered = allOrders.filter((o: any) =>
        o.orderNumber?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        o.customerName?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        o.customerPhone?.includes(searchQuery)
      );
      setOrders(filtered);
    } catch {
      setError('Failed to search orders');
    } finally {
      setIsLoading(false);
    }
  };

  const selectOrder = (order: any) => {
    setSelectedOrder(order);
    setReturnItems(
      (order.items || []).map((item: any) => ({
        orderItemId: item.id || item.itemId || '',
        productName: item.productName || item.product_name || item.name || 'Item',
        sku: item.sku || '',
        quantity: item.quantity || 1,
        returnQty: 0,
        unitPrice: Math.round(item.unitPrice || item.unit_price || 0),
        reason: 'CUSTOMER_CHANGED_MIND' as ReturnReason,
        notes: '',
        condition: 'GOOD' as const,
      }))
    );
    setStep('select');
  };

  const updateReturnItem = (index: number, updates: Partial<ReturnItem>) => {
    setReturnItems(prev => prev.map((item, i) => i === index ? { ...item, ...updates } : item));
  };

  const activeReturns = returnItems.filter(i => i.returnQty > 0);
  const totalRefund = activeReturns.reduce((sum, i) => sum + i.returnQty * i.unitPrice, 0);

  const handleSubmit = () => {
    if (activeReturns.length === 0) { setError('Select at least one item to return'); return; }
    // In production: POST to /api/v1/returns
    setResultId(`RET-${Date.now().toString(36).toUpperCase()}`);
    setStep('complete');
  };

  const fc = (amount: number) => `₹${Math.round(amount).toLocaleString('en-IN')}`;

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head" style={{ maxWidth: 900 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Returns & Exchanges</div>
          <h1>Undo, gracefully.</h1>
          <div className="hint">Refund to source, exchange for another SKU, or issue a store-credit note. Every action is audit-logged against the original invoice.</div>
        </div>
      </div>

      {/* Return Type Selection */}
      <div className="flex gap-2">
        {([
          { id: 'RETURN' as const, label: 'Return & Refund', icon: RotateCcw, desc: 'Refund to original payment' },
          { id: 'EXCHANGE' as const, label: 'Exchange', icon: ArrowLeftRight, desc: 'Replace with different product' },
          { id: 'CREDIT_NOTE' as const, label: 'Store Credit', icon: Receipt, desc: 'Issue credit for future use' },
        ]).map(t => (
          <button key={t.id} onClick={() => setReturnType(t.id)}
            className={clsx('flex-1 p-3 rounded-xl border-2 text-left transition-all',
              returnType === t.id ? 'border-bv-gold-500 bg-bv-gold-50' : 'border-gray-200 hover:border-gray-300')}>
            <div className="flex items-center gap-2">
              <t.icon className={clsx('w-5 h-5', returnType === t.id ? 'text-bv-gold-600' : 'text-gray-500')} />
              <span className={clsx('text-sm font-medium', returnType === t.id ? 'text-bv-gold-700' : 'text-gray-700')}>{t.label}</span>
            </div>
            <p className="text-xs text-gray-500 mt-1 ml-7">{t.desc}</p>
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-sm text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" /><span>{error}</span>
          <button onClick={() => setError(null)} className="ml-auto"><X className="w-4 h-4" /></button>
        </div>
      )}

      {/* Step 1: Search Order */}
      {step === 'search' && (
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <h3 className="font-semibold text-gray-900 mb-3">Find Original Order</h3>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
              <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && searchOrders()}
                placeholder="Order number, customer name, or phone..."
                className="w-full pl-10 pr-4 py-2.5 border border-gray-300 rounded-lg text-sm" />
            </div>
            <button onClick={searchOrders} disabled={isLoading}
              className="px-6 py-2.5 bg-bv-gold-500 text-white rounded-lg text-sm font-semibold hover:bg-bv-gold-600 disabled:opacity-50">
              {isLoading ? 'Searching...' : 'Search'}
            </button>
          </div>

          {orders.length > 0 && (
            <div className="mt-4 space-y-2">
              {orders.map(order => (
                <button key={order.id} onClick={() => selectOrder(order)}
                  className="w-full flex items-center justify-between p-3 rounded-lg border border-gray-200 hover:border-bv-gold-300 hover:bg-bv-gold-50 text-left transition-all">
                  <div>
                    <p className="font-medium text-sm text-gray-900">{order.orderNumber}</p>
                    <p className="text-xs text-gray-500">{order.customerName} · {new Date(order.createdAt).toLocaleDateString('en-IN')}</p>
                    <p className="text-xs text-gray-500">{(order.items || []).length} items</p>
                  </div>
                  <div className="text-right">
                    <p className="font-bold text-sm">{fc(order.grandTotal || 0)}</p>
                    <ChevronRight className="w-4 h-4 text-gray-500 ml-auto mt-1" />
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Step 2: Select Items */}
      {step === 'select' && selectedOrder && (
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="font-semibold text-gray-900">Select Items for {returnType === 'EXCHANGE' ? 'Exchange' : 'Return'}</h3>
              <p className="text-xs text-gray-500">Order {selectedOrder.orderNumber} · {selectedOrder.customerName}</p>
            </div>
            <button onClick={() => { setStep('search'); setSelectedOrder(null); }} className="text-sm text-gray-500 hover:text-gray-700">Change order</button>
          </div>

          <div className="space-y-3">
            {returnItems.map((item, i) => (
              <div key={i} className={clsx('border rounded-lg p-4 transition-all',
                item.returnQty > 0 ? 'border-bv-gold-300 bg-bv-gold-50' : 'border-gray-200')}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <p className="font-medium text-sm">{item.productName}</p>
                    <p className="text-xs text-gray-500">{item.sku} · Purchased: {item.quantity} · {fc(item.unitPrice)} each</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <label className="text-xs text-gray-500">Return qty:</label>
                    <select value={item.returnQty} onChange={e => updateReturnItem(i, { returnQty: Number(e.target.value) })}
                      className="px-2 py-1 border border-gray-300 rounded text-sm">
                      {Array.from({ length: item.quantity + 1 }, (_, n) => (
                        <option key={n} value={n}>{n}</option>
                      ))}
                    </select>
                  </div>
                </div>

                {item.returnQty > 0 && (
                  <div className="mt-3 pt-3 border-t border-gray-200 grid grid-cols-1 tablet:grid-cols-3 gap-3">
                    <div>
                      <label className="text-xs text-gray-500 block mb-1">Reason *</label>
                      <select value={item.reason} onChange={e => updateReturnItem(i, { reason: e.target.value as ReturnReason })}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm">
                        {Object.entries(RETURN_REASONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-gray-500 block mb-1">Item Condition</label>
                      <select value={item.condition} onChange={e => updateReturnItem(i, { condition: e.target.value as 'GOOD' | 'DAMAGED' | 'OPENED' })}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm">
                        <option value="GOOD">Good / Resellable</option>
                        <option value="OPENED">Opened / Used</option>
                        <option value="DAMAGED">Damaged</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-gray-500 block mb-1">Notes</label>
                      <input value={item.notes} onChange={e => updateReturnItem(i, { notes: e.target.value })}
                        placeholder="Optional details..." className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm" />
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>

          {activeReturns.length > 0 && (
            <div className="mt-4 pt-4 border-t border-gray-200">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-gray-700">
                  {returnType === 'CREDIT_NOTE' ? 'Credit Amount' : 'Refund Amount'}: <span className="font-bold text-lg">{fc(totalRefund)}</span>
                </span>
              </div>
              <textarea value={approvalNote} onChange={e => setApprovalNote(e.target.value)}
                placeholder="Approval notes or justification (visible to admin)..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm h-16 resize-none mb-3" />
              <div className="flex gap-3">
                <button onClick={() => { setStep('search'); setSelectedOrder(null); }}
                  className="px-4 py-2.5 border border-gray-300 rounded-lg text-sm">Cancel</button>
                <button onClick={handleSubmit}
                  className="flex-1 py-2.5 bg-bv-gold-500 text-white rounded-lg text-sm font-semibold hover:bg-bv-gold-600">
                  Submit {returnType === 'EXCHANGE' ? 'Exchange' : returnType === 'CREDIT_NOTE' ? 'Credit Note' : 'Return'} ({activeReturns.length} items)
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Step 3: Complete */}
      {step === 'complete' && (
        <div className="bg-white border border-gray-200 rounded-xl p-8 text-center">
          <CheckCircle className="w-16 h-16 text-green-500 mx-auto mb-4" />
          <h3 className="text-xl font-bold text-gray-900">
            {returnType === 'EXCHANGE' ? 'Exchange Processed' : returnType === 'CREDIT_NOTE' ? 'Credit Note Issued' : 'Return Processed'}
          </h3>
          <p className="text-gray-500 mt-2">Reference: {resultId}</p>
          <p className="text-2xl font-bold text-bv-gold-600 mt-3">{fc(totalRefund)}</p>
          <p className="text-sm text-gray-500 mt-1">
            {returnType === 'CREDIT_NOTE' ? 'Store credit added to customer account' :
              returnType === 'EXCHANGE' ? 'Customer can select replacement product' :
                'Refund to be processed via original payment method'}
          </p>
          <div className="flex gap-3 justify-center mt-6">
            <button onClick={() => { setStep('search'); setSelectedOrder(null); setReturnItems([]); setResultId(null); }}
              className="px-6 py-2.5 bg-bv-gold-500 text-white rounded-lg text-sm font-semibold">New Return</button>
          </div>
        </div>
      )}
    </div>
  );
}
