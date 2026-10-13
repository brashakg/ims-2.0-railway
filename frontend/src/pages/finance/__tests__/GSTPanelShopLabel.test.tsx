// ============================================================================
// IMS 2.0 - Finance > GST: 'your shop' is THE Purchase / Finance shop rule
// ============================================================================
// Panel 2026-10-13: the GST tab worked out the caller's shop on its own
// (active shop, else the first assigned one) while usePurchaseShop -- the one
// Purchase / Finance scope the Finance header and Vendor Returns read -- took
// the active shop only. A login holding shops but no active one read
// 'Your shop: Pune' on the GST tab and no shop at all in the header. And a
// login with NO shop was labelled 'All shops' (fail open). One rule now
// (usePurchaseShop.ownStoreId, the server's resolve_store_scope), and a login
// with no shop is never 'All shops'.

import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import type { GSTSummaryData } from '../financeTypes';

let user: Record<string, unknown> = {};
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user }),
}));
vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({ data: [{ store_id: 'WO-PUN-01', store_name: 'Pune' }] }),
}));

import GSTPanel from '../GSTPanel';
import { PurchaseShopLabel } from '../../purchase/purchaseShop';

const VIEW: GSTSummaryData = {
  period: '',
  cgst_collected: 9000,
  sgst_collected: 9000,
  igst_collected: 0,
  total_gst: 18000,
  gst_payable: 0,
  input_tax_credit: 0,
  gst_type: 'CGST_SGST',
};

const page = () =>
  render(
    <>
      <PurchaseShopLabel />
      <GSTPanel gstSummary={VIEW} />
    </>,
  ).container.textContent || '';

describe('GSTPanel - the shop it names is the one shop rule', () => {
  it('a login holding shops but no active one: the header and the GST tab name the same shop', () => {
    user = { id: 'u-1', roles: ['AREA_MANAGER'], storeIds: ['WO-PUN-01', 'BV-DHN-01'] };
    const text = page();
    expect(text).toContain('Shop: Pune');
    expect(text).toContain('Your shop: Pune');
  });

  it('a login with no shop is never labelled All shops', () => {
    user = { id: 'u-2', roles: ['STORE_MANAGER'], storeIds: [] };
    const text = page();
    expect(text).not.toContain('All shops');
    expect(text).toContain('Your shop: No shop assigned');
  });

  it('an admin reads All shops', () => {
    user = { id: 'u-3', roles: ['ADMIN'], activeStoreId: 'WO-PUN-01', storeIds: ['WO-PUN-01'] };
    expect(page()).toContain('All shops');
  });
});
