// ============================================================================
// IMS 2.0 - Goods Receipt Note (GRN)  ·  v2 reskin (slice 2c)
// ============================================================================
// GRN creation, partial receipt, quality inspection, discrepancies.
// Reskinned to the v2 statutory-calm aesthetic (docs/design/inventory.html
// Receive GRN modal): inv-body shell, stat-strip, inv-tabs, 4-step receive
// stepper, card/tbl primitives, BV brand tokens. Same backend wiring + data
// flow (vendorsApi.getGRNs / createGRN). PO dropdown is wired to the real
// getPurchaseOrders API (empty state when none) -- no mock data.

import { useState, useEffect, useMemo, startTransition } from 'react';
import { Check, AlertCircle, Package, FileText, Printer, Loader2 } from 'lucide-react';
import clsx from 'clsx';
import { vendorsApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { GRNPrint } from '../../components/print/GRNPrint';

interface GRNLineItem {
  po_item_id: string;
  product_id: string;
  product_name: string;
  sku?: string;
  hsn_code?: string;
  po_qty: number;
  received_qty: number;
  inspection_status: 'pending' | 'passed' | 'failed';
}

interface GRNDiscrepancyItem {
  product_id: string;
  product_name?: string;
  received_qty: number;
  accepted_qty: number;
  rejected_qty: number;
  rejection_reason?: string;
}

interface GRN {
  id: string;
  grn_number: string;
  po_id: string;
  po_number: string;
  received_at: string;
  items_received: number;
  total_received: number;
  total_accepted: number;
  total_rejected: number;
  items: GRNDiscrepancyItem[];
  quality_status: 'passed' | 'failed' | 'conditional';
  created_by: string;
}

interface POOption {
  po_id: string;
  po_number: string;
  vendor_name?: string;
  status?: string;
  items?: Array<{
    po_item_id?: string;
    id?: string;
    product_id: string;
    product_name?: string;
    name?: string;
    sku?: string;
    hsn_code?: string;
    quantity?: number;
    qty?: number;
    ordered_qty?: number;
  }>;
}

// Normalise a raw GRN doc from the API into the shape the UI needs. The
// backend stores items + total_received/accepted/rejected and created_at;
// quality_status is derived from the real accept/reject totals rather than a
// (non-existent) stored field.
function transformGRN(grn: any): GRN {
  const items: GRNDiscrepancyItem[] = Array.isArray(grn.items) ? grn.items : [];
  const totalReceived =
    grn.total_received ?? items.reduce((s, i) => s + (i.received_qty || 0), 0);
  const totalAccepted =
    grn.total_accepted ?? items.reduce((s, i) => s + (i.accepted_qty || 0), 0);
  const totalRejected =
    grn.total_rejected ?? items.reduce((s, i) => s + (i.rejected_qty || 0), 0);
  return {
    id: grn.grn_id || grn.id || grn._id,
    grn_number: grn.grn_number,
    po_id: grn.po_id,
    po_number: grn.po_number || 'Unknown PO',
    received_at: grn.received_at || grn.created_at,
    items_received: grn.items_received ?? totalReceived,
    total_received: totalReceived,
    total_accepted: totalAccepted,
    total_rejected: totalRejected,
    items,
    quality_status:
      totalRejected === 0 ? 'passed' : totalAccepted === 0 ? 'failed' : 'conditional',
    created_by: grn.created_by || 'Unknown',
  };
}

const INSPECTION_CHECKLIST = [
  'Product packaging intact',
  'Expiry date valid (min 6 months)',
  'No visible damage or scratches',
  'Serial numbers match documentation',
  'Quantity matches PO',
  'Color/specification matches order',
  'Quality certifications present',
];

// Maps quality status to a v2 chip class.
const qualityChip = (status: string): string => {
  switch (status) {
    case 'passed':
      return 'ok';
    case 'failed':
      return 'err';
    case 'conditional':
      return 'warn';
    default:
      return '';
  }
};

export function GoodsReceiptNote() {
  const { user } = useAuth();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<'create' | 'history' | 'discrepancies'>('create');
  const [poNumber, setPoNumber] = useState('');
  const [receivedItems, setReceivedItems] = useState<GRNLineItem[]>([]);
  const [inspectionChecks, setInspectionChecks] = useState<Record<string, boolean>>({});
  const [qualityNotes, setQualityNotes] = useState('');
  const [vendorInvoiceNo, setVendorInvoiceNo] = useState('');
  const [discrepancies, setDiscrepancies] = useState('');
  // F9 — Delivery-Challan mode: lenses physically arrive with a DC; the tax
  // invoice comes later. In DC mode dc_number/dc_date are required and the
  // vendor invoice no. is optional.
  const [isDcMode, setIsDcMode] = useState(false);
  const [dcNumber, setDcNumber] = useState('');
  const [dcDate, setDcDate] = useState(new Date().toISOString().split('T')[0]);
  const [grns, setGrns] = useState<GRN[]>([]);
  const [pos, setPos] = useState<POOption[]>([]);
  const [, setIsLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // Print state: which GRN is currently being previewed (null = no print modal).
  const [printGrn, setPrintGrn] = useState<GRN | null>(null);

  const storeId = user?.activeStoreId || '';

  // Only POs that still have goods to receive belong in the receive picker.
  // A DRAFT PO hasn't been sent; a RECEIVED/CANCELLED one is closed. Keep the
  // legacy "PARTIAL" alias alongside the canonical "PARTIALLY_RECEIVED".
  const isReceivablePO = (status?: string) =>
    !status ||
    ['SENT', 'ACKNOWLEDGED', 'PARTIAL', 'PARTIALLY_RECEIVED'].includes(status);

  const fetchPurchaseOrders = async (): Promise<POOption[]> => {
    const poResp = await vendorsApi
      .getPurchaseOrders({ store_id: storeId })
      .catch(() => null);
    const poList: any[] = Array.isArray(poResp)
      ? poResp
      : poResp?.purchase_orders || poResp?.pos || poResp?.data || [];
    return poList
      .map((p: any) => ({
        po_id: p.po_id || p.id || p._id || '',
        po_number: p.po_number || p.po_id || 'PO',
        vendor_name: p.vendor_name || p.vendor?.trade_name || p.vendor?.legal_name,
        status: p.status,
        items: p.items || p.line_items || [],
      }))
      .filter((p: POOption) => isReceivablePO(p.status));
  };

  // Load GRNs + open purchase orders on mount / store change.
  useEffect(() => {
    const load = async () => {
      try {
        setIsLoading(true);
        const [grnResp, poList] = await Promise.all([
          vendorsApi.getGRNs({ store_id: storeId }),
          fetchPurchaseOrders(),
        ]);
        const grnList = Array.isArray(grnResp) ? grnResp : grnResp.grns || grnResp.data || [];
        setGrns(grnList.map(transformGRN));
        setPos(poList);
      } catch (error) {
        toast.error('Failed to load GRNs');
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, [storeId]);

  const reloadGrns = async () => {
    const response = await vendorsApi.getGRNs({ store_id: storeId });
    const grnList = Array.isArray(response) ? response : response.grns || response.data || [];
    setGrns(grnList.map(transformGRN));
  };

  const reloadPurchaseOrders = async () => {
    setPos(await fetchPurchaseOrders());
  };

  // When a PO is picked, hydrate the receive lines from its order items.
  const onSelectPO = (poId: string) => {
    setPoNumber(poId);
    const po = pos.find((p) => p.po_id === poId || p.po_number === poId);
    const lines = (po?.items || []).map((it) => {
      const qty = it.quantity ?? it.qty ?? it.ordered_qty ?? 0;
      return {
        po_item_id: it.po_item_id || it.id || it.product_id,
        product_id: it.product_id,
        product_name: it.product_name || it.name || it.product_id,
        sku: it.sku,
        hsn_code: it.hsn_code,
        po_qty: qty,
        received_qty: qty,
        inspection_status: 'pending' as const,
      };
    });
    setReceivedItems(lines);
  };

  const toggleInspectionCheck = (item: string) => {
    startTransition(() => {
      setInspectionChecks((prev) => ({ ...prev, [item]: !prev[item] }));
    });
  };

  const checksComplete = INSPECTION_CHECKLIST.filter((c) => inspectionChecks[c]).length;
  const allChecksComplete =
    checksComplete === INSPECTION_CHECKLIST.length && INSPECTION_CHECKLIST.length > 0;
  const qualityStatus = allChecksComplete && !discrepancies ? 'passed' : discrepancies ? 'failed' : 'conditional';

  // Receive totals (drives the stepper + totals strip).
  const totals = useMemo(() => {
    const ord = receivedItems.reduce((s, i) => s + i.po_qty, 0);
    const rec = receivedItems.reduce((s, i) => s + i.received_qty, 0);
    const short = receivedItems.reduce((s, i) => s + Math.max(0, i.po_qty - i.received_qty), 0);
    const over = receivedItems.reduce((s, i) => s + Math.max(0, i.received_qty - i.po_qty), 0);
    return { ord, rec, short, over };
  }, [receivedItems]);

  // 4-step receive flow state: PO -> lines -> inspection -> post.
  const steps = useMemo(() => {
    const hasPO = !!poNumber;
    const hasLines = receivedItems.length > 0;
    const inspected = allChecksComplete || !!discrepancies;
    return [
      { done: hasPO, active: !hasPO, t: 'Match PO & vendor invoice', s: poNumber ? poNumber : 'Pick the order being received' },
      { done: hasPO && hasLines, active: hasPO && !hasLines, t: 'Verify boxes & lines', s: hasLines ? `${receivedItems.length} lines` : 'No lines on this PO' },
      { done: inspected, active: hasLines && !inspected, t: 'Quality inspection', s: `${checksComplete}/${INSPECTION_CHECKLIST.length} checks` },
      { done: false, active: inspected && hasLines, t: 'Post & close', s: 'Stock ledger updated' },
    ];
  }, [poNumber, receivedItems.length, allChecksComplete, discrepancies, checksComplete]);

  const handleSubmit = async () => {
    if (!poNumber) {
      toast.error('Please select a Purchase Order');
      return;
    }
    if (receivedItems.length === 0) {
      toast.error('No line items to receive on this PO');
      return;
    }
    if (isDcMode && !dcNumber.trim()) {
      toast.error('DC Number is required for a Delivery Challan');
      return;
    }
    setSubmitting(true);
    try {
      // Step 1 — create the GRN doc (status PENDING). This records the receipt
      // + per-line accept/reject and stamps each line's short/exact/over flag.
      const created = await vendorsApi.createGRN({
        po_id: poNumber,
        // F9 — in DC mode the vendor invoice no. arrives later; send the
        // subtype + dc_number/dc_date so the bulk DC->invoice tally can pick it.
        grn_subtype: isDcMode ? 'DELIVERY_CHALLAN' : 'STANDARD',
        dc_number: isDcMode ? dcNumber.trim() : undefined,
        dc_date: isDcMode ? dcDate : undefined,
        vendor_invoice_no: vendorInvoiceNo || undefined,
        vendor_invoice_date: new Date().toISOString().split('T')[0],
        items: receivedItems.map((item) => ({
          po_item_id: item.po_item_id,
          product_id: item.product_id,
          received_qty: item.received_qty,
          accepted_qty: item.inspection_status === 'failed' ? 0 : item.received_qty,
          rejected_qty: item.inspection_status === 'failed' ? item.received_qty : 0,
          rejection_reason: item.inspection_status === 'failed' ? 'Quality inspection failed' : undefined,
        })),
        notes: qualityNotes || undefined,
      });

      // Step 2 — POST it: mint serialized stock into stock_units at this store
      // and advance the PO to partially/fully received. Posting is what makes
      // the goods sellable; a created-but-unposted GRN holds no stock.
      const grnId: string | undefined = created?.grn_id || created?.id;
      let posted: any = null;
      if (grnId) {
        try {
          posted = await vendorsApi.acceptGRN(grnId);
        } catch (postErr) {
          // The GRN doc was saved; only the stock-posting step failed. Tell the
          // user so they can retry the post from the History tab rather than
          // believing nothing happened.
          toast.warning(
            'GRN saved but stock posting failed. Retry from History — no stock was added.',
          );
          setActiveTab('history');
          await reloadGrns();
          return;
        }
      }

      const units = posted?.units_added ?? 0;
      const poState =
        posted?.po_status === 'RECEIVED'
          ? 'PO fully received'
          : posted?.po_status === 'PARTIALLY_RECEIVED'
            ? 'PO partially received'
            : '';
      toast.success(
        `GRN posted${units ? ` · ${units} unit${units === 1 ? '' : 's'} added to stock` : ''}${poState ? ` · ${poState}` : ''}`,
      );

      setActiveTab('history');
      setPoNumber('');
      setReceivedItems([]);
      setInspectionChecks({});
      setQualityNotes('');
      setVendorInvoiceNo('');
      setDiscrepancies('');
      setIsDcMode(false);
      setDcNumber('');
      // Posting changes which POs are still receivable — refresh both lists.
      await Promise.all([reloadGrns(), reloadPurchaseOrders()]);
    } catch (err) {
      toast.error('Failed to create GRN');
    } finally {
      setSubmitting(false);
    }
  };

  const tabs: Array<[typeof activeTab, string, number]> = [
    ['create', 'Create GRN', 0],
    ['history', 'History', grns.length],
    [
      'discrepancies',
      'Discrepancies',
      grns.filter((g) => g.total_rejected > 0 || g.total_received !== g.total_accepted).length,
    ],
  ];

  // Build the GRNPrint-compatible data shape from a history GRN record.
  const buildGrnPrintData = (grn: GRN) => ({
    grn_id: grn.id,
    grn_number: grn.grn_number,
    grn_date: grn.received_at,
    po_number: grn.po_number,
    vendor_id: '',
    vendor_name: '',
    vendor_address: '',
    vendor_gstin: '',
    items: grn.items.map((item) => ({
      product_id: item.product_id,
      product_name: item.product_name ?? item.product_id,
      hsn_code: undefined,
      ordered_qty: item.accepted_qty + item.rejected_qty,
      received_qty: item.received_qty,
      variance: item.received_qty - (item.accepted_qty + item.rejected_qty),
      remarks: item.rejection_reason,
    })),
    quality_inspection:
      grn.quality_status === 'passed'
        ? ('accepted' as const)
        : grn.quality_status === 'failed'
          ? ('rejected' as const)
          : ('partially_accepted' as const),
    inspection_remarks: undefined,
  });

  // storeName is not available on the User JWT shape; use a static fallback
  // until store-detail fetch is wired here.
  const storeInfo = {
    storeName: 'Better Vision Opticals',
    address: '',
    city: '',
    state: '',
    pincode: '',
  };

  return (
    <>
      {/* GRN print modal — rendered when user clicks Print on a history row */}
      {printGrn && (
        <GRNPrint
          grn={buildGrnPrintData(printGrn)}
          store={storeInfo}
          onClose={() => setPrintGrn(null)}
        />
      )}
    <div className="inv-body">
      {/* Header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Purchase · inward</div>
          <h1>Goods received.</h1>
          <p className="text-sm text-ink-4 mt-1" style={{ color: 'var(--ink-4)' }}>
            Record item receipt against a PO with quality inspection and placement.
          </p>
        </div>
      </div>

      {/* Summary stat strip */}
      <div className="stat-strip">
        <div>
          <div className="l">Total GRNs</div>
          <div className="v">{grns.length}</div>
          <div className="d">all stores in scope</div>
        </div>
        <div>
          <div className="l">Quality passed</div>
          <div className="v" style={{ color: 'var(--ok)' }}>
            {grns.filter((g) => g.quality_status === 'passed').length}
          </div>
          <div className="d good">clean receipts</div>
        </div>
        <div>
          <div className="l">Conditional</div>
          <div className="v" style={{ color: 'var(--warn)' }}>
            {grns.filter((g) => g.quality_status === 'conditional').length}
          </div>
          <div className="d warn">partial accept</div>
        </div>
        <div>
          <div className="l">Failed quality</div>
          <div className="v" style={{ color: 'var(--err)' }}>
            {grns.filter((g) => g.quality_status === 'failed').length}
          </div>
          <div className="d bad">debit note raised</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="inv-tabs">
        {tabs.map(([tab, label, count]) => (
          <button
            key={tab}
            className={activeTab === tab ? 'on' : ''}
            onClick={() => startTransition(() => setActiveTab(tab))}
          >
            {label}
            {count > 0 && <span className="count">· {count}</span>}
          </button>
        ))}
      </div>

      {/* ─────────────────────────── CREATE ─────────────────────────── */}
      {activeTab === 'create' && (
        <div className="space-y-4">
          {/* Receive stepper */}
          <div className="grn-stepper">
            {steps.map((st, i) => (
              <div key={i} className={st.done ? 'done' : st.active ? 'active' : ''}>
                <div className="n">{st.done ? '✓' : i + 1}</div>
                <div>
                  <div className="t">{st.t}</div>
                  <div className="s">{st.s}</div>
                </div>
              </div>
            ))}
          </div>

          {/* PO selection */}
          <div className="card">
            <div className="card-head">
              <h3>Select purchase order</h3>
              <span className="meta">
                {isDcMode ? 'DC · invoice arrives later' : 'PO precedes GRN · GRN is the GST document'}
              </span>
            </div>
            <div className="card-body">
              {/* F9 — Delivery-Challan mode toggle. Light/restrained: a plain
                  checkbox row, no colour-coded flag. */}
              <label
                className="flex items-center gap-2 mb-4 text-sm cursor-pointer"
                style={{ color: 'var(--ink-3)' }}
              >
                <input
                  type="checkbox"
                  checked={isDcMode}
                  onChange={(e) => setIsDcMode(e.target.checked)}
                />
                This is a Delivery Challan (no invoice yet)
              </label>
              {isDcMode && (
                <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4 mb-4">
                  <div>
                    <label className="block text-xs font-medium mb-1" style={{ color: 'var(--ink-4)' }}>
                      DC Number
                    </label>
                    <input
                      type="text"
                      value={dcNumber}
                      onChange={(e) => startTransition(() => setDcNumber(e.target.value))}
                      placeholder="e.g. DC/26/05/118"
                      className="input w-full"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium mb-1" style={{ color: 'var(--ink-4)' }}>
                      DC Date
                    </label>
                    <input
                      type="date"
                      value={dcDate}
                      onChange={(e) => setDcDate(e.target.value)}
                      className="input w-full"
                    />
                  </div>
                </div>
              )}
              {pos.length === 0 ? (
                <div className="text-center py-6" style={{ color: 'var(--ink-4)', fontSize: 13 }}>
                  <Package className="w-8 h-8 mx-auto mb-2" style={{ color: 'var(--ink-5)' }} />
                  No open purchase orders to receive against.
                  <div className="text-xs mt-1" style={{ color: 'var(--ink-5)' }}>
                    Create or send a PO from Purchase Management first.
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-medium mb-1" style={{ color: 'var(--ink-4)' }}>
                      Purchase order
                    </label>
                    <select value={poNumber} onChange={(e) => onSelectPO(e.target.value)} className="input w-full">
                      <option value="">Select a PO…</option>
                      {pos.map((p) => (
                        <option key={p.po_id} value={p.po_id}>
                          {p.po_number}
                          {p.vendor_name ? ` · ${p.vendor_name}` : ''}
                          {p.status === 'PARTIALLY_RECEIVED' || p.status === 'PARTIAL'
                            ? ' · partially received'
                            : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium mb-1" style={{ color: 'var(--ink-4)' }}>
                      {isDcMode ? 'Vendor invoice no. (optional — arrives later)' : 'Vendor invoice no.'}
                    </label>
                    <input
                      type="text"
                      value={vendorInvoiceNo}
                      onChange={(e) => startTransition(() => setVendorInvoiceNo(e.target.value))}
                      placeholder="e.g. JJ/24/04/2240"
                      className="input w-full"
                    />
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Items reception */}
          {receivedItems.length > 0 && (
            <div className="card">
              <div className="card-head">
                <h3 className="flex items-center gap-2">
                  <Package className="w-4 h-4" /> Line items · received quantity
                </h3>
                <span className="meta">
                  {totals.rec} / {totals.ord} units · auto-detects shortages
                </span>
              </div>
              <div className="overflow-x-auto -mx-4 sm:mx-0">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Item</th>
                    <th className="right">Ordered</th>
                    <th className="right">Received</th>
                    <th className="right">Δ</th>
                    <th>QA</th>
                  </tr>
                </thead>
                <tbody>
                  {receivedItems.map((item, idx) => {
                    const variance = item.received_qty - item.po_qty;
                    return (
                      <tr key={item.po_item_id || idx}>
                        <td className="mono" style={{ color: 'var(--ink-4)' }}>{idx + 1}</td>
                        <td>
                          <div className="font-medium" style={{ color: 'var(--ink)' }}>{item.product_name}</div>
                          <div className="mono text-xs" style={{ color: 'var(--ink-4)' }}>
                            {item.sku || item.product_id}
                            {item.hsn_code ? ` · HSN ${item.hsn_code}` : ''}
                          </div>
                        </td>
                        <td className="right mono">{item.po_qty}</td>
                        <td className="right">
                          <input
                            type="number"
                            min={0}
                            value={item.received_qty}
                            onChange={(e) =>
                              startTransition(() => {
                                const v = Math.max(0, parseInt(e.target.value) || 0);
                                setReceivedItems((arr) =>
                                  arr.map((r, j) => (j === idx ? { ...r, received_qty: v } : r)),
                                );
                              })
                            }
                            className="input"
                            style={{ width: 64, textAlign: 'right', height: 30, padding: '0 6px' }}
                          />
                        </td>
                        <td className="right">
                          {variance === 0 ? (
                            <span style={{ color: 'var(--ink-4)' }}>—</span>
                          ) : (
                            <span className={clsx('chip', variance < 0 ? 'warn' : 'err')}>
                              {variance > 0 ? `+${variance}` : variance}
                            </span>
                          )}
                        </td>
                        <td>
                          <select
                            value={item.inspection_status}
                            onChange={(e) =>
                              startTransition(() => {
                                const v = e.target.value as GRNLineItem['inspection_status'];
                                setReceivedItems((arr) =>
                                  arr.map((r, j) => (j === idx ? { ...r, inspection_status: v } : r)),
                                );
                              })
                            }
                            className="input"
                            style={{ height: 30, padding: '0 6px', maxWidth: 130 }}
                          >
                            <option value="pending">Pending</option>
                            <option value="passed">Passed</option>
                            <option value="failed">Failed</option>
                          </select>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              </div>
              {/* Totals strip */}
              <div className="grn-totals" style={{ borderTop: '1px solid var(--line)' }}>
                <div>
                  <div className="l">Lines</div>
                  <div className="v">{receivedItems.length}</div>
                  <div className="d">matched against PO</div>
                </div>
                <div>
                  <div className="l">Ordered → received</div>
                  <div className="v">
                    {totals.rec} <span style={{ fontSize: 13, color: 'var(--ink-4)' }}>/ {totals.ord}</span>
                  </div>
                  <div className="d" style={{ color: totals.short ? 'var(--err)' : 'var(--ok)' }}>
                    {totals.short ? `${totals.short} short · raise debit note` : 'No shortage'}
                  </div>
                </div>
                <div>
                  <div className="l">Over-receipt</div>
                  <div className="v" style={{ color: totals.over ? 'var(--warn)' : 'var(--ink)' }}>{totals.over}</div>
                  <div className="d">{totals.over ? 'excess vs PO' : 'within PO'}</div>
                </div>
                <div>
                  <div className="l">QA status</div>
                  <div className="v" style={{ fontSize: 18 }}>
                    <span
                      className={clsx('chip', qualityChip(qualityStatus))}
                      style={{ fontSize: 12, verticalAlign: 'middle' }}
                    >
                      {qualityStatus.charAt(0).toUpperCase() + qualityStatus.slice(1)}
                    </span>
                  </div>
                  <div className="d">{checksComplete}/{INSPECTION_CHECKLIST.length} checks done</div>
                </div>
              </div>
            </div>
          )}

          {/* Quality inspection */}
          {receivedItems.length > 0 && (
            <div className="card">
              <div className="card-head">
                <h3 className="flex items-center gap-2">
                  <Check className="w-4 h-4" /> Quality inspection checklist
                </h3>
                <span className="meta">{checksComplete}/{INSPECTION_CHECKLIST.length} complete</span>
              </div>
              <div className="card-body">
                <div className="grid grid-cols-1 tablet:grid-cols-2 gap-x-6 gap-y-2 mb-4">
                  {INSPECTION_CHECKLIST.map((item) => (
                    <label key={item} className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={inspectionChecks[item] || false}
                        onChange={() => toggleInspectionCheck(item)}
                        className="w-4 h-4 rounded"
                        style={{ accentColor: 'var(--bv)' }}
                      />
                      <span className="text-sm" style={{ color: 'var(--ink-2)' }}>{item}</span>
                    </label>
                  ))}
                </div>

                <div className="mb-4">
                  <label className="block text-xs font-medium mb-1" style={{ color: 'var(--ink-4)' }}>
                    Quality notes
                  </label>
                  <textarea
                    value={qualityNotes}
                    onChange={(e) => startTransition(() => setQualityNotes(e.target.value))}
                    placeholder="Add any observations during inspection…"
                    className="input w-full resize-none"
                    rows={2}
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium mb-1" style={{ color: 'var(--ink-4)' }}>
                    Discrepancies found
                  </label>
                  <textarea
                    value={discrepancies}
                    onChange={(e) => startTransition(() => setDiscrepancies(e.target.value))}
                    placeholder="List any damaged items, missing items, or other discrepancies…"
                    className="input w-full resize-none"
                    rows={2}
                  />
                </div>
              </div>
            </div>
          )}

          {/* Footer actions */}
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-xs" style={{ color: 'var(--ink-4)' }}>
              Posting against {poNumber || 'the selected PO'} adds {totals.rec} unit{totals.rec === 1 ? '' : 's'} to this store&rsquo;s stock · the PO is marked partially / fully received · variance raises a debit note.
            </span>
            <span className="flex-1" />
            <button
              type="button"
              className="btn"
              onClick={() => window.print()}
              disabled={receivedItems.length === 0}
            >
              <Printer className="w-4 h-4" /> Print preview
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={submitting || !poNumber || receivedItems.length === 0}
              className="btn accent"
            >
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
              Post GRN
            </button>
          </div>
        </div>
      )}

      {/* ─────────────────────────── HISTORY ─────────────────────────── */}
      {activeTab === 'history' && (
        <div className="space-y-3">
          {grns.length === 0 ? (
            <div className="card text-center py-12" style={{ color: 'var(--ink-4)' }}>
              <FileText className="w-10 h-10 mx-auto mb-3" style={{ color: 'var(--ink-5)' }} />
              <p className="font-medium">No goods receipt notes yet</p>
              <p className="text-sm mt-1" style={{ color: 'var(--ink-5)' }}>
                Receive a purchase order to create the first GRN.
              </p>
            </div>
          ) : (
            grns.map((grn) => (
              <div key={grn.id} className="card">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <p className="font-semibold mono" style={{ color: 'var(--ink)' }}>{grn.grn_number}</p>
                    <p className="text-sm" style={{ color: 'var(--ink-4)' }}>Against {grn.po_number}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={clsx('chip', qualityChip(grn.quality_status))}>
                      {grn.quality_status.charAt(0).toUpperCase() + grn.quality_status.slice(1)}
                    </span>
                    <button
                      type="button"
                      className="btn sm"
                      onClick={() => setPrintGrn(grn)}
                      title="Print GRN"
                    >
                      <Printer className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                <div className="grn-totals" style={{ border: '1px solid var(--line)', borderRadius: 'var(--r-md)' }}>
                  <div>
                    <div className="l">Items received</div>
                    <div className="v" style={{ fontSize: 18 }}>{grn.items_received}</div>
                  </div>
                  <div>
                    <div className="l">Received date</div>
                    <div className="v" style={{ fontSize: 14 }}>
                      {grn.received_at ? new Date(grn.received_at).toLocaleDateString('en-IN') : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="l">Accept / reject</div>
                    <div className="v" style={{ fontSize: 14 }}>
                      {grn.total_accepted} / {grn.total_rejected}
                    </div>
                  </div>
                  <div>
                    <div className="l">Received by</div>
                    <div className="v" style={{ fontSize: 14 }}>{grn.created_by}</div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* ─────────────────────────── DISCREPANCIES ─────────────────────────── */}
      {activeTab === 'discrepancies' && (
        <div className="space-y-3">
          <div
            className="flex items-start gap-3 rounded-lg p-4"
            style={{ background: 'var(--bv-soft)', border: '1px solid var(--bv-50)' }}
          >
            <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" style={{ color: 'var(--bv)' }} />
            <div>
              <p className="font-semibold" style={{ color: 'var(--ink)' }}>Discrepancy report</p>
              <p className="text-sm mt-1" style={{ color: 'var(--ink-3)' }}>
                Items with variance between PO quantity and received quantity, or quality inspection failures.
              </p>
            </div>
          </div>

          {(() => {
            const discrepant = grns.filter(
              (g) => g.total_rejected > 0 || g.total_received !== g.total_accepted,
            );
            if (discrepant.length === 0) {
              return (
                <div className="card text-center py-8" style={{ color: 'var(--ink-4)' }}>
                  No discrepancies — all received goods matched their POs and passed inspection.
                </div>
              );
            }
            return discrepant.map((g) => {
              const isQualityFail = g.total_rejected > 0;
              const rejectedItems = g.items.filter((i) => (i.rejected_qty || 0) > 0);
              return (
                <div key={g.id} className="card">
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <p className="font-semibold mono" style={{ color: 'var(--ink)' }}>{g.grn_number}</p>
                      <p className="text-sm" style={{ color: 'var(--ink-4)' }}>Against {g.po_number}</p>
                    </div>
                    <span className={clsx('chip', isQualityFail ? 'err' : 'warn')}>
                      {isQualityFail ? 'Quality rejection' : 'Quantity variance'}
                    </span>
                  </div>
                  <p className="text-sm" style={{ color: 'var(--ink-3)' }}>
                    Received: {g.total_received} · Accepted: {g.total_accepted} · Rejected: {g.total_rejected}
                  </p>
                  {rejectedItems.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {rejectedItems.map((i, idx) => (
                        <li key={idx} className="text-xs" style={{ color: 'var(--err)' }}>
                          {i.product_name || i.product_id}: {i.rejected_qty} rejected
                          {i.rejection_reason ? ` — ${i.rejection_reason}` : ''}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            });
          })()}
        </div>
      )}
    </div>
    </>
  );
}

export default GoodsReceiptNote;
