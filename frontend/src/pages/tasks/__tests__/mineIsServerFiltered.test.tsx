// ============================================================================
// THE 50-TASK BUG: past 50 tasks, "Mine" showed none of your own work
// ============================================================================
// Both old task pages called GET /tasks with no params (backend default
// limit=50) and then narrowed the result IN THE BROWSER. Once a shop passed 50
// tasks, a person's own rows could all sit outside the store's first page, so
// "Mine" rendered empty while an open P0 was assigned to them.
//
// The fake below behaves like the real endpoint: it honours `assigned_to`,
// `skip` and `limit`, and its store holds 60 tasks whose OWNER's rows are all
// at the end. So:
//   * server-side filter  -> the P0 renders
//   * browser-side filter -> the request returns rows 0..49, none of them the
//     owner's, and the page says "No tasks here" -> this test fails.
//
// That is the discriminating property: revert the fix and this goes red.
//
// OWNER RULING 2026-10-12: below manager the SERVER already returns only your
// own tasks plus the ones addressed to your role at your shop
// (backend services/task_visibility.py). So a SALES_STAFF "Mine" sends NO
// assignee filter - one would hide the role's tasks - and a manager's "Mine"
// still sends assigned_to (without it a manager would get the whole shop).

import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const OWNER = 'USR-ME';
const auth = vi.hoisted(() => ({ roles: ['SALES_STAFF'] as string[], id: 'USR-ME' }));

vi.mock('../../../services/api', () => ({
  tasksApi: {
    getTasks: vi.fn(),
    completeTask: vi.fn(),
    updateTask: vi.fn(),
    reassignTask: vi.fn(),
    getTaskFile: vi.fn(),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: auth.id, activeStoreId: 'BV-PUN-01', roles: auth.roles } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { tasksApi } from '../../../services/api';
import { TasksSplitView } from '../TasksSplitView';

const getTasks = tasksApi.getTasks as unknown as ReturnType<typeof vi.fn>;

/** 60 store tasks. Rows 0-54 belong to other people; the owner's are last,
 *  and row 59 is addressed to the SALES_STAFF role with no person on it. */
const STORE = Array.from({ length: 60 }, (_, i) => ({
  task_id: `T-${i}`,
  title: i === 57 ? 'Till short by Rs 4,200' : i === 59 ? 'Count the frames wall' : `Task ${i}`,
  description: '',
  priority: i === 57 ? 'P0' : 'P3',
  status: 'OPEN',
  assigned_to: i === 59 ? 'SALES_STAFF' : i >= 55 ? OWNER : `USR-OTHER-${i}`,
  assigned_to_name: i >= 55 ? 'Me' : 'Someone else',
  due_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  created_at: new Date().toISOString(),
  store_id: 'BV-PUN-01',
}));

function renderMine() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TasksSplitView scope="mine" emptyLine="Nothing is assigned to you." />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  auth.roles = ['SALES_STAFF'];
  auth.id = OWNER;
  getTasks.mockClear();
  // A stand-in for backend/api/routers/tasks.py::list_tasks: below manager the
  // server sees only your tasks and your role's; a manager sees the shop.
  getTasks.mockImplementation(async (params: Record<string, unknown> = {}) => {
    const skip = Number(params.skip ?? 0);
    const limit = Number(params.limit ?? 50);
    const manager = auth.roles.includes('STORE_MANAGER');
    const matched = STORE.filter(
      (t) =>
        (manager || t.assigned_to === auth.id || auth.roles.includes(t.assigned_to)) &&
        (!params.assigned_to || t.assigned_to === params.assigned_to),
    );
    return { tasks: matched.slice(skip, skip + limit), total: matched.length };
  });
});

describe('Mine is filtered on the server', () => {
  it('below manager, lets the server rule decide, so the owner P0 at row 57 is visible', async () => {
    renderMine();

    expect(await screen.findByText('Till short by Rs 4,200')).toBeInTheDocument();

    const params = getTasks.mock.calls[0][0];
    expect(params.assigned_to).toBeUndefined();
    expect(params.limit).toBeGreaterThan(0);
  });

  it("shows the task addressed to the person's role at their shop", async () => {
    renderMine();
    expect(await screen.findByText('Count the frames wall')).toBeInTheDocument();
  });

  it("a manager's Mine still narrows to tasks assigned to them by name", async () => {
    auth.roles = ['STORE_MANAGER'];
    renderMine();
    expect(await screen.findByText('Till short by Rs 4,200')).toBeInTheDocument();
    expect(getTasks.mock.calls[0][0].assigned_to).toBe(OWNER);
    expect(screen.queryByText('Task 0')).not.toBeInTheDocument();
  });

  it('offers Reassign to a manager only - the server refuses it for staff', async () => {
    renderMine();
    await screen.findByRole('button', { name: /Complete/ });
    expect(screen.queryByRole('button', { name: /Reassign/ })).not.toBeInTheDocument();
  });

  it('a manager opening a task is offered Reassign', async () => {
    auth.roles = ['STORE_MANAGER'];
    renderMine();
    expect(await screen.findByRole('button', { name: /Reassign/ })).toBeInTheDocument();
  });

  it('does not show other people\'s tasks', async () => {
    renderMine();
    await screen.findByText('Till short by Rs 4,200');
    expect(screen.queryByText('Task 0')).not.toBeInTheDocument();
    expect(screen.queryByText('Someone else')).not.toBeInTheDocument();
  });

  it('reports the server-side total, not the page length', async () => {
    renderMine();
    await screen.findByText('Till short by Rs 4,200');
    // 4 of the 60 store tasks belong to the owner, 1 more to their role.
    await waitFor(() =>
      expect(screen.getByText(/5 match/)).toBeInTheDocument(),
    );
  });

  it("the next person on a shared tablet never sees the last person's cached list", async () => {
    // One QueryClient for the life of the page with a 5-minute staleTime (as
    // App.tsx keeps it), and a sign-out that does not reload the page.
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 5 * 60 * 1000 } },
    });
    const view = (scope: 'mine' | 'team') => (
      <QueryClientProvider client={qc}>
        <TasksSplitView scope={scope} emptyLine="Nothing here." />
      </QueryClientProvider>
    );
    auth.roles = ['STORE_MANAGER'];
    auth.id = 'USR-MGR';
    const first = render(view('team'));
    expect((await screen.findAllByText('Task 0')).length).toBeGreaterThan(0);
    first.unmount();

    auth.roles = ['SALES_STAFF'];
    auth.id = OWNER;
    render(view('mine'));
    expect(await screen.findByText('Till short by Rs 4,200')).toBeInTheDocument();
    expect(screen.queryByText('Task 0')).not.toBeInTheDocument();
    expect(getTasks).toHaveBeenCalledTimes(2);
  });

  it('never asks for a date window - task queues are exempt from the 30-day horizon', async () => {
    renderMine();
    await screen.findByText('Till short by Rs 4,200');
    const params = getTasks.mock.calls[0][0];
    // A horizon would arrive as one of these; `assigned_to` must not trip it.
    const HORIZON_KEYS = /^(start_date|end_date|from_date|to_date|from|to|date|days|since|window|horizon)$/i;
    for (const key of Object.keys(params)) {
      expect(key).not.toMatch(HORIZON_KEYS);
    }
  });
});
