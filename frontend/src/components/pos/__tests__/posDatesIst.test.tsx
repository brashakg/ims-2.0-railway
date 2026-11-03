// ============================================================================
// Till dates use the IST calendar day, not the UTC one
// ============================================================================
// At 00:30 IST on 3 Nov the UTC clock still reads 19:00 on 2 Nov. Three till
// screens took "today" from toISOString(), so after midnight and before 05:30
// IST they stamped / limited / defaulted to YESTERDAY. All now use the shared
// istDayString helper. Clock pinned to 2026-11-02T19:00Z (= 03 Nov 00:30 IST).
import { render } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', name: 'Sales' } }),
}));
vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    orderApi: { ...(actual.orderApi as object), quoteBill: vi.fn(), createOrder: vi.fn(), addPayment: vi.fn() },
    workshopApi: { ...(actual.workshopApi as object), createJob: vi.fn() },
  };
});

import { orderApi, workshopApi } from '../../../services/api';
import { usePOSStore } from '../../../stores/posStore';
import { submitPosOrder } from '../submitOrder';
import { LensFittingFormModal } from '../LensFittingFormModal';
import { PrescriptionForm } from '../PrescriptionForm';

const IST_TODAY = '2026-11-03';
const IST_PLUS_5 = '2026-11-08';

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-02T19:00:00Z'));
});
afterEach(() => vi.useRealTimers());

const dateValues = (c: HTMLElement) =>
  Array.from(c.querySelectorAll<HTMLInputElement>('input[type="date"]')).map((i) => i.value);

describe('till dates at 00:30 IST', () => {
  it('lens fitting form defaults order date and receive date from the IST day', () => {
    const { container } = render(<LensFittingFormModal onSave={() => {}} onBack={() => {}} />);
    const values = dateValues(container);
    expect(values).toContain(IST_TODAY);
    expect(values).toContain(IST_PLUS_5);
  });

  it("prescription form's next-checkup minimum is the IST day", () => {
    const { container } = render(
      <PrescriptionForm onSubmit={() => {}} onCancel={() => {}} allowContactLens={false} />,
    );
    const mins = Array.from(container.querySelectorAll<HTMLInputElement>('input[type="date"]')).map((i) => i.min);
    expect(mins).toContain(IST_TODAY);
  });

  it('workshop job expected date is IST day + 5', async () => {
    vi.mocked(orderApi.quoteBill).mockResolvedValue({
      subtotal: 1000, taxable: 952.38, tax: 47.62, total_discount: 0, grand_total: 1000, round_off: 0,
    } as never);
    vi.mocked(orderApi.createOrder).mockResolvedValue({ order_id: 'ord-1', order_number: 'BV-1' } as never);
    vi.mocked(orderApi.addPayment).mockResolvedValue({} as never);
    vi.mocked(workshopApi.createJob).mockResolvedValue({ job_id: 'job-1' } as never);
    const s = usePOSStore.getState();
    s.resetTransaction('discard');
    s.addToCart({
      product_id: 'p-frame', name: 'Frame', sku: 'S', category: 'FRAME',
      unit_price: 1000, mrp: 1000, quantity: 1, is_optical: true,
    } as never);
    usePOSStore.getState().addPayment({ method: 'CASH', amount: 1000 } as never);
    const state = { ...usePOSStore.getState(), sale_type: 'prescription_order', prescription: { id: 'rx-1' } };
    await submitPosOrder(state as never, 'idem-ist');
    expect(vi.mocked(workshopApi.createJob)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(workshopApi.createJob).mock.calls[0][0]).toMatchObject({ expected_date: IST_PLUS_5 });
  });
});
