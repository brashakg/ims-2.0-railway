// ============================================================================
// Sales -> Workshop fitting handoff lives on the completion screen
// ============================================================================
// The classic till opened LensFittingFormModal right after an Rx order spawned
// a workshop job; the new tills' completion screen never did, so the
// measurements that need the customer wearing the frame (seg height, fitting
// height) had nowhere to go at the till. It is one tap here now, writing
// through the same door the Workshop page's "Confirm fitting details" uses.
//
// Removing the button, or wiring the save to anything but
// workshopApi.updateFittingDetails(jobId, ...), fails the first test.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', activeStoreId: 'BV-BOK-01', roles: ['SALES_STAFF'] } }),
}));
vi.mock('../../../../services/api/client', () => ({ default: { get: vi.fn() } }));

const getOrder = vi.fn();
const updateFittingDetails = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: { getOrder: (...a: unknown[]) => getOrder(...a) },
  workshopApi: {
    getJob: () => Promise.resolve({ jobNumber: 'WJ-1' }),
    updateFittingDetails: (...a: unknown[]) => updateFittingDetails(...a),
  },
}));
vi.mock('../../../../services/api/marketing', () => ({
  marketingApi: { sendNotification: vi.fn(), sendReviewRequest: vi.fn() },
}));
vi.mock('../../../../services/api/incentive', () => ({
  incentiveApi: {
    listDaily: () => Promise.resolve({ items: [] }),
    getMtd: () => Promise.resolve({ items: [] }),
  },
}));
vi.mock('../../../../services/api/walkouts', () => ({
  walkoutsApi: { dashboardPerStaff: () => Promise.resolve({ items: [] }) },
}));
vi.mock('../../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: () =>
    Promise.resolve({ store: { storeName: 'BV Bokaro', storeCode: 'BV-BOK-01' }, entity: {} }),
}));
vi.mock('../../../../components/print/WorkshopJobCardPrint', () => ({
  WorkshopJobCardPrint: () => null,
}));
// The form itself has its own tests; here it only has to hand a value back.
vi.mock('../../../../components/pos/LensFittingFormModal', () => ({
  LensFittingFormModal: (p: {
    prefilledCoating?: string;
    onSave: (v: unknown) => void;
    onBack: () => void;
  }) => (
    <div>
      <span>fitting-modal:{p.prefilledCoating || ''}</span>
      <button type="button" onClick={() => p.onSave({ dia: '70', fh: '18' })}>
        save-fitting
      </button>
      <button type="button" onClick={p.onBack}>
        back-fitting
      </button>
    </div>
  ),
}));

import { SaleCompleteScreen } from '../SaleCompleteScreen';

const ORDER = {
  id: 'o-1',
  orderNumber: 'ORD-1',
  storeId: 'BV-BOK-01',
  customerName: 'Asha Verma',
  customerPhone: '9876543210',
  grandTotal: 5000,
  amountPaid: 5000,
  balanceDue: 0,
  items: [],
};

const mount = (props: Record<string, unknown>) =>
  render(
    <MemoryRouter>
      <SaleCompleteScreen orderId="o-1" {...props} />
    </MemoryRouter>,
  );

beforeEach(() => {
  getOrder.mockReset().mockResolvedValue(ORDER);
  updateFittingDetails.mockReset().mockResolvedValue({});
});

describe('the fitting-details handoff', () => {
  it('opens the fitting form pre-filled with the chosen coating and saves it against the JOB', async () => {
    mount({ jobId: 'job-1', fittingCoating: 'ARC' });
    await screen.findByText(/Asha Verma/);

    fireEvent.click(screen.getByRole('button', { name: /fitting details/i }));
    expect(screen.getByText('fitting-modal:ARC')).toBeTruthy();

    fireEvent.click(screen.getByText('save-fitting'));
    await waitFor(() => expect(updateFittingDetails).toHaveBeenCalledTimes(1));
    expect(updateFittingDetails.mock.calls[0][0]).toBe('job-1');
    expect(updateFittingDetails.mock.calls[0][1]).toMatchObject({ dia: '70', fh: '18' });

    // Saved once: the modal is gone and the button says so.
    await waitFor(() => expect(screen.queryByText('fitting-modal:ARC')).toBeNull());
    expect(screen.getByRole('button', { name: /fitting details saved/i })).toBeTruthy();
  });

  it('keeps the form open and says why when the workshop door refuses', async () => {
    updateFittingDetails.mockRejectedValue({
      response: { data: { detail: 'Job is already in progress' } },
    });
    mount({ jobId: 'job-1' });
    await screen.findByText(/Asha Verma/);

    fireEvent.click(screen.getByRole('button', { name: /fitting details/i }));
    fireEvent.click(screen.getByText('save-fitting'));
    await screen.findByText('Job is already in progress');
    expect(screen.getByText('fitting-modal:')).toBeTruthy();
  });

  it('offers no fitting form when the sale spawned no workshop job', async () => {
    mount({});
    await screen.findByText(/Asha Verma/);
    expect(screen.queryByRole('button', { name: /fitting details/i })).toBeNull();
  });
});
