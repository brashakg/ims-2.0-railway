// ============================================================================
// Codex PR-1425 (L2-WS-F03 / L2-WS-F02 follow-up): the job card printed at the
// counter right after the sale is the card that goes to the bench with the
// frame. It must carry the sale's fitting instructions and the order's notes,
// and print the IST calendar day in every browser zone -- the same shared
// mapping (workshop/shared.jobCardPrintData) the Workshop page prints through.
// ============================================================================
// Browser zone west of UTC: a naive-UTC createdAt '2026-11-13T20:00:00' is
// 14 Nov 01:30 IST, but a local-zone parse reads it as 13 Nov.
process.env.TZ = 'America/New_York';

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', activeStoreId: 'BV-BOK-01', roles: ['SALES_STAFF'] } }),
}));
vi.mock('../../../../services/api/client', () => ({ default: { get: vi.fn() } }));

const NOTES = 'Frame: Frame note must reach bench; Lens: Lens note must reach bench';
const JOB = {
  jobNumber: 'WS-261114-42315E0A', orderNumber: 'ORD-1', customerName: 'Asha Verma',
  customerPhone: '9876543210', frameName: 'Audit Frame', lensType: 'Progressive 1.60',
  status: 'PENDING', priority: 'NORMAL', promisedDate: '2026-11-16', expectedDate: '2026-11-16',
  createdAt: '2026-11-13T20:00:00', fittingInstructions: NOTES, notes: 'Order special note',
};
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    getOrder: () => Promise.resolve({
      id: 'o-1', orderNumber: 'ORD-1', storeId: 'BV-BOK-01', customerName: 'Asha Verma',
      customerPhone: '9876543210', grandTotal: 5000, amountPaid: 5000, balanceDue: 0, items: [],
    }),
  },
  workshopApi: { getJob: () => Promise.resolve(JOB), updateFittingDetails: vi.fn() },
}));
vi.mock('../../../../services/api/marketing', () => ({
  marketingApi: { sendNotification: vi.fn(), sendReviewRequest: vi.fn() },
}));
vi.mock('../../../../services/api/incentive', () => ({
  incentiveApi: { listDaily: () => Promise.resolve({ items: [] }), getMtd: () => Promise.resolve({ items: [] }) },
}));
vi.mock('../../../../services/api/walkouts', () => ({
  walkoutsApi: { dashboardPerStaff: () => Promise.resolve({ items: [] }) },
}));
vi.mock('../../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: () =>
    Promise.resolve({ store: { storeName: 'BV Bokaro', storeCode: 'BV-BOK-01' }, entity: {} }),
}));

import { SaleCompleteScreen } from '../SaleCompleteScreen';

describe('sale-complete workshop job card', () => {
  it('sanity: the browser zone really is west of UTC', () => {
    expect(new Date('2026-11-13T20:00:00').getDate()).toBe(13);
  });

  const openCard = async () => {
    const { container } = render(
      <MemoryRouter>
        <SaleCompleteScreen orderId="o-1" jobId="job-1" />
      </MemoryRouter>,
    );
    await screen.findByText(/Asha Verma/);
    const btn = await screen.findByRole('button', { name: /workshop job card/i });
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(btn);
    // The card is open once its lens line is printed.
    const body = container.ownerDocument.body;
    await waitFor(() => expect(body.textContent).toContain('Progressive 1.60'));
    return body.textContent || '';
  };

  it('prints the fitting instructions and the notes', async () => {
    const text = await openCard();
    expect(text).toContain(NOTES);
    expect(text).toContain('Order special note');
  });

  it('prints the IST created day and the promise day, never the browser-zone day', async () => {
    const text = await openCard();
    expect(text).toContain('14-Nov-2026');
    expect(text).not.toContain('13-Nov-2026');
    expect(text).toContain('16-Nov-2026');
  });
});
