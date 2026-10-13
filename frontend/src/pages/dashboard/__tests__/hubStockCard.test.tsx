// D7b round 12 on #1180: the Hub's 'Inventory' card sent a SALES_STAFF,
// CASHIER or OPTOMETRIST to /inventory, whose module gate leaves them out, so
// the card landed on /unauthorized. The card now asks the two routes' own
// gates (canOpenPath): whoever cannot open the module but can open the
// read-only lookup gets 'Stock lookup' -> /inventory/lookup. SALES_CASHIER is
// folded into SALES_STAFF at sign-in, so no browser session holds it.
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

let roles: string[] = [];
vi.mock('../../../context/AuthContext', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useAuth: () => ({
    user: { name: 'Test', roles, activeStoreId: 'S1' },
    hasRole: (want: string | string[]) =>
      roles.includes('SUPERADMIN') || roles.includes('ADMIN') || [want].flat().some((r) => roles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useToast: () => ({ error: vi.fn(), success: vi.fn() }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../services/api', async (orig) => {
  const never = () => new Promise(() => {});
  return {
    ...(await orig<Record<string, unknown>>()),
    analyticsApi: { getDashboardSummary: never },
    tasksApi: { getTaskSummary: never, getTasks: never, getSopTemplates: never },
    clinicalApi: { getQueue: never },
  };
});
// The Hub's side cards fetch their own data; this test is about the grid.
for (const m of [
  '../../../components/handoffs/HandoffInboxCard',
  '../../../components/handoffs/HandoffUploadModal',
  '../../../components/handoffs/ClinicalHandoverCard',
  '../../../components/tasks/NewTaskModal',
  '../../../components/notifications/DashboardNotifications',
  '../../../components/dashboard/OwnerDigestCard',
  '../../../components/hub/TickerCard',
]) vi.doMock(m, () => ({ default: () => null, HandoffUploadModal: () => null, NewTaskModal: () => null }));

async function stockCard(role: string) {
  roles = [role];
  const { default: HubPage } = await import('../HubPage');
  render(<MemoryRouter><HubPage /></MemoryRouter>);
  const link = screen.getByRole('heading', { name: /^(Inventory|Stock lookup)$/ }).closest('a')!;
  return [link.textContent!.includes('Stock lookup') ? 'Stock lookup' : 'Inventory', link.getAttribute('href')];
}

describe('the Hub stock card opens a screen the role can open', () => {
  it.each(['SALES_STAFF', 'CASHIER', 'OPTOMETRIST'])('%s gets the read-only Stock lookup', async (role) => {
    expect(await stockCard(role)).toEqual(['Stock lookup', '/inventory/lookup']);
  });

  it.each(['ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'])('%s keeps the Inventory module', async (role) => {
    expect(await stockCard(role)).toEqual(['Inventory', '/inventory']);
  });
});
