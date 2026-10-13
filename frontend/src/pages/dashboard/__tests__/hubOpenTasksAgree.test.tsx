// ============================================================================
// The Hub's 'Open Tasks' figure and its 'Priority tasks' list agree
// ============================================================================
// Owner ruling 2026-10-12: below manager a person sees only their own tasks,
// so ONE started task is the normal case. The Hub used to count OPEN +
// IN_PROGRESS but list OPEN only, and a sales person whose only task was in
// progress read 'Open Tasks 1' beside 'Nothing urgent - you're all caught up.'
//
// The fake below behaves like GET /tasks: it honours the status list it is
// asked for (comma-separated) and reports the matching total. Ask for OPEN
// only and the started task disappears -> this test goes red.

import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../services/api', () => ({
  analyticsApi: { getDashboardSummary: vi.fn() },
  clinicalApi: { getQueue: vi.fn() },
  tasksApi: { getTasks: vi.fn(), getTaskSummary: vi.fn(), getSopTemplates: vi.fn() },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-sales', activeStoreId: 'BV-S1', roles: ['SALES_STAFF'], activeRole: 'SALES_STAFF' },
    hasRole: (roles: string[]) => roles.includes('SALES_STAFF'),
    hasModuleAccess: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../components/shell', () => ({
  Icon: new Proxy({}, { get: () => () => null }),
}));
vi.mock('../../../components/handoffs/HandoffInboxCard', () => ({ default: () => null }));
vi.mock('../../../components/handoffs/HandoffUploadModal', () => ({ HandoffUploadModal: () => null }));
vi.mock('../../../components/handoffs/ClinicalHandoverCard', () => ({ default: () => null }));
vi.mock('../../../components/tasks/NewTaskModal', () => ({ NewTaskModal: () => null }));
vi.mock('../../../components/notifications/DashboardNotifications', () => ({ default: () => null }));
vi.mock('../../../components/dashboard/OwnerDigestCard', () => ({ default: () => null }));
vi.mock('../../../components/hub/TickerCard', () => ({ default: () => null }));

import { analyticsApi, clinicalApi, tasksApi } from '../../../services/api';
import HubPage from '../HubPage';

const mocked = (f: unknown) => f as ReturnType<typeof vi.fn>;

// The sales person's only task: they pressed Start on it.
const MINE = [
  { task_id: 'T-1', title: "Fit Mr Iyer's new lenses", priority: 'P2', status: 'IN_PROGRESS', due_at: null },
];

beforeEach(() => {
  mocked(analyticsApi.getDashboardSummary).mockResolvedValue({});
  mocked(clinicalApi.getQueue).mockResolvedValue([]);
  mocked(tasksApi.getSopTemplates).mockResolvedValue({ templates: [] });
  mocked(tasksApi.getTaskSummary).mockResolvedValue({ open: 1, overdue: 0 });
  mocked(tasksApi.getTasks).mockImplementation(async (params: { status?: string } = {}) => {
    const wanted = params.status ? params.status.split(',') : null;
    const tasks = MINE.filter((t) => !wanted || wanted.includes(t.status));
    return { tasks, total: tasks.length };
  });
});

describe('Hub task figure and list', () => {
  it('a started task is listed, never "Nothing urgent" beside "Open Tasks 1"', async () => {
    render(
      <MemoryRouter>
        <HubPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Fit Mr Iyer's new lenses")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing urgent/)).not.toBeInTheDocument();
    const card = screen.getByText('Open Tasks').parentElement as HTMLElement;
    expect(within(card).getByText('1')).toBeInTheDocument();
  });
});
