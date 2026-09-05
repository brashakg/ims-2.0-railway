// ============================================================================
// IMS 2.0 - Customer dues: THE one read of what a customer still owes
// ============================================================================
// Two existing doors, read together:
//   * GET /orders?customer_id=      -> per-order balance_due (the rows)
//   * GET /customers/{id}/credit-summary -> the account's AR outstanding /
//     credit limit (the headline the POS dues tile already shows)
// Used by the Customer 360 page (orders) and the POS customer panel (dues
// section), so the envelope-unwrapping and the "what counts as due" rule live
// here once. Read-only: collecting money stays in the payment flows.

import { useQuery } from '@tanstack/react-query';
import { orderApi } from '../services/api/sales';
import { customerApi } from '../services/api/customers';

/** GET /orders returns an envelope {orders,total,data,...}, NOT a bare array
 *  (calling .reduce on it once blanked the whole Customer 360 page). Orders come
 *  back camelCase (grandTotal/createdAt/balanceDue) via order_to_frontend. */
export async function fetchCustomerOrders(customerId: string): Promise<any[]> {
  const resp: any = await orderApi.getOrders({ customerId });
  return Array.isArray(resp) ? resp : resp?.orders || resp?.data || [];
}

export interface CustomerDues {
  credit: {
    credit_limit: number;
    ar_outstanding: number;
    ar_available: number | null;
    limit_exceeded: boolean;
  } | null;
  /** Orders that still carry a balance, newest first. */
  dueOrders: Array<{
    id: string;
    orderNumber: string;
    createdAt: string | null;
    grandTotal: number;
    balanceDue: number;
  }>;
}

export const customerDuesQueryKey = (customerId: string) =>
  ['orders', 'customer-dues', customerId] as const;

export function useCustomerDues(customerId: string | null | undefined) {
  return useQuery<CustomerDues, Error>({
    queryKey: customerDuesQueryKey(customerId || ''),
    enabled: !!customerId,
    queryFn: async () => {
      const id = customerId as string;
      const [orders, credit] = await Promise.all([
        fetchCustomerOrders(id),
        // The account summary is a headline, not the rows: a failure there
        // must not hide the per-order dues.
        customerApi.getCreditSummary(id).catch(() => null),
      ]);
      const dueOrders = orders
        .filter((o: any) => {
          const status = String(o?.orderStatus ?? o?.status ?? '').toUpperCase();
          if (status === 'CANCELLED') return false;
          return Number(o?.balanceDue ?? o?.balance_due ?? 0) > 0;
        })
        .map((o: any) => ({
          id: String(o.id ?? o.order_id ?? ''),
          orderNumber: String(o.orderNumber ?? o.order_number ?? o.id ?? ''),
          createdAt: o.createdAt ?? o.created_at ?? null,
          grandTotal: Number(o.grandTotal ?? o.grand_total ?? 0),
          balanceDue: Number(o.balanceDue ?? o.balance_due ?? 0),
        }));
      return { credit, dueOrders };
    },
  });
}
