// ============================================================================
// Hub "Priority tasks" - which shop it asks for (panel 2026-09-29, audit C1)
// ============================================================================
// Below manager the server lists only your OWN tasks (owner 09-03). A
// catalogue manager reaches every shop, and a receipt task sits at the
// receipt's shop: asking for the ACTIVE shop only hid it. Managers keep the
// active-shop view of the whole shop.

import { render, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

let roles: string[] = [];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Test User', activeStoreId: 'BV-DHN-02', roles },
    hasRole: (want: string[]) => want.some((r) => roles.includes(r)),
    hasModuleAccess: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../services/api', () => ({
  analyticsApi: { getDashboardSummary: vi.fn(async () => ({})) },
  clinicalApi: { getQueue: vi.fn(async () => []) },
  tasksApi: {
    getTaskSummary: vi.fn(async () => ({})),
    getTasks: vi.fn(async () => ({ tasks: [] })),
    getSopTemplates: vi.fn(async () => ({ templates: [] })),
  },
}));
vi.mock('../../../components/handoffs/HandoffInboxCard', () => ({ default: () => null }));
vi.mock('../../../components/handoffs/HandoffUploadModal', () => ({ HandoffUploadModal: () => null }));
vi.mock('../../../components/handoffs/ClinicalHandoverCard', () => ({ default: () => null }));
vi.mock('../../../components/tasks/NewTaskModal', () => ({ NewTaskModal: () => null }));
vi.mock('../../../components/notifications/DashboardNotifications', () => ({ default: () => null }));
vi.mock('../../../components/dashboard/OwnerDigestCard', () => ({ default: () => null }));
vi.mock('../../../components/hub/TickerCard', () => ({ default: () => null }));

import { tasksApi } from '../../../services/api';
import HubPage from '../HubPage';

const getTasks = tasksApi.getTasks as unknown as ReturnType<typeof vi.fn>;

const renderAs = (r: string[]) => {
  roles = r;
  return render(
    <MemoryRouter>
      <HubPage />
    </MemoryRouter>,
  );
};

beforeEach(() => getTasks.mockClear());

describe('Hub Priority tasks', () => {
  it('asks for a catalogue manager’s own tasks at every shop', async () => {
    renderAs(['CATALOG_MANAGER']);
    await waitFor(() => expect(getTasks).toHaveBeenCalled());
    expect(getTasks).toHaveBeenCalledWith({ status: 'OPEN' });
  });

  it('asks for the active shop for a store manager', async () => {
    renderAs(['STORE_MANAGER']);
    await waitFor(() => expect(getTasks).toHaveBeenCalled());
    expect(getTasks).toHaveBeenCalledWith({ store_id: 'BV-DHN-02', status: 'OPEN' });
  });
});
