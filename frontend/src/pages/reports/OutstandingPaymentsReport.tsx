// ============================================================================
// IMS 2.0 — Outstanding Payments Report
// ============================================================================
import { useState, useEffect, useMemo } from 'react';
import { useAuth } from '../../context/AuthContext';
import { orderApi } from '../../services/api';
import {
  IndianRupee, Phone,
  ChevronDown, ChevronUp, Printer, Search,
  
} from 'lucide-react';
import clsx from 'clsx';

interface OutstandingOrder {
  id: string;
  orderNumber: string;
  customerName: string;
  customerPhone: string;
  grandTotal: number;
  amountPaid: number;
  balanceDue: number;
  createdAt: string;
  storeId: string;
  daysOld: number;
  items: any[];
}

type AgeBucket = '0-7' | '8-15' | '16-30' | '31-60' | '60+';

export default function OutstandingPaymentsReport() {
  const { user } = useAuth();
  const [orders, setOrders] = useState<OutstandingOrder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState<'amount' | 'age' | 'name'>('amount');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [selectedBucket, setSelectedBucket] = useState<AgeBucket | 'ALL'>('ALL');

  useEffect(() => { loadOutstanding(); }, []);

  const loadOutstanding = async () => {
    setIsLoading(true);
    try {
      const res = await orderApi.getOrders({ storeId: user?.activeStoreId });
      const all = res?.orders || res || [];
      const now = new Date();
      const outstanding: OutstandingOrder[] = all
        .filter((o: any) => (o.balanceDue || 0) > 0 && o.orderStatus !== 'CANCELLED')
        .map((o: any) => {
          const created = new Date(o.createdAt || o.created_at || '');
          return {
            id: o.id || o.order_id,
            orderNumber: o.orderNumber || o.order_number || '',
            customerName: o.customerName || o.customer_name || 'Unknown',
            customerPhone: o.customerPhone || o.customer_phone || '',
            grandTotal: Math.round(o.grandTotal || o.grand_total || 0),
            amountPaid: Math.round(o.amountPaid || o.amount_paid || 0),
            balanceDue: Math.round(o.balanceDue || o.balance_due || 0),
            createdAt: o.createdAt || o.created_at || '',
            storeId: o.storeId || o.store_id || '',
            daysOld: Math.floor((now.getTime() - created.getTime()) / 86400000),
            items: o.items || [],
          };
        });
      setOrders(outstanding);
    } catch {
      setOrders([]);
    } finally {
      setIsLoading(false);
    }
  };

  const getBucket = (days: number): AgeBucket => {
    if (days <= 7) return '0-7';
    if (days <= 15) return '8-15';
    if (days <= 30) return '16-30';
    if (days <= 60) return '31-60';
    return '60+';
  };

  const buckets = useMemo(() => {
    const b: Record<AgeBucket, { count: number; total: number }> = {
      '0-7': { count: 0, total: 0 },
      '8-15': { count: 0, total: 0 },
      '16-30': { count: 0, total: 0 },
      '31-60': { count: 0, total: 0 },
      '60+': { count: 0, total: 0 },
    };
    for (const o of orders) {
      const bk = getBucket(o.daysOld);
      b[bk].count++;
      b[bk].total += o.balanceDue;
    }
    return b;
  }, [orders]);

  const totalOutstanding = orders.reduce((s, o) => s + o.balanceDue, 0);

  const filtered = useMemo(() => {
    let list = orders;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      list = list.filter(o =>
        o.customerName.toLowerCase().includes(q) ||
        o.customerPhone.includes(q) ||
        o.orderNumber.toLowerCase().includes(q)
      );
    }
    if (selectedBucket !== 'ALL') {
      list = list.filter(o => getBucket(o.daysOld) === selectedBucket);
    }
    list = [...list].sort((a, b) => {
      const mul = sortDir === 'desc' ? -1 : 1;
      if (sortBy === 'amount') return mul * (a.balanceDue - b.balanceDue);
      if (sortBy === 'age') return mul * (a.daysOld - b.daysOld);
      return mul * a.customerName.localeCompare(b.customerName);
    });
    return list;
  }, [orders, searchQuery, selectedBucket, sortBy, sortDir]);

  const fc = (n: number) => `₹${Math.round(n).toLocaleString('en-IN')}`;

  const toggleSort = (field: typeof sortBy) => {
    if (sortBy === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(field); setSortDir('desc'); }
  };

  const handlePrint = () => window.print();

  const BUCKET_COLORS: Record<AgeBucket, string> = {
    '0-7': 'bg-green-50 border-green-200 text-green-700',
    '8-15': 'bg-yellow-50 border-yellow-200 text-yellow-700',
    '16-30': 'bg-orange-50 border-orange-200 text-orange-700',
    '31-60': 'bg-red-50 border-red-200 text-red-700',
    '60+': 'bg-red-100 border-red-300 text-red-800',
  };

  return (
    <div className="max-w-5xl mx-auto p-4 tablet:p-6 space-y-5 print:p-0">
      {/* Header */}
      <div className="flex items-center justify-between print:hidden">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Outstanding Payments</h1>
          <p className="text-sm text-gray-500">{orders.length} orders with pending balance</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handlePrint} className="flex items-center gap-1.5 px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">
            <Printer className="w-4 h-4" /> Print
          </button>
        </div>
      </div>

      {/* Print header */}
      <div className="hidden print:block text-center mb-4">
        <h2 className="text-lg font-bold">Better Vision Opticals — Outstanding Payments Report</h2>
        <p className="text-sm text-gray-600">{new Date().toLocaleDateString('en-IN', { day: '2-digit', month: 'long', year: 'numeric' })}</p>
      </div>

      {/* Summary */}
      <div className="bg-white border border-gray-200 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <p className="text-sm text-gray-500">Total Outstanding</p>
            <p className="text-3xl font-bold text-red-600">{fc(totalOutstanding)}</p>
          </div>
          <div className="text-right">
            <p className="text-sm text-gray-500">Orders</p>
            <p className="text-2xl font-bold text-gray-900">{orders.length}</p>
          </div>
        </div>

        {/* Age buckets */}
        <div className="grid grid-cols-5 gap-2">
          {(Object.entries(buckets) as [AgeBucket, { count: number; total: number }][]).map(([bucket, data]) => (
            <button key={bucket} onClick={() => setSelectedBucket(selectedBucket === bucket ? 'ALL' : bucket)}
              className={clsx('p-2.5 rounded-lg border text-center transition-all',
                selectedBucket === bucket ? 'ring-2 ring-bv-gold-500' : '',
                BUCKET_COLORS[bucket])}>
              <p className="text-[10px] font-medium">{bucket} days</p>
              <p className="text-sm font-bold">{fc(data.total)}</p>
              <p className="text-[10px]">{data.count} orders</p>
            </button>
          ))}
        </div>
      </div>

      {/* Search + Sort */}
      <div className="flex gap-3 print:hidden">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search customer, phone, or order number..."
            className="w-full pl-9 pr-4 py-2.5 border border-gray-300 rounded-lg text-sm" />
        </div>
        <div className="flex gap-1.5">
          {([
            { id: 'amount' as const, label: 'Amount' },
            { id: 'age' as const, label: 'Age' },
            { id: 'name' as const, label: 'Name' },
          ]).map(s => (
            <button key={s.id} onClick={() => toggleSort(s.id)}
              className={clsx('px-3 py-2 rounded-lg text-xs font-medium border transition-all flex items-center gap-1',
                sortBy === s.id ? 'bg-bv-gold-50 border-bv-red-300 text-bv-gold-700' : 'border-gray-200 text-gray-500')}>
              {s.label}
              {sortBy === s.id && (sortDir === 'desc' ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />)}
            </button>
          ))}
        </div>
      </div>

      {/* Orders List */}
      {isLoading ? (
        <div className="text-center py-12"><div className="w-8 h-8 border-2 border-gray-200 border-t-bv-gold-500 rounded-full animate-spin mx-auto" /></div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <IndianRupee className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>{searchQuery || selectedBucket !== 'ALL' ? 'No matching orders' : 'No outstanding payments!'}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map(order => {
            const bucket = getBucket(order.daysOld);
            return (
              <div key={order.id} className="bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-4">
                {/* Age indicator */}
                <div className={clsx('w-12 h-12 rounded-lg flex flex-col items-center justify-center flex-shrink-0 text-xs font-bold',
                  bucket === '60+' ? 'bg-red-100 text-red-700' :
                    bucket === '31-60' ? 'bg-red-50 text-red-600' :
                      bucket === '16-30' ? 'bg-orange-50 text-orange-600' :
                        bucket === '8-15' ? 'bg-yellow-50 text-yellow-600' : 'bg-green-50 text-green-600')}>
                  <span className="text-lg leading-none">{order.daysOld}</span>
                  <span className="text-[8px]">days</span>
                </div>

                {/* Customer info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-medium text-sm text-gray-900 truncate">{order.customerName}</p>
                    <span className="text-[10px] text-gray-500">{order.orderNumber}</span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-gray-500 mt-0.5">
                    {order.customerPhone && <span className="flex items-center gap-1"><Phone className="w-3 h-3" />{order.customerPhone}</span>}
                    <span>{new Date(order.createdAt).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}</span>
                    <span>{order.items.length} items</span>
                  </div>
                </div>

                {/* Payment info */}
                <div className="text-right flex-shrink-0">
                  <p className="text-lg font-bold text-red-600">{fc(order.balanceDue)}</p>
                  <p className="text-[10px] text-gray-500">
                    of {fc(order.grandTotal)} · Paid {fc(order.amountPaid)}
                  </p>
                </div>

                {/* Action */}
                <a href={`tel:${order.customerPhone}`} className="p-2 text-gray-500 hover:text-bv-red-600 hover:bg-bv-gold-50 rounded-lg flex-shrink-0 print:hidden">
                  <Phone className="w-5 h-5" />
                </a>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
