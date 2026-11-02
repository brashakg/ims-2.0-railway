// Owner ruling 2026-11-01: a contact-lens batch is EXPIRED on its expiry date
// itself. The server owns the rule (backend/api/utils/expiry.py) and sends
// `expired` on each line; the Contact Lens table reads it, so a batch expiring
// today (days_until_expiry 0) reads "expired", never "0d".

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'USR-1', activeStoreId: 'BV-DHN-01', roles: ['STORE_MANAGER'] } }),
}));

const line = (batch: string, days: number, expired: boolean) => ({
  product_id: `P-${batch}`, sku: `SKU-${batch}`, brand: 'Acuvue', model: 'Oasys', category: 'CL',
  cl_series: null, modality: 'DAILY', base_curve: 8.5, diameter: 14.2, cl_power: -2,
  cl_cyl: null, cl_axis: null, color: null, pack_size: 30, batch_code: batch,
  expiry_date: '2026-12-31', on_hand: 2, days_until_expiry: days, expired,
});

vi.mock('../../../services/api', () => ({
  default: {
    get: vi.fn(async () => ({ data: { items: [line('TODAY', 0, true), line('TMRW', 1, false)] } })),
  },
}));

import { ContactLensInventoryWidget } from '../AdvancedInventoryFeatures';

describe('Contact Lens table: expiry date itself reads expired', () => {
  it('shows "expired" for the day-0 batch and "1d" for tomorrow', async () => {
    render(<ContactLensInventoryWidget />);
    const today = (await screen.findByText('TODAY', { selector: 'td' })).closest('tr')!;
    const tmrw = screen.getByText('TMRW', { selector: 'td' }).closest('tr')!;
    expect(today.textContent).toContain('(expired)');
    expect(today.textContent).not.toContain('(0d)');
    expect(tmrw.textContent).toContain('(1d)');
  });
});
