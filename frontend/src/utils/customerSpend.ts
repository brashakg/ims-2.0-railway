// ============================================================================
// IMS 2.0 - THE customer-spend rule for every customer door (L2-CUST-F01)
// ============================================================================
// The Customers page cards, its Purchase History summary and Customer 360 used
// to sum different fields (totalAmount vs grandTotal) over different windows
// (10 vs 100 vs 50 orders), so one customer showed Rs 6,000 and Rs 0 at once.
// Every door now reads the same orders (CUSTOMER_SPEND_ORDER_LIMIT) and sums
// them here.
//
// Spend = the order's grand total, for every order except DRAFT, CANCELLED and
// REFUNDED - the same exclusion the server's lifetime figure on the Online
// customers list uses (customers._CUSTOMER_STATS_EXCLUDED_STATUSES). Partial
// returns are not netted (gross ordered value), as on that list.

/** The on-screen name and footnote of this figure on every customer door: it
 *  is GROSS ORDERED value, not settled net spend (audit: keep labels clear). */
export const CUSTOMER_SPEND_LABEL = 'Total ordered (gross)';
export const CUSTOMER_SPEND_NOTE = 'Excludes draft, cancelled, refunded; returns not deducted';

/** GET /orders allows at most 500 per call; a customer-scoped read is not
 *  date-limited, so this is the customer's latest 500 orders. */
export const CUSTOMER_SPEND_ORDER_LIMIT = 500;
/** Shown on EVERY door whose figures hit the limit (D50: never stop silently). */
export const CUSTOMER_SPEND_CAPPED_NOTE = `Figures cover the latest ${CUSTOMER_SPEND_ORDER_LIMIT} orders only; older orders are not included.`;

const EXCLUDED = new Set(['DRAFT', 'CANCELLED', 'REFUNDED']);

// GET /orders returns camelCase (grandTotal, orderStatus, createdAt); the
// snake_case names cover raw rows.
type OrderLike = Record<string, any>;

export function orderGrandTotal(o: OrderLike): number {
  const v = o?.grandTotal ?? o?.grand_total ?? 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

export function orderCountsAsSpend(o: OrderLike): boolean {
  const status = String(o?.orderStatus ?? o?.status ?? '').toUpperCase();
  return !EXCLUDED.has(status);
}

export interface CustomerSpend {
  totalSpend: number;
  orderCount: number;
  avgOrderValue: number;
  lastOrderDate: string | null;
  /** True when the read hit the limit: older orders are not in the figures. */
  capped: boolean;
}

export function summariseCustomerSpend(orders: OrderLike[]): CustomerSpend {
  const list = Array.isArray(orders) ? orders : [];
  const counted = list.filter(orderCountsAsSpend);
  const totalSpend = counted.reduce((sum, o) => sum + orderGrandTotal(o), 0);
  const dates = counted
    .map((o) => o?.createdAt ?? o?.created_at ?? o?.order_date ?? null)
    .filter((d): d is string => !!d)
    .sort((a, b) => new Date(b).getTime() - new Date(a).getTime());
  return {
    totalSpend,
    orderCount: counted.length,
    avgOrderValue: counted.length ? Math.round(totalSpend / counted.length) : 0,
    lastOrderDate: dates[0] ?? null,
    capped: list.length >= CUSTOMER_SPEND_ORDER_LIMIT,
  };
}
