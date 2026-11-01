// The DC-hardlock banner links to /purchase/receive, which is managers-only.
// Workshop staff must not see the link (they would land on 'not allowed').
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

let currentRole = 'WORKSHOP_STAFF';
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ hasRole: (roles: string[]) => roles.includes(currentRole) }),
}));

import { CreateJobModal } from '../CreateJobModal';
import type { WorkshopPageState } from '../useWorkshopPage';

const page = {
  showCreateJob: true,
  createSelectedOrder: { orderNumber: 'ORD-1', customerName: 'A', items: [] },
  dcHardlock: 'No Delivery Challan logged for this lens.',
  canOverrideHardlock: false,
  createOrders: [],
  createOrderSearch: '',
  createPriority: 'NORMAL',
  createExpectedDate: '',
  createFitting: '',
  createNotes: '',
  overrideReason: '',
  createLoading: false,
} as unknown as WorkshopPageState;

const LINK = 'Go to Receive goods (delivery challan)';

function renderAs(role: string) {
  currentRole = role;
  return render(<MemoryRouter><CreateJobModal page={page} /></MemoryRouter>);
}

describe('CreateJobModal receive-goods link', () => {
  it('is hidden from workshop staff', () => {
    renderAs('WORKSHOP_STAFF');
    expect(screen.getByText('No Delivery Challan logged for this lens.')).toBeTruthy();
    expect(screen.queryByText(LINK)).toBeNull();
  });

  it('is shown to a store manager and points at /purchase/receive', () => {
    renderAs('STORE_MANAGER');
    expect(screen.getByText(LINK).closest('a')?.getAttribute('href')).toBe('/purchase/receive');
  });
});
