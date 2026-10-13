// ============================================================================
// Performance: the totals and the owner ranking cover the same tasks
// ============================================================================
// The headline counts came from GET /tasks/summary for the ACTIVE shop, while
// the owner ranking was built from GET /tasks with no shop - every shop the
// person reaches. An area manager read 'Total tasks 2' for one shop beside a
// ranking of every shop in the area. Both now ask for the active shop.
//
// The fake below behaves like the real endpoints: both honour store_id. Drop
// the shop from the list and the other shop's people join the ranking -> red.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../services/api', () => ({
  tasksApi: { getTasks: vi.fn(), getTaskSummary: vi.fn() },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-area', activeStoreId: 'BV-S1', roles: ['AREA_MANAGER'] } }),
}));
vi.mock('../../../components/tasks/SystemIntegrityPanel', () => ({ default: () => null }));

import { tasksApi } from '../../../services/api';
import { TasksPerformancePage } from '../TasksPerformancePage';

const mocked = (f: unknown) => f as ReturnType<typeof vi.fn>;

const AREA = [
  { task_id: 'A1', store_id: 'BV-S1', assigned_to: 'u-ravi', assigned_to_name: 'Ravi', status: 'COMPLETED' },
  { task_id: 'A2', store_id: 'BV-S1', assigned_to: 'u-ravi', assigned_to_name: 'Ravi', status: 'OPEN' },
  { task_id: 'B1', store_id: 'BV-S2', assigned_to: 'u-meera', assigned_to_name: 'Meera', status: 'COMPLETED' },
  { task_id: 'B2', store_id: 'BV-S2', assigned_to: 'u-meera', assigned_to_name: 'Meera', status: 'COMPLETED' },
  { task_id: 'B3', store_id: 'BV-S2', assigned_to: 'u-meera', assigned_to_name: 'Meera', status: 'COMPLETED' },
];
const inShop = (storeId?: string) => AREA.filter((t) => !storeId || t.store_id === storeId);

beforeEach(() => {
  mocked(tasksApi.getTasks).mockImplementation(async (p: { store_id?: string } = {}) => {
    const tasks = inShop(p.store_id);
    return { tasks, total: tasks.length };
  });
  mocked(tasksApi.getTaskSummary).mockImplementation(async (storeId?: string) => {
    const tasks = inShop(storeId);
    const done = tasks.filter((t) => t.status === 'COMPLETED').length;
    return { summary: { COMPLETED: done }, total: tasks.length, overdue: 0 };
  });
});

describe('Performance page scope', () => {
  it('ranks the same shop its totals count', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <TasksPerformancePage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Ravi')).toBeInTheDocument();
    expect(screen.getByText(/Computed from the 2 tasks/)).toBeInTheDocument();
    expect(await screen.findByText('1/2')).toBeInTheDocument();
    expect(screen.queryByText('Meera')).not.toBeInTheDocument();
  });
});
