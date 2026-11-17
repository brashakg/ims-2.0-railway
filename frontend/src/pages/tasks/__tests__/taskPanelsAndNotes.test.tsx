// ============================================================================
// Tasks screens: audit 2026-11-04 R05, R07, R26, R45
// ============================================================================
// R05  every saved note is shown in the task detail, oldest first.
// R07  the automatic pick of the most urgent task must not open the
//      list-hiding detail on a narrow screen; only a tap does, Back closes it.
// R26  a failed checklist read / task-list read is an error with Retry, not
//      "Select a checklist above." / "No tasks yet".
// R45  a failed integrity count reads "Unavailable", never 0.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../services/api', () => ({
  tasksApi: {
    getTasks: vi.fn(),
    getTaskSummary: vi.fn(),
    getSopTemplates: vi.fn(),
    getSopChecklist: vi.fn(),
    getSilentTasks: vi.fn(),
    getFakeClosures: vi.fn(),
    scanPaymentVariance: vi.fn(),
    toggleSopChecklistItem: vi.fn(),
    completeTask: vi.fn(),
    updateTask: vi.fn(),
    reassignTask: vi.fn(),
    getTaskFile: vi.fn(),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-sm', activeStoreId: 'S1', roles: ['STORE_MANAGER'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { tasksApi } from '../../../services/api';
import { TasksSplitView } from '../TasksSplitView';
import { TasksChecklistPage } from '../TasksChecklistPage';
import { TasksPerformancePage } from '../TasksPerformancePage';

const api = tasksApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const outage = (status: number) => () =>
  Promise.reject(Object.assign(new Error('read failed'), { response: { status } }));

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>{node}</QueryClientProvider>
    </MemoryRouter>,
  );
}

const TASKS = [
  {
    task_id: 'TASK-9325FFD4',
    title: 'Count the display',
    description: 'Brief',
    priority: 'P1',
    status: 'OPEN',
    assigned_to: 'u-cash',
    store_id: 'S1',
    due_at: new Date(Date.now() + 3600_000).toISOString(),
    notes: 'Second count: display two has 8 test units.',
    notes_log: [
      { text: 'First count: display one has 12 test units.', by: 'u-cash', by_name: 'Cashier One', at: '2026-11-04T05:20:00Z' },
      { text: 'Second count: display two has 8 test units.', by: 'u-cash', by_name: 'Cashier One', at: '2026-11-04T05:25:00Z' },
    ],
  },
  { task_id: 'TASK-2', title: 'Wipe the counter', description: '', priority: 'P3', status: 'OPEN', assigned_to: 'u-cash', store_id: 'S1' },
];

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.getTasks.mockResolvedValue({ tasks: TASKS, total: TASKS.length });
  api.getTaskSummary.mockResolvedValue({ summary: { COMPLETED: 3 }, total: 7, overdue: 0 });
  api.getSilentTasks.mockResolvedValue({ silent: [], count: 2 });
  api.getFakeClosures.mockResolvedValue({ flagged: [], count: 1 });
});

describe('R07 + R05 task list and detail', () => {
  it('auto-selection keeps the list; a tap opens the detail; Back closes it', async () => {
    const { container } = wrap(<TasksSplitView scope="team" emptyLine="none" />);
    await screen.findByRole('heading', { name: 'Count the display' });
    const body = container.querySelector('.t-body') as HTMLElement;
    expect(body.classList.contains('detail-open')).toBe(false);

    fireEvent.click(screen.getByText('Wipe the counter'));
    expect(body.classList.contains('detail-open')).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Close detail' }));
    expect(body.classList.contains('detail-open')).toBe(false);
  });

  it('shows every saved note, oldest first, with who wrote it', async () => {
    wrap(<TasksSplitView scope="team" emptyLine="none" />);
    const first = await screen.findByText('First count: display one has 12 test units.');
    const second = screen.getByText('Second count: display two has 8 test units.');
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByText(/Cashier One/).length).toBeGreaterThanOrEqual(2);
  });

  it('the phone drawer opens on the class the screen sets, not a body flag nobody sets', () => {
    const css = readFileSync(
      join(__dirname, '..', '..', '..', 'styles', 'mobile.css'),
      'utf8',
    ).replace(/\/\*[\s\S]*?\*\//g, '');
    expect(css).toMatch(/\.t-body\.detail-open \.t-detail\s*\{\s*transform: translateX\(0\)/);
    expect(css).not.toContain('data-task-open');
  });
});

describe('R26 partial outages are errors, not empty states', () => {
  it('a failed checklist read says so and Retry recovers', async () => {
    api.getSopTemplates.mockResolvedValue({
      templates: [{ template_id: 'SOP-1', title: 'Opening', frequency: 'DAILY' }],
    });
    api.getSopChecklist.mockImplementationOnce(outage(503)).mockResolvedValue({
      template_id: 'SOP-1',
      date: '2026-11-04',
      status: 'OPEN',
      progress: { done: 0, total: 1, percent: 0 },
      items: [{ step_number: 1, instruction: 'Unlock the safe', completed: false }],
    });
    wrap(<TasksChecklistPage />);
    expect(await screen.findByText(/Could not load today's ticks/)).toBeInTheDocument();
    expect(screen.queryByText('Select a checklist above.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Unlock the safe')).toBeInTheDocument();
  });

  it('a failed task list is not "No tasks yet" beside real totals', async () => {
    api.getTasks.mockImplementationOnce(outage(503));
    wrap(<TasksPerformancePage />);
    expect(
      await screen.findByText(/Could not load the tasks this ranking is built from/),
    ).toBeInTheDocument();
    expect(screen.queryByText('No tasks yet')).toBeNull();
    const retries = screen.getAllByRole('button', { name: 'Retry' });
    fireEvent.click(retries[retries.length - 1]);
    expect((await screen.findAllByText('u-cash')).length).toBeGreaterThan(0);
  });
});

describe('R45 integrity counts', () => {
  it('a failed read is Unavailable, the other count stays, Retry recovers', async () => {
    api.getSilentTasks.mockImplementationOnce(outage(400));
    wrap(<TasksPerformancePage />);
    expect(await screen.findByText('Unavailable')).toBeInTheDocument();
    const fastClosed = screen.getByText(/Fast-closed/).parentElement as HTMLElement;
    const silentBox = screen.getByText(/Silent \(unacknowledged\)/).parentElement as HTMLElement;
    await waitFor(() => expect(fastClosed.textContent).toContain('1')); // the other count stays
    expect(screen.getByText(/this is not a zero/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText('Unavailable')).toBeNull());
    await waitFor(() => expect(silentBox.textContent).toContain('2'));
  });
});
