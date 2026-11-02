// IST clock sweep lane B, FE-8: the Activity Log's default range ends on the
// IST day. NIGHT 2026-12-31T20:00Z is 1 Jan 01:30 IST; DAY is the control.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const getAuditLogs = vi.fn().mockResolvedValue({ logs: [], total: 0 });
vi.mock('../../../services/api/settings', () => ({ settingsApi: { getAuditLogs: (p: unknown) => getAuditLogs(p) } }));
vi.mock('../../../services/api/stores', () => ({
  adminUserApi: { getUsers: vi.fn().mockResolvedValue([]) },
  orgStoreApi: { list: vi.fn().mockResolvedValue({ stores: [], total: 0 }) },
}));
vi.mock('../../../services/api/entities', () => ({ entitiesApi: { list: vi.fn().mockResolvedValue({ entities: [], total: 0 }) } }));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ hasRole: () => true }) }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ error: vi.fn(), success: vi.fn() }) }));

import ActivityLogPage from '../ActivityLogPage';

afterEach(() => {
  vi.useRealTimers();
  getAuditLogs.mockClear();
});

async function rangeAt(iso: string) {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(iso));
  render(<MemoryRouter><ActivityLogPage /></MemoryRouter>);
  await waitFor(() => expect(getAuditLogs).toHaveBeenCalled());
  const p = getAuditLogs.mock.calls[0][0] as { start_date: string; end_date: string };
  return [p.start_date, p.end_date];
}

describe('ActivityLogPage default range', () => {
  it('night: ends on IST 1 Jan', async () => {
    expect(await rangeAt('2026-12-31T20:00:00Z')).toEqual(['2026-12-25', '2027-01-01']);
    expect(screen.getAllByDisplayValue('2027-01-01').length).toBeGreaterThan(0);
  });

  it('day control: ends on 31 Dec', async () => {
    expect(await rangeAt('2026-12-31T09:00:00Z')).toEqual(['2026-12-24', '2026-12-31']);
  });
});
