// ============================================================================
// IMS 2.0 — Returns, Exchanges & Credit Notes
// ============================================================================
import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../context/AuthContext';
import { orderApi, productApi } from '../../services/api';
import {
  returnsApi,
  type CreateReturnPayload,
  type RefundTenderCode,
  type RefundTenderMethod,
  type ReturnQuote,
} from '../../services/api/returns';
import { RefundApprovalModal } from '../../components/returns/RefundApprovalModal';
import { formatDateIST } from '../../utils/datetime';
import {
  Search, RotateCcw, ArrowLeftRight, Receipt,
  AlertTriangle, CheckCircle, X, ChevronRight, Plus, Trash2, RefreshCw,

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
  // NET (pre-GST) unit price from the order line — sent to the server as-is.
  unitPrice: number;
  // Per-unit GST-INCLUSIVE gross actually billed, computed exactly as the
  // backend does: (taxable_value + tax_amount) / quantity. Display only; the
  // authoritative refund figure always comes from the server quote.
  billedUnitGross: number;
  // GST rate (%) the line was billed at (display + server hint).
  gstRate: number;
  reason: ReturnReason;
  notes: string;
  condition: 'GOOD' | 'DAMAGED' | 'OPENED';
}

/**
 * Per-unit GST-INCLUSIVE gross for an order line, mirroring the backend's
 * `_billed_unit_gross`: (taxable_value + tax_amount) / quantity. Correct for
 * BOTH inclusive orders (taxable + tax == gross) and legacy exclusive ones.
 * Falls back to the line's stored gross/unit price when those fields are
 * absent — and NEVER re-applies GST to an already-inclusive price.
 */
function billedUnitGross(item: any): number {
  const tv = item?.taxable_value ?? item?.taxableValue;
  const tx = item?.tax_amount ?? item?.taxAmount;
  const qty = Number(item?.quantity ?? 1);
  if (tv != null && tx != null && qty > 0) {
    return Math.round(((Number(tv) + Number(tx)) / qty) * 100) / 100;
  }
  const lineGross = item?.final_price ?? item?.finalPrice ?? item?.item_total ?? item?.line_total;
  if (lineGross != null && qty > 0) return Math.round((Number(lineGross) / qty) * 100) / 100;
  return Number(item?.unitPrice ?? item?.unit_price ?? 0);
}

/**
 * The replacement unit price to SEND, straight from the catalog and UNROUNDED.
 *
 * Math.round() here was a real refusal: the server treats the catalog price as
 * an exact ceiling, so a catalog 4241.50 rounded to 4242 came back
 * "above the catalog price Rs 4241.50" for a cashier who picked from search and
 * typed nothing. Display may round; the wire may not.
 */
export function replacementUnitPrice(p: any): number {
  const raw = p?.offer_price ?? p?.price ?? p?.mrp ?? 0;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/**
 * Build a replacement line from a CATALOG product, or null when the product
 * carries no id.
 *
 * A line without a product_id can never be priced server-side (the exchange
 * difference feeds the cash drawer, so a typed price is a drawer input), which
 * is why the old free-text "Manual" button was a dead control: every payload it
 * produced was refused pre-claim. Returning null keeps that impossible line
 * unconstructable rather than letting the UI offer it.
 */
export function buildReplacementLine(p: any): ReplacementItem | null {
  const productId = p?.product_id || p?.id || p?._id;
  if (!productId) return null;
  return {
    productId,
    name: p?.name || p?.model || p?.product_name || 'Item',
    sku: p?.sku || '',
    quantity: 1,
    unitPrice: replacementUnitPrice(p),
  };
}

interface ReplacementItem {
  productId?: string;
  name: string;
  sku: string;
  quantity: number;
  unitPrice: number;
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

// Map a per-line return reason to the F27 matrix reason code (DEFECTIVE /
// CHANGE_OF_MIND / PRICE_MATCH / GOODWILL drive the tier bump). Anything not a
// clear defect is treated as a change-of-mind for the matrix.
const MATRIX_REASON: Record<ReturnReason, string> = {
  WRONG_PRODUCT: 'DEFECTIVE',
  DEFECTIVE: 'DEFECTIVE',
  POWER_MISMATCH: 'DEFECTIVE',
  SIZE_ISSUE: 'CHANGE_OF_MIND',
  CUSTOMER_CHANGED_MIND: 'CHANGE_OF_MIND',
  DAMAGED_IN_STORE: 'CHANGE_OF_MIND',
  OTHER: 'CHANGE_OF_MIND',
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
  const [restockingFee, setRestockingFee] = useState(0);
  const [resultId, setResultId] = useState<string | null>(null);
  // THE SERVER'S answer about whether Day-End will auto-deduct this refund.
  // The old banner keyed on the LOCAL refundTenders array, so on an order the
  // server could not verify (a Shopify-paid order: refund_tenders persisted
  // None, drawer nets Rs 0) the screen still told the cashier NOT to record the
  // payout -- a guaranteed false shortage blamed on them at close.
  const [resultDrawerNetted, setResultDrawerNetted] = useState<boolean | null>(null);
  const [resultCashRefunded, setResultCashRefunded] = useState(0);
  const [resultCreditAmount, setResultCreditAmount] = useState(0);
  const [resultCollectAmount, setResultCollectAmount] = useState(0);
  const [resultCollectMethod, setResultCollectMethod] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Refund-tender capture (drawer-truth): how the money is actually handed back.
  // The Day-End drawer nets off THIS, never a guessed original-sale tender.
  // `method: ''` = not yet chosen; it is NEVER defaulted to CASH.
  const [refundTenders, setRefundTenders] = useState<{ method: RefundTenderMethod | ''; amount: number }[]>([]);
  // EXCHANGE COLLECT: the tender the price difference is taken in at the till.
  // Starts UNSET — an untouched dropdown must never stamp phantom drawer cash.
  const [collectMethod, setCollectMethod] = useState<RefundTenderCode | '' | 'NOT_AT_TILL'>('');
  // Irreversible-refund confirmation dialog (UI P0: no submit without a confirm).
  const [showConfirm, setShowConfirm] = useState(false);
  // AUTHORITATIVE server money quote for the current selection.
  const [quote, setQuote] = useState<ReturnQuote | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState<string | null>(null);

  // EXCHANGE: replacement product picker state
  const [replacementItems, setReplacementItems] = useState<ReplacementItem[]>([]);
  const [productQuery, setProductQuery] = useState('');
  const [productResults, setProductResults] = useState<any[]>([]);
  const [productSearching, setProductSearching] = useState(false);

  // New-return wizard vs. history list of past returns.
  const [mode, setMode] = useState<'new' | 'history'>('new');
  const [history, setHistory] = useState<any[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // F27 refund-approval gate: when the server demands a tiered sign-off this
  // holds the context for the request+poll modal.
  const [approvalGate, setApprovalGate] = useState<{
    amount: number;
    reason?: string;
    requiredTier?: string;
  } | null>(null);

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
        // NET unit price as billed (sent to the server unchanged — the server
        // re-resolves the authoritative billed gross from the original line).
        unitPrice: Number(item.unitPrice ?? item.unit_price ?? 0),
        // Per-unit GST-INCLUSIVE gross the customer actually paid, derived the
        // SAME way the backend does: (taxable_value + tax_amount) / quantity.
        // NEVER re-gross-up unit_price — under GST_PRICING_MODE=INCLUSIVE that
        // double-applies GST (a Rs 5,900 line displayed as Rs 6,962) and the
        // tender split then fails the server's paise-exact balance check.
        billedUnitGross: billedUnitGross(item),
        // Rate the line was billed at (stamped on the order item); display +
        // hint only — the server prefers the rate on the original order line.
        gstRate: Number(item.gst_rate ?? item.gstRate ?? 0),
        reason: 'CUSTOMER_CHANGED_MIND' as ReturnReason,
        notes: '',
        condition: 'GOOD' as const,
      }))
    );
    setRestockingFee(0);
    setStep('select');
  };

  const updateReturnItem = (index: number, updates: Partial<ReturnItem>) => {
    setReturnItems(prev => prev.map((item, i) => i === index ? { ...item, ...updates } : item));
  };

  const activeReturns = returnItems.filter(i => i.returnQty > 0);
  // LOCAL ESTIMATE ONLY (instant feedback while the server quote is in flight).
  // It uses the billed gross the backend itself stores — it never re-applies
  // GST — but the AUTHORITATIVE figure is always `quote.net_refund` below.
  const estimatedGross =
    Math.round(activeReturns.reduce((sum, i) => sum + i.returnQty * i.billedUnitGross, 0) * 100) / 100;
  const safeFee = Math.max(0, Math.min(restockingFee || 0, estimatedGross));

  // THE authoritative money figures come from the server (POST /returns/quote),
  // computed by the very same code path that will price the POST. Prefilling
  // the tender picker from a client-side amount is what made a Rs 5,900 refund
  // display as Rs 6,962 and 400 on submit with no way for the cashier to
  // recover; the client is no longer authoritative for any rupee figure.
  const totalRefund = quote ? quote.gross_refund : estimatedGross;
  const netRefund = quote ? quote.net_refund : Math.max(0, estimatedGross - safeFee);

  // Exchange settlement: server-computed when quoted, local estimate otherwise.
  // The SERVER re-prices replacement lines from the product master (a typed
  // price would be a cash-drawer input via the COLLECT). Show the resolved
  // figures once the quote lands; the local sum is a pre-quote estimate only.
  const replacementTotal = quote?.replacement_items_priced?.length
    ? Math.round(
        quote.replacement_items_priced.reduce(
          (sum, r) => sum + (Number(r.quantity) || 1) * (Number(r.unit_price) || 0), 0,
        ) * 100,
      ) / 100
    : replacementItems.reduce((sum, r) => sum + r.quantity * r.unitPrice, 0);
  const exchangeDiff = quote?.settlement
    ? (quote.settlement.direction === 'REFUND'
        ? -Math.abs(quote.settlement.difference)
        : Math.abs(quote.settlement.difference))
    : Math.round((replacementTotal - totalRefund) * 100) / 100;
  const exchangeDirection: 'COLLECT' | 'REFUND' | 'EVEN' =
    quote?.settlement
      ? quote.settlement.direction
      : Math.abs(exchangeDiff) < 0.005 ? 'EVEN' : exchangeDiff > 0 ? 'COLLECT' : 'REFUND';

  // Ask the SERVER for the authoritative amounts whenever the selection changes.
  // Debounced so dragging a qty spinner does not spam the endpoint. The quote is
  // read-only (no reservation, no write) and is the single source of truth for
  // every rupee figure shown and submitted on this screen.
  const quoteKey = JSON.stringify({
    o: selectedOrder?.id || selectedOrder?.order_id || selectedOrder?.orderId,
    t: returnType,
    f: safeFee,
    l: activeReturns.map(i => [i.orderItemId, i.returnQty, i.unitPrice, i.gstRate]),
    r: returnType === 'EXCHANGE' ? replacementItems.map(r => [r.productId, r.quantity, r.unitPrice]) : null,
  });
  useEffect(() => {
    if (step !== 'select' || !selectedOrder || activeReturns.length === 0) {
      setQuote(null);
      setQuoteError(null);
      return;
    }
    let cancelled = false;
    setQuoteLoading(true);
    const timer = setTimeout(async () => {
      try {
        const q = await returnsApi.quote(buildPayload());
        if (!cancelled) { setQuote(q); setQuoteError(null); }
      } catch (e: any) {
        if (!cancelled) {
          setQuote(null);
          const d = e?.response?.data?.detail;
          setQuoteError(typeof d === 'string' ? d : 'Could not price this return. Check the selected items.');
        }
      } finally {
        if (!cancelled) setQuoteLoading(false);
      }
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quoteKey, step]);

  // ----- Refund-tender capture (drawer-truth) -----
  // Guessing the refund tender from the original sale regresses split-tender
  // days, so the cashier records how the refund is ACTUALLY returned — and the
  // server independently enforces that a tender may only be refunded up to what
  // it collected on this order (a UI rule is not a control).
  const TENDER_CODES: RefundTenderMethod[] = ['CASH', 'UPI', 'CARD', 'BANK'];

  // Tenders the SERVER says this order captured (authoritative). Only these can
  // legitimately receive a refund; anything else is refused server-side.
  const capturedTenders = quote?.captured_tenders ?? {};
  const refundableByTender = quote?.refundable_by_tender ?? {};
  const nonRefundableTenders = quote?.non_refundable_tenders ?? {};
  // Offerable tenders = the captured cash-in ones, plus STORE_CREDIT when the
  // sale was part-paid with an instrument that cannot come back as cash. Without
  // that option a part-voucher refund is UN-COMPLETABLE: every cash-in-only
  // split is rejected and the cashier's next instinct (a second CASH row) used
  // to over-net the drawer.
  const sourceTenders = Object.keys(refundableByTender).filter(
    (t) => (refundableByTender[t] ?? 0) > 0,
  ) as RefundTenderMethod[];
  const cashInShortfall = quote?.cash_in_shortfall ?? false;
  // SPLIT is judged on the RAW captured payment legs, not on the mappable
  // subset: a GIFT_VOUCHER / LOYALTY / EMI leg (or an empty payments[]) must
  // never look like a clean single-tender sale and silently prefill CASH.
  const rawPaymentLegs = ((selectedOrder?.payments as any[]) || []).length;
  const isSingleSource = sourceTenders.length === 1 && rawPaymentLegs <= 1;
  const tendersUnverifiable = quote?.tenders_unverifiable ?? false;
  const roundedNet = Math.round(netRefund * 100) / 100;

  // (Re)prefill the picker from the SERVER quote. Exactly one captured tender
  // and one payment leg -> prefill that tender with the server's net (editable).
  // Anything else (split, unmappable legs, unverifiable order) -> seed a row
  // with NO tender preselected so the cashier must choose. There is deliberately
  // no `|| 'CASH'` fallback anywhere: a silent CASH default fabricates drawer
  // movement from an untouched screen.
  useEffect(() => {
    if (returnType !== 'RETURN' || roundedNet <= 0) {
      setRefundTenders([]);
      return;
    }
    if (isSingleSource) {
      setRefundTenders([{ method: sourceTenders[0], amount: roundedNet }]);
    } else {
      setRefundTenders((prev) => (prev.length ? prev : [{ method: '', amount: 0 }]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrder, returnType, roundedNet, isSingleSource, quote]);

  const refundTenderTotal = Math.round(
    refundTenders.reduce((s, t) => s + (Number(t.amount) || 0), 0) * 100,
  ) / 100;
  // PAISE-EXACT, matching the server's own 0.01 tolerance. A Re 1.00 client
  // tolerance showed a green "balanced" split that the server then 400'd.
  const TENDER_BALANCE_EPSILON = 0.01;
  const refundTendersBalanced =
    Math.abs(refundTenderTotal - roundedNet) <= TENDER_BALANCE_EPSILON &&
    refundTenders.every((t) => t.method !== '' && (Number(t.amount) || 0) >= 0);
  // THE ESCAPE. When the server says a complete verifiable split is impossible
  // (walk-in part-voucher, gateway + counter cash, imported order), an untouched
  // picker is a VALID submission: the refund is recorded and simply not
  // auto-deducted. Without this the Review button stayed disabled forever and
  // the cashier had no way to record the refund at all — which is how a
  // Rs 5,900 payout ended up recorded nowhere and the till read short.
  const splitUsable = refundTenders.length > 0 && refundTendersBalanced;
  const escapeAllowed =
    tendersUnverifiable &&
    refundTenders.every((t) => t.method === '' || (Number(t.amount) || 0) === 0);
  // A RETURN with a positive net needs a balanced, fully-specified breakdown (or
  // the escape above); an EXCHANGE-COLLECT needs an explicit collect tender.
  // Nothing submits until the server quote has landed (the amounts must be the
  // server's).
  const refundTendersReady =
    !!quote &&
    (returnType !== 'RETURN' || roundedNet <= 0 || splitUsable || escapeAllowed) &&
    (returnType !== 'EXCHANGE' || exchangeDirection !== 'COLLECT' || collectMethod !== '');

  const addRefundTenderRow = () =>
    setRefundTenders((prev) => [...prev, { method: '', amount: 0 }]);
  const removeRefundTenderRow = (i: number) =>
    setRefundTenders((prev) => prev.filter((_, idx) => idx !== i));
  const updateRefundTenderRow = (
    i: number,
    patch: Partial<{ method: RefundTenderMethod; amount: number }>,
  ) => setRefundTenders((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  // ----- Replacement product picker (EXCHANGE only) -----
  const searchProducts = async () => {
    if (!productQuery.trim()) return;
    setProductSearching(true);
    try {
      const response = await productApi.searchProducts(productQuery.trim());
      const list = response.products || response.data || response || [];
      setProductResults(Array.isArray(list) ? list.slice(0, 10) : []);
    } catch {
      setProductResults([]);
    } finally {
      setProductSearching(false);
    }
  };

  const addReplacementFromProduct = (p: any) => {
    const line = buildReplacementLine(p);
    if (!line) return;   // uncatalogued -> unpriceable server-side; never added
    // DEDUPE. Two clicks on the same frame used to append two lines, and the
    // per-line quantity cap did not see the total, so the exchange collected
    // double and the drawer moved with it. Bump the quantity instead.
    setReplacementItems(prev => {
      const at = prev.findIndex(r => r.productId && r.productId === line.productId);
      if (at >= 0) {
        return prev.map((r, i) => (i === at ? { ...r, quantity: r.quantity + 1 } : r));
      }
      return [...prev, line];
    });
    setProductResults([]);
    setProductQuery('');
  };

  const updateReplacement = (index: number, updates: Partial<ReplacementItem>) => {
    setReplacementItems(prev => prev.map((r, i) => (i === index ? { ...r, ...updates } : r)));
  };

  const removeReplacement = (index: number) => {
    setReplacementItems(prev => prev.filter((_, i) => i !== index));
  };

  // Dominant matrix reason across the returned lines (drives the F27 tier).
  const dominantMatrixReason = (): string => {
    const first = activeReturns[0]?.reason as ReturnReason | undefined;
    return (first && MATRIX_REASON[first]) || 'CHANGE_OF_MIND';
  };

  // ONE payload builder for both the read-only quote and the real POST, so the
  // priced preview and the submitted return can never describe different money.
  const buildPayload = (
    approval?: { requestId: string; approvalToken?: string },
  ): CreateReturnPayload => ({
    order_id: selectedOrder?.id || selectedOrder?.order_id || selectedOrder?.orderId,
    order_number: selectedOrder?.orderNumber || selectedOrder?.order_number,
    customer_id: selectedOrder?.customerId || selectedOrder?.customer_id,
    store_id: user?.activeStoreId,
    return_type: returnType,
    items: activeReturns.map(i => ({
      order_item_id: i.orderItemId,
      product_name: i.productName,
      sku: i.sku,
      return_qty: i.returnQty,
      unit_price: i.unitPrice,
      gst_rate: i.gstRate,
      reason: i.reason,
      condition: i.condition,
      notes: i.notes,
    })),
    replacement_items:
      returnType === 'EXCHANGE'
        ? replacementItems.map(r => ({
            product_id: r.productId,
            name: r.name,
            sku: r.sku,
            quantity: r.quantity,
            unit_price: r.unitPrice,
          }))
        : undefined,
    approval_note: approvalNote || undefined,
    // Restocking fee is a refund-path concept only (EXCHANGE is settled on
    // the difference). Send it for RETURN / CREDIT_NOTE.
    restocking_fee: returnType === 'EXCHANGE' ? undefined : safeFee || undefined,
    // DRAWER-TRUTH: the tender(s) the refund was actually returned in. Sent
    // for a RETURN with a positive net so Day-End auto-nets the CASH leg(s).
    // Legs are only ever sent once fully specified (never a blank method).
    refund_tenders:
      returnType === 'RETURN' && roundedNet > 0 && refundTenders.every(t => t.method !== '')
        ? refundTenders.map((t) => ({
            method: t.method as RefundTenderMethod,
            amount: Math.round((Number(t.amount) || 0) * 100) / 100,
          }))
        : undefined,
    // EXCHANGE COLLECT: the tender the price difference was collected in.
    // Omitted when unset or explicitly "not collected at this till" — the
    // server treats absent as UNKNOWN and nets it nowhere (the safe state).
    collect_method:
      returnType === 'EXCHANGE' &&
      exchangeDirection === 'COLLECT' &&
      collectMethod !== '' &&
      collectMethod !== 'NOT_AT_TILL'
        ? (collectMethod as RefundTenderCode)
        : undefined,
    refund_reason: returnType === 'EXCHANGE' ? undefined : dominantMatrixReason(),
    refund_approval_request_id: approval?.requestId,
    refund_approval_token: approval?.approvalToken,
  });

  // Core submit. `approval` carries the F27 token + request id when re-submitting
  // after a manager approved a gated refund. Returns true on success.
  const submitReturn = async (
    approval?: { requestId: string; approvalToken?: string },
  ): Promise<boolean> => {
    const result = await returnsApi.create(buildPayload(approval));
    setResultId(result.return_id || null);
    // Read the SERVER's verdict + the amounts it actually recorded.
    setResultDrawerNetted(result?.drawer_auto_netted === true);
    setResultCashRefunded(
      Math.round(
        ((result?.refund_tenders as any[]) || [])
          .filter((t) => String(t?.method || '').toUpperCase() === 'CASH')
          .reduce((sum: number, t: any) => sum + (Number(t?.amount) || 0), 0) * 100,
      ) / 100,
    );
    setResultCreditAmount(Number(result?.credit_amount) || 0);
    setResultCollectAmount(Number(result?.collect_amount) || 0);
    setResultCollectMethod(String(result?.collect_method || ''));
    setStep('complete');
    return true;
  };

  const handleSubmit = async () => {
    if (activeReturns.length === 0) { setError('Select at least one item to return'); return; }
    setError(null);
    setIsSubmitting(true);
    try {
      await submitReturn();
    } catch (e: any) {
      // F27: the server gates a tiered refund with 403 reason=REFUND_APPROVAL_REQUIRED.
      // Open the request+poll modal instead of surfacing a raw error.
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 403 && detail?.reason === 'REFUND_APPROVAL_REQUIRED') {
        setApprovalGate({
          amount: netRefund,
          reason: dominantMatrixReason(),
          requiredTier: detail?.required_tier,
        });
        return;
      }
      const msg = typeof detail === 'string' ? detail : detail?.message;
      setError(msg || 'Failed to process return. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // After a manager approves the gated refund, re-submit with the token.
  const onRefundApproved = async (approval: { requestId: string; approvalToken?: string }) => {
    setApprovalGate(null);
    setIsSubmitting(true);
    setError(null);
    try {
      await submitReturn(approval);
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message;
      setError(msg || 'The refund could not be finalised after approval. Please retry.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const fc = (amount: number) => `₹${Math.round(amount).toLocaleString('en-IN')}`;
  // PAISE-EXACT formatter for every figure the server balance-checks (the
  // tender legs, the running total and the net refund). Rounding these to
  // rupees hid the gap that made a "balanced" split 400 on submit.
  const fp = (amount: number) =>
    `₹${(Math.round((Number(amount) || 0) * 100) / 100).toLocaleString('en-IN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;

  // Approval status pill for a return-history row. The return doc stamps the
  // F27 refund-approval outcome (status + approver name + request id) only when
  // the matrix gate cleared a tiered refund; a normal refund has none of these,
  // so we show "Not required". Approved -> green with the approver's name;
  // anything else (expired/rejected) -> amber/gray.
  const ApprovalPill = ({ r }: { r: any }) => {
    const status: string | undefined = r.refund_approval_status;
    const by: string | undefined = r.refund_approval_by_name || r.refund_approval_by;
    if (status === 'APPROVED') {
      return (
        <span
          className="text-xs px-2 py-0.5 rounded-full bg-green-50 text-green-700 border border-green-200"
          title={by ? `Approved by ${by}` : 'Approved'}
        >
          {by ? `Approved · ${by}` : 'Approved'}
        </span>
      );
    }
    if (status === 'EXPIRED') {
      return <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 border border-gray-200">Expired</span>;
    }
    if (status === 'REQUESTED' || status === 'PENDING') {
      return <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">Approval pending</span>;
    }
    if (status === 'REJECTED') {
      return <span className="text-xs px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200">Rejected</span>;
    }
    return <span className="text-xs text-gray-400">Not required</span>;
  };

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await returnsApi.list({ store_id: user?.activeStoreId, limit: 100 });
      setHistory(res?.returns || res || []);
    } catch {
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, [user?.activeStoreId]);

  useEffect(() => {
    if (mode === 'history') loadHistory();
  }, [mode, loadHistory]);

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head" style={{ maxWidth: 900 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Returns & Exchanges</div>
          <h1>Undo, gracefully.</h1>
          <div className="hint">Refund to source, exchange for another SKU, or issue a store-credit note. Every action is audit-logged against the original invoice.</div>
        </div>
        <div className="flex rounded-lg border border-gray-200 overflow-hidden self-start">
          {(['new', 'history'] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className={clsx('px-3 py-1.5 text-sm', mode === m ? 'bg-bv-red-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50')}>
              {m === 'new' ? 'New return' : 'History'}
            </button>
          ))}
        </div>
      </div>

      {mode === 'new' && (
      <>
      {/* Return Type Selection */}
      <div className="flex gap-2">
        {([
          { id: 'RETURN' as const, label: 'Return & Refund', icon: RotateCcw, desc: 'Refund to original payment' },
          { id: 'EXCHANGE' as const, label: 'Exchange', icon: ArrowLeftRight, desc: 'Replace with different product' },
          { id: 'CREDIT_NOTE' as const, label: 'Store Credit', icon: Receipt, desc: 'Issue credit for future use' },
        ]).map(t => (
          <button key={t.id} onClick={() => setReturnType(t.id)}
            className={clsx('flex-1 p-3 rounded-xl border-2 text-left transition-all',
              returnType === t.id ? 'border-bv-red-600 bg-bv-red-50' : 'border-gray-200 hover:border-gray-300')}>
            <div className="flex items-center gap-2">
              <t.icon className={clsx('w-5 h-5', returnType === t.id ? 'text-bv-red-600' : 'text-gray-500')} />
              <span className={clsx('text-sm font-medium', returnType === t.id ? 'text-bv-red-700' : 'text-gray-700')}>{t.label}</span>
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
            {/* DELTAS Critical #2: primary CTA = ink (gray-900),
                not BV-red. BV-red is an accent reserved for the
                rail-active indicator + ≤1 hero CTA per screen. */}
            <button onClick={searchOrders} disabled={isLoading}
              className="px-6 py-2.5 bg-gray-900 text-white rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50">
              {isLoading ? 'Searching...' : 'Search'}
            </button>
          </div>

          {orders.length > 0 && (
            <div className="mt-4 space-y-2">
              {orders.map(order => (
                <button key={order.id} onClick={() => selectOrder(order)}
                  className="w-full flex items-center justify-between p-3 rounded-lg border border-gray-200 hover:border-bv-red-300 hover:bg-bv-red-50 text-left transition-all">
                  <div>
                    <p className="font-medium text-sm text-gray-900">{order.orderNumber}</p>
                    <p className="text-xs text-gray-500">{order.customerName} · {formatDateIST(order.createdAt)}</p>
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
                item.returnQty > 0 ? 'border-bv-red-300 bg-bv-red-50' : 'border-gray-200')}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <p className="font-medium text-sm">{item.productName}</p>
                    {/* Show the GST-INCLUSIVE gross actually billed per unit —
                        the same basis the refund is computed on. */}
                    <p className="text-xs text-gray-500">{item.sku} · Purchased: {item.quantity} · {fp(item.billedUnitGross)} each</p>
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

          {/* EXCHANGE: replacement product picker */}
          {returnType === 'EXCHANGE' && activeReturns.length > 0 && (
            <div className="mt-4 pt-4 border-t border-gray-200">
              <h4 className="text-sm font-semibold text-gray-900 mb-2">Replacement Product(s)</h4>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                  <input value={productQuery} onChange={e => setProductQuery(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && searchProducts()}
                    placeholder="Search product by name, brand, or SKU..."
                    className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm" />
                </div>
                <button onClick={searchProducts} disabled={productSearching}
                  className="px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50">
                  {productSearching ? 'Searching...' : 'Search'}
                </button>
              </div>

              {productResults.length > 0 && (
                <div className="mt-2 space-y-1 max-h-48 overflow-auto">
                  {productResults.map((p, idx) => (
                    <button key={p.product_id || p.id || p._id || idx} onClick={() => addReplacementFromProduct(p)}
                      className="w-full flex items-center justify-between p-2 rounded-lg border border-gray-200 hover:border-bv-red-300 hover:bg-bv-red-50 text-left text-sm">
                      <span>
                        <span className="font-medium">{p.name || p.model || 'Item'}</span>
                        <span className="text-xs text-gray-500 ml-2">{p.sku || ''}</span>
                      </span>
                      <span className="font-semibold">{fc(p.offer_price || p.price || p.mrp || 0)}</span>
                    </button>
                  ))}
                </div>
              )}

              {replacementItems.length > 0 && (
                <div className="mt-3 space-y-2">
                  {replacementItems.map((r, i) => (
                    <div key={i} className="grid grid-cols-12 gap-2 items-center">
                      <input value={r.name} onChange={e => updateReplacement(i, { name: e.target.value })}
                        placeholder="Product name" className="col-span-5 px-2 py-1.5 border border-gray-300 rounded text-sm" />
                      <input value={r.sku} onChange={e => updateReplacement(i, { sku: e.target.value })}
                        placeholder="SKU" className="col-span-2 px-2 py-1.5 border border-gray-300 rounded text-sm" />
                      <input type="number" min={1} value={r.quantity} onChange={e => updateReplacement(i, { quantity: Math.max(1, Number(e.target.value)) })}
                        className="col-span-2 px-2 py-1.5 border border-gray-300 rounded text-sm" />
                      <input type="number" min={0} value={r.unitPrice} onChange={e => updateReplacement(i, { unitPrice: Math.max(0, Number(e.target.value)) })}
                        className="col-span-2 px-2 py-1.5 border border-gray-300 rounded text-sm" />
                      <button onClick={() => removeReplacement(i)} className="col-span-1 text-gray-400 hover:text-bv-red-600">
                        <Trash2 className="w-4 h-4 mx-auto" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeReturns.length > 0 && (
            <div className="mt-4 pt-4 border-t border-gray-200">
              {returnType === 'EXCHANGE' ? (
                <div className="mb-3 space-y-1">
                  <div className="flex items-center justify-between text-sm text-gray-700">
                    <span>Returned value</span><span className="font-medium">{fc(totalRefund)}</span>
                  </div>
                  <div className="flex items-center justify-between text-sm text-gray-700">
                    <span>Replacement total</span><span className="font-medium">{fc(replacementTotal)}</span>
                  </div>
                  <div className={clsx('flex items-center justify-between mt-1 pt-2 border-t border-gray-200 text-sm font-semibold',
                    exchangeDirection === 'COLLECT' ? 'text-bv-red-600' : exchangeDirection === 'REFUND' ? 'text-green-700' : 'text-gray-900')}>
                    <span>
                      {exchangeDirection === 'COLLECT' ? 'Collect from customer'
                        : exchangeDirection === 'REFUND' ? 'Refund / store credit'
                        : 'Even exchange'}
                    </span>
                    <span className="text-lg">{exchangeDirection === 'EVEN' ? fc(0) : fc(Math.abs(exchangeDiff))}</span>
                  </div>
                  {exchangeDirection === 'COLLECT' && (
                    <div className="mt-2 pt-2 border-t border-gray-200">
                      <div className="flex items-center justify-between gap-3">
                        <label className="text-sm text-gray-700">Collected via</label>
                        <select
                          value={collectMethod}
                          onChange={e => setCollectMethod(e.target.value as RefundTenderCode | '' | 'NOT_AT_TILL')}
                          className={clsx('w-56 px-2 py-1.5 border rounded text-sm',
                            collectMethod === '' ? 'border-amber-400 text-gray-500' : 'border-gray-300')}
                        >
                          <option value="" disabled>Select tender…</option>
                          {TENDER_CODES.map(t => <option key={t} value={t}>{t}</option>)}
                          <option value="NOT_AT_TILL">Not collected at this till / billed through POS</option>
                        </select>
                      </div>
                      {collectMethod === '' && (
                        <p className="text-xs text-amber-600 mt-1 text-right">
                          Choose how the difference was collected — it is never assumed.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="mb-3 space-y-2">
                  <div className="flex items-center justify-between text-sm text-gray-700">
                    <span>Gross paid (incl. GST)</span>
                    <span className="font-medium">{fc(totalRefund)}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <label className="text-sm text-gray-700 block">Restocking fee (Rs)</label>
                      <p className="text-xs text-gray-500">for damaged/opened goods - leave 0 for a full refund</p>
                    </div>
                    <input type="number" min={0} max={Math.round(totalRefund)} value={restockingFee}
                      onChange={e => setRestockingFee(Math.max(0, Number(e.target.value)))}
                      className="w-28 px-2 py-1.5 border border-gray-300 rounded text-sm text-right" />
                  </div>
                  {safeFee > 0 && (
                    <div className="flex items-center justify-between text-sm text-gray-500">
                      <span>Less restocking fee</span>
                      <span className="font-medium text-bv-red-600">-{fc(safeFee)}</span>
                    </div>
                  )}
                  <div className="flex items-center justify-between mt-1 pt-2 border-t border-gray-200">
                    <span className="text-sm text-gray-700">
                      {returnType === 'CREDIT_NOTE' ? 'Net store credit' : 'Net refund'}
                    </span>
                    <span className="font-bold text-lg text-gray-900">
                      {quoteLoading && !quote ? 'Calculating…' : fp(netRefund)}
                    </span>
                  </div>

                  {/* Refund-tender capture (RETURN only): how the cash is returned. */}
                  {returnType === 'RETURN' && roundedNet > 0 && (
                    <div className="mt-3 pt-3 border-t border-gray-200">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-medium text-gray-800">Refund paid via</span>
                        {!isSingleSource && !tendersUnverifiable && (
                          <span className="text-xs text-amber-600">Specify how the refund is returned</span>
                        )}
                      </div>
                      {/* The original sale's captured tenders, straight from the
                          server — so the prefill is checkable and the cashier can
                          see what is legitimately refundable per tender. */}
                      {Object.keys(capturedTenders).length > 0 && (
                        <p className="text-xs text-gray-500 mb-2">
                          Paid on this order:{' '}
                          {Object.keys(capturedTenders).map((t, i) => (
                            <span key={t}>
                              {i > 0 && ' · '}
                              <span className="font-medium text-gray-700">{t} {fp(capturedTenders[t] || 0)}</span>
                              {(refundableByTender[t] ?? 0) !== (capturedTenders[t] ?? 0) &&
                                ` (refundable ${fp(refundableByTender[t] || 0)})`}
                            </span>
                          ))}
                          {Object.keys(nonRefundableTenders).map((t) => (
                            <span key={t}>
                              {' · '}
                              <span className="font-medium text-gray-700">{t} {fp(nonRefundableTenders[t] || 0)}</span>
                            </span>
                          ))}
                        </p>
                      )}
                      {/* WHY a cash-in-only split cannot balance — without this
                          the cashier hits a dead end with no explanation. */}
                      {cashInShortfall && (
                        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 mb-2">
                          Part of this sale was paid with{' '}
                          {Object.keys(nonRefundableTenders).join(' / ') || 'a non-refundable instrument'},
                          which cannot be handed back as cash. Refund that portion as{' '}
                          <span className="font-semibold">STORE_CREDIT</span> (it is recorded but not deducted from the drawer).
                        </p>
                      )}
                      {tendersUnverifiable && !cashInShortfall && (
                        <p className="text-xs text-amber-600 mb-2">
                          {Object.keys(capturedTenders).length === 0
                            ? 'This order has no recorded payment tenders, so the refund cannot be auto-deducted from the drawer.'
                            : `This order was partly paid by a method the till cannot refund to, so only ${fp(Object.values(refundableByTender).reduce((a, b) => a + (b || 0), 0))} of the ${fp(roundedNet)} can be auto-deducted.`}
                          {' '}Leave the tender rows blank to record the refund without auto-deduction, then enter it as cash paid out at day-end if you hand back cash.
                        </p>
                      )}
                      <p className="text-xs text-gray-500 mb-2">
                        Recorded in Day-End so the drawer nets it automatically — the total must equal the net refund exactly.
                      </p>
                      <div className="space-y-2">
                        {refundTenders.map((t, i) => (
                          <div key={i} className="flex items-center gap-2">
                            <select
                              value={t.method}
                              onChange={e => updateRefundTenderRow(i, { method: e.target.value as RefundTenderMethod })}
                              className={clsx('w-32 px-2 py-1.5 border rounded text-sm',
                                t.method === '' ? 'border-amber-400 text-gray-500' : 'border-gray-300')}
                            >
                              <option value="" disabled>Tender…</option>
                              {(sourceTenders.length ? sourceTenders : TENDER_CODES).map(code => (
                                <option key={code} value={code}>
                                  {code}
                                  {refundableByTender[code] != null ? ` (max ${fp(refundableByTender[code])})` : ''}
                                </option>
                              ))}
                            </select>
                            <div className="flex items-center flex-1">
                              <span className="text-gray-400 mr-1">₹</span>
                              <input
                                type="number" min={0} step="0.01" value={t.amount || ''}
                                onChange={e => updateRefundTenderRow(i, { amount: Math.max(0, Number(e.target.value)) })}
                                className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-right tabular-nums"
                              />
                            </div>
                            {refundTenders.length > 1 && (
                              <button type="button" onClick={() => removeRefundTenderRow(i)}
                                className="p-1.5 text-gray-400 hover:text-bv-red-600" aria-label="Remove tender">
                                <Trash2 className="w-4 h-4" />
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                      <div className="flex items-center justify-between mt-2">
                        <button type="button" onClick={addRefundTenderRow}
                          className="text-xs text-bv-red-600 font-medium flex items-center gap-1">
                          <Plus className="w-3 h-3" /> Add tender
                        </button>
                        <span className={clsx('text-xs font-medium tabular-nums',
                          refundTendersBalanced ? 'text-green-700' : 'text-amber-600')}>
                          {fp(refundTenderTotal)} / {fp(roundedNet)}
                          {!refundTendersBalanced && ' — must match net refund'}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              )}
              <textarea value={approvalNote} onChange={e => setApprovalNote(e.target.value)}
                placeholder="Approval notes or justification (visible to admin)..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm h-16 resize-none mb-3" />
              <div className="flex gap-3">
                <button onClick={() => { setStep('search'); setSelectedOrder(null); }}
                  className="px-4 py-2.5 border border-gray-300 rounded-lg text-sm">Cancel</button>
                <button onClick={() => setShowConfirm(true)} disabled={isSubmitting || quoteLoading || !refundTendersReady}
                  className="flex-1 py-2.5 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700 disabled:opacity-50 disabled:cursor-not-allowed">
                  {isSubmitting ? 'Processing...'
                    : quoteLoading ? 'Pricing…'
                    : `Review ${returnType === 'EXCHANGE' ? 'Exchange' : returnType === 'CREDIT_NOTE' ? 'Credit Note' : 'Return'} (${activeReturns.length} items)`}
                </button>
              </div>
              {quoteError && (
                <p className="text-xs text-bv-red-600 mt-2 text-right">{quoteError}</p>
              )}
              {!quoteError && returnType === 'RETURN' && roundedNet > 0 && !refundTendersReady && !quoteLoading && (
                <p className="text-xs text-amber-600 mt-2 text-right">Enter the refund tender split so it totals the net refund exactly.</p>
              )}
              {!quoteError && returnType === 'EXCHANGE' && exchangeDirection === 'COLLECT' && collectMethod === '' && (
                <p className="text-xs text-amber-600 mt-2 text-right">Choose how the difference was collected.</p>
              )}
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
          <p className="text-2xl font-bold text-bv-red-600 mt-3">
            {returnType === 'EXCHANGE' ? (exchangeDirection === 'EVEN' ? fc(0) : fc(Math.abs(exchangeDiff))) : fc(netRefund)}
          </p>
          <p className="text-sm text-gray-500 mt-1">
            {returnType === 'CREDIT_NOTE' ? 'Store credit added to customer account' :
              returnType === 'EXCHANGE'
                ? (exchangeDirection === 'COLLECT' ? 'Collect this balance from the customer'
                  : exchangeDirection === 'REFUND' ? 'Difference issued as store credit'
                  : 'Even exchange — no balance due')
                : 'Refund recorded against the tender(s) you selected'}
          </p>
          {/* Store credit actually ISSUED for the non-drawer portion. */}
          {returnType === 'RETURN' && resultCreditAmount > 0 && (
            <p className="text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 mt-3 max-w-md mx-auto">
              {fp(resultCreditAmount)} issued as store credit to the customer's account.
            </p>
          )}
          {/* Day-End guidance keyed on the SERVER's answer, never on local
              intent. Getting this backwards guarantees a false shortage. */}
          {returnType === 'RETURN' && resultDrawerNetted === true && resultCashRefunded > 0 && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mt-3 max-w-md mx-auto">
              This {fp(resultCashRefunded)} cash refund is now recorded in Day-End — do not also enter it as cash paid out.
            </p>
          )}
          {returnType === 'RETURN' && resultDrawerNetted === false && roundedNet > 0 && (
            <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mt-3 max-w-md mx-auto">
              This refund could NOT be auto-deducted from the drawer (the order has no verified payment tenders).
              If you handed back cash, record {fp(netRefund)} as cash paid out at day-end — otherwise the till will read short.
            </p>
          )}
          {/* An EXCHANGE-COLLECT taken in CASH moves the SAME drawer figure as
              a refund does. Both banners used to be gated to RETURN, so this
              money entered the expected drawer with nothing on screen. */}
          {returnType === 'EXCHANGE' && resultDrawerNetted === true
            && resultCollectMethod === 'CASH' && resultCollectAmount > 0 && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mt-3 max-w-md mx-auto">
              The {fp(resultCollectAmount)} you collected in cash is now expected in Day-End — do not also record it as a separate cash-in.
            </p>
          )}
          {returnType === 'EXCHANGE' && resultDrawerNetted === false
            && exchangeDirection === 'COLLECT' && (
            <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mt-3 max-w-md mx-auto">
              This collection was NOT added to the expected drawer (no tender was recorded for it).
              If you took cash, record it at day-end — otherwise the till will read over.
            </p>
          )}
          <div className="flex gap-3 justify-center mt-6">
            <button onClick={() => { setStep('search'); setSelectedOrder(null); setReturnItems([]); setResultId(null); setReplacementItems([]); setProductResults([]); setProductQuery(''); setRestockingFee(0); setRefundTenders([]); setCollectMethod(''); setQuote(null); setQuoteError(null); setResultDrawerNetted(null); setResultCashRefunded(0); setResultCreditAmount(0); setResultCollectAmount(0); setResultCollectMethod(''); }}
              className="px-6 py-2.5 bg-bv-red-600 text-white rounded-lg text-sm font-semibold">New Return</button>
          </div>
        </div>
      )}
      </>
      )}

      {mode === 'history' && (
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-gray-900">Recent returns</h3>
            <button onClick={loadHistory} className="text-sm text-gray-500 hover:text-gray-700 inline-flex items-center gap-1">
              <RefreshCw className="w-4 h-4" /> Refresh
            </button>
          </div>
          {historyLoading ? (
            <div className="py-10 text-center text-gray-500">Loading...</div>
          ) : history.length === 0 ? (
            <div className="py-10 text-center text-gray-500">
              <RotateCcw className="w-9 h-9 mx-auto mb-2 opacity-40" />
              <p>No returns recorded yet.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 text-left border-b border-gray-200">
                    <th className="py-2 pr-3">Return</th>
                    <th className="py-2 pr-3">Type</th>
                    <th className="py-2 pr-3">Customer</th>
                    <th className="py-2 pr-3">Order</th>
                    <th className="py-2 pr-3 text-right">Amount</th>
                    <th className="py-2 pr-3">Approval</th>
                    <th className="py-2">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((r, i) => {
                    const amt = r.return_type === 'EXCHANGE'
                      ? (r.settlement?.difference ?? 0)
                      : (r.refund_amount ?? r.credit_amount ?? r.returned_value ?? 0);
                    const typeLabel = ({ RETURN: 'Refund', EXCHANGE: 'Exchange', CREDIT_NOTE: 'Credit' } as Record<string, string>)[r.return_type] || r.return_type;
                    return (
                      <tr key={r.return_id || i} className="border-b border-gray-100">
                        <td className="py-2 pr-3 font-mono text-xs text-gray-700">{r.return_id}</td>
                        <td className="py-2 pr-3"><span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-700">{typeLabel}</span></td>
                        <td className="py-2 pr-3">{r.customer_name || '-'}</td>
                        <td className="py-2 pr-3 text-gray-500">{r.order_number || '-'}</td>
                        <td className="py-2 pr-3 text-right font-medium">{fc(amt)}</td>
                        <td className="py-2 pr-3"><ApprovalPill r={r} /></td>
                        <td className="py-2 text-gray-500">{formatDateIST(r.created_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {approvalGate && (
        <RefundApprovalModal
          amount={approvalGate.amount}
          storeId={user?.activeStoreId}
          orderId={selectedOrder?.id || selectedOrder?.order_id || selectedOrder?.orderId}
          orderNumber={selectedOrder?.orderNumber || selectedOrder?.order_number}
          customerName={selectedOrder?.customerName || selectedOrder?.customer_name}
          reason={approvalGate.reason}
          requiredTier={approvalGate.requiredTier}
          requestedByName={user?.name}
          onClose={() => setApprovalGate(null)}
          onApproved={onRefundApproved}
        />
      )}

      {/* UI P0: irreversible-refund confirmation. Names the order, items, net
          refund AND the refund tender(s) before anything is recorded. */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md p-6">
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle className="w-5 h-5 text-bv-red-600" />
              <h3 className="text-lg font-bold text-gray-900">
                Confirm {returnType === 'EXCHANGE' ? 'Exchange' : returnType === 'CREDIT_NOTE' ? 'Credit Note' : 'Refund'}
              </h3>
            </div>
            <p className="text-sm text-gray-500 mb-3">
              This records money movement and cannot be undone. Please check the details.
            </p>
            <dl className="text-sm space-y-1.5 mb-4">
              <div className="flex justify-between"><dt className="text-gray-500">Order</dt>
                <dd className="font-medium text-gray-900">{selectedOrder?.orderNumber || selectedOrder?.order_number || '—'}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-500">Items</dt>
                <dd className="font-medium text-gray-900 text-right max-w-[60%] truncate">
                  {activeReturns.length} · {activeReturns.map(i => i.productName).join(', ')}
                </dd></div>
              {returnType === 'EXCHANGE' ? (
                <>
                  <div className="flex justify-between"><dt className="text-gray-500">
                    {exchangeDirection === 'COLLECT' ? 'Collect from customer' : exchangeDirection === 'REFUND' ? 'Refund / store credit' : 'Even exchange'}</dt>
                    <dd className="font-bold text-gray-900 tabular-nums">{exchangeDirection === 'EVEN' ? fp(0) : fp(Math.abs(exchangeDiff))}</dd></div>
                  {exchangeDirection === 'COLLECT' && (
                    <div className="flex justify-between"><dt className="text-gray-500">Collected via</dt>
                      <dd className="font-medium text-gray-900">
                        {collectMethod === 'NOT_AT_TILL' ? 'Not at this till (not drawer-netted)' : collectMethod}
                      </dd></div>
                  )}
                </>
              ) : (
                <>
                  <div className="flex justify-between"><dt className="text-gray-500">
                    {returnType === 'CREDIT_NOTE' ? 'Net store credit' : 'Net refund'}</dt>
                    <dd className="font-bold text-gray-900 tabular-nums">{fp(netRefund)}</dd></div>
                  {returnType === 'RETURN' && roundedNet > 0 && (
                    <div className="flex justify-between"><dt className="text-gray-500">Refund tender(s)</dt>
                      <dd className="font-medium text-gray-900 text-right tabular-nums">
                        {refundTenders.map((t, i) => <div key={i}>{t.method} {fp(t.amount)}</div>)}
                      </dd></div>
                  )}
                </>
              )}
            </dl>
            <div className="flex gap-3">
              <button onClick={() => setShowConfirm(false)}
                className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg text-sm">Back</button>
              <button
                onClick={() => { setShowConfirm(false); handleSubmit(); }}
                disabled={isSubmitting}
                className="flex-1 py-2.5 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700 disabled:opacity-50">
                {isSubmitting ? 'Processing...' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
