// R08 (audit 2026-11-04): an ACCOUNTANT's Hub offered the POS card, and the
// click landed on /unauthorized. Every Hub workspace card must pass the SAME
// route gate (canOpenPath -> gateAllows) its destination screen runs.
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { canOpenPath } from '../../../components/layout/AppLayout';

let roles: string[] = [];
let online = false;
const authFor = () => ({
  user: { name: 'Test', roles, activeRole: roles[0], activeStoreId: 'S1', storeIds: ['S1'] },
  isAuthenticated: true,
  isLoading: false,
  // The real AuthContext.hasRole: ADMIN / SUPERADMIN satisfy any role list.
  hasRole: (want: string | string[]) =>
    roles.includes('SUPERADMIN') || roles.includes('ADMIN') || [want].flat().some((r) => roles.includes(r)),
  hasPermission: () => true,
  hasModuleAccess: () => true,
});
vi.mock('../../../context/AuthContext', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useAuth: () => authFor(),
}));
vi.mock('../../../context/ToastContext', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useToast: () => ({ error: vi.fn(), success: vi.fn() }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => online }));
vi.mock('../../../services/api', async (orig) => {
  const never = () => new Promise(() => {});
  return {
    ...(await orig<Record<string, unknown>>()),
    analyticsApi: { getDashboardSummary: never },
    tasksApi: { getTaskSummary: never, getTasks: never, getSopTemplates: never },
    clinicalApi: { getQueue: never },
  };
});
for (const m of [
  '../../../components/handoffs/HandoffInboxCard',
  '../../../components/handoffs/HandoffUploadModal',
  '../../../components/handoffs/ClinicalHandoverCard',
  '../../../components/tasks/NewTaskModal',
  '../../../components/notifications/DashboardNotifications',
  '../../../components/dashboard/OwnerDigestCard',
  '../../../components/hub/TickerCard',
]) vi.doMock(m, () => ({ default: () => null, HandoffUploadModal: () => null, NewTaskModal: () => null }));

async function hubCards(role: string, onlineStore = false) {
  roles = [role];
  online = onlineStore;
  const { default: HubPage } = await import('../HubPage');
  const { container, unmount } = render(<MemoryRouter><HubPage /></MemoryRouter>);
  const hrefs = [...container.querySelectorAll('section.modules a.mod-card')].map((a) => a.getAttribute('href')!);
  const count = container.querySelector('section.modules .sub')?.textContent;
  unmount();
  return { hrefs, count };
}

const ROLES = [
  'SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CATALOG_MANAGER',
  'OPTOMETRIST', 'SALES_STAFF', 'CASHIER', 'WORKSHOP_STAFF', 'DESIGN_MANAGER', 'INVESTOR',
];

describe('R08: every Hub card opens for the signed-in role', () => {
  it.each(ROLES)('%s (shop) is offered only screens its route gate lets in', async (role) => {
    const { hrefs, count } = await hubCards(role);
    const auth = authFor() as unknown as Parameters<typeof canOpenPath>[0];
    expect(hrefs.filter((h) => !canOpenPath(auth, h))).toEqual([]);
    expect(count).toBe(`${hrefs.length} modules · role-gated`);
  });

  it.each(ROLES)('%s (online store) is offered only screens its route gate lets in', async (role) => {
    const { hrefs } = await hubCards(role, true);
    const auth = authFor() as unknown as Parameters<typeof canOpenPath>[0];
    expect(hrefs.filter((h) => !canOpenPath(auth, h))).toEqual([]);
  });

  it('ACCOUNTANT no longer gets POS; a SALES_STAFF and an ADMIN still do', async () => {
    expect((await hubCards('ACCOUNTANT')).hrefs).not.toContain('/pos/new');
    expect((await hubCards('SALES_STAFF')).hrefs).toContain('/pos/new');
    expect((await hubCards('ADMIN')).hrefs).toContain('/pos/new');
  });
});
