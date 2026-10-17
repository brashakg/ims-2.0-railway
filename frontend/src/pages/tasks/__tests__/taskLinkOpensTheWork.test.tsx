// ============================================================================
// A system task names the page where its work is done (panel 2026-09-29)
// ============================================================================
// A receipt holding units for an item not catalogued yet raises a task for
// the catalogue manager with link '/catalog/review'; nothing rendered it, so
// the assignee read "finish them from Catalogue > Needs review" and had to
// find it by hand. Only a same-app path is ever rendered as a link.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

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
  useAuth: () => ({ user: { id: 'u-cat-hq', activeStoreId: 'BV-DHN-02', roles: ['CATALOG_MANAGER'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { tasksApi } from '../../../services/api';
import { TasksSplitView } from '../TasksSplitView';

const getTasks = tasksApi.getTasks as unknown as ReturnType<typeof vi.fn>;

const task = (link: string) => ({
  task_id: 'TSK-1',
  title: 'Finish 1 item(s) held on receipt RCPT/BV-DHN-02/26-27/0001',
  description: 'Finish them from Catalogue > Needs review.',
  priority: 'P2',
  status: 'OPEN',
  assigned_to: 'u-cat-hq',
  due_at: new Date(Date.now() + 3600_000).toISOString(),
  created_at: new Date().toISOString(),
  store_id: 'BV-DHN-02',
  link,
});

function renderMine() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TasksSplitView scope="mine" emptyLine="Nothing is assigned to you." />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => getTasks.mockReset());

describe("a task's link", () => {
  it('opens the page where the work is done', async () => {
    getTasks.mockResolvedValue({ tasks: [task('/catalog/review')], total: 1 });
    renderMine();
    const link = await screen.findByRole('link', { name: /Open where this is done/ });
    expect(link).toHaveAttribute('href', '/catalog/review');
  });

  // Browsers read '/\\host' as '//host' and strip tabs/newlines from a URL.
  it.each(['//evil.example/x', '/\\evil.example/x', '/\t/evil.example/x', 'https://evil.example/x'])(
    'is never rendered for an address outside the app (%j)',
    async (href) => {
      getTasks.mockResolvedValue({ tasks: [task(href)], total: 1 });
      renderMine();
      await screen.findAllByText(/Finish 1 item\(s\) held/);
      expect(screen.queryByRole('link', { name: /Open where this is done/ })).toBeNull();
    },
  );
});

// Main's catalogue-flag task carries `links` (each flagged product's edit
// page): the same one in-app rule (sameAppPath) decides which are rendered.
describe("a task's links", () => {
  it('render only the addresses inside the app', async () => {
    getTasks.mockResolvedValue({
      tasks: [
        {
          ...task(''),
          links: [
            { label: 'Fix Boss 1700', to: '/catalog/add?edit=P-1' },
            { label: 'Backslash host', to: '/\\evil.example/x' },
            { label: 'Tab host', to: '/\t/evil.example/x' },
          ],
        },
      ],
      total: 1,
    });
    renderMine();
    const good = await screen.findByRole('link', { name: 'Fix Boss 1700' });
    expect(good).toHaveAttribute('href', '/catalog/add?edit=P-1');
    expect(screen.queryByRole('link', { name: 'Backslash host' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Tab host' })).toBeNull();
  });
});
