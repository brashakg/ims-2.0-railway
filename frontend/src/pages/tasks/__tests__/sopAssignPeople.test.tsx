// ============================================================================
// SOPs ARE ASSIGNED TO PEOPLE BY NAME
// ============================================================================
// Owner 2026-09-03: "for tasks and sop instead of assigning it to store
// manager, Cashier, optom, assign it to individuals like sameer, rupesh".
//
// Three behaviours guarded here, each of which FAILS if its fix is reverted:
//   1. toForm no longer hardcodes assigned_users: [] - reverting that (or the
//      assignedUsers mapping in tasksQueries) wipes stored assignees on every
//      edit-save, and the pass-through probe below reads [].
//   2. The card shows the assignee's NAME, resolved from the store staff list
//      - reverting the resolution shows the raw user id.
//   3. The Assign flow posts the picked person's ID and returns the existing
//      roles UNCHANGED - reverting the picker removes the button; dropping
//      role preservation changes the posted payload.
// The staff fixture supplies names for ids, but never the id->name RESOLUTION
// or the /assign payload - those come from the page under test.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../services/api', () => ({
  tasksApi: {
    getSopTemplates: vi.fn(),
    assignSop: vi.fn(async () => ({ success: true })),
    seedDefaultSops: vi.fn(),
  },
}));
vi.mock('../../../services/api/stores', () => ({
  adminStoreApi: {
    getStoreUsers: vi.fn(async () => ({
      users: [
        { user_id: 'USR-sameer', name: 'Sameer', roles: ['SALES_STAFF'] },
        { user_id: 'USR-rupesh', name: 'Rupesh', roles: ['OPTOMETRIST'] },
      ],
    })),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'USR-1', activeStoreId: 'BV-PUN-01', roles: ['STORE_MANAGER'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
// Probe: renders EXACTLY the assigned_users the page hands the editor, so the
// assertion is on the page's prop, not on anything this mock invents.
vi.mock('../../../components/tasks/SopEditorModal', () => ({
  SopEditorModal: ({ isOpen, initial }: any) =>
    isOpen ? (
      <div data-testid="editor-assigned-users">{JSON.stringify(initial?.assigned_users ?? null)}</div>
    ) : null,
}));

import { tasksApi } from '../../../services/api';
import { TasksSopPage } from '../TasksSopPage';

const getSopTemplates = tasksApi.getSopTemplates as unknown as ReturnType<typeof vi.fn>;
const assignSop = tasksApi.assignSop as unknown as ReturnType<typeof vi.fn>;

function template(overrides: Record<string, unknown> = {}) {
  return {
    template_id: 'SOP-1',
    title: 'Close the till',
    description: 'Evening closing routine',
    category: 'Operations',
    frequency: 'DAILY',
    estimated_time: 15,
    steps: [{ step_number: 1, instruction: 'Count the cash drawer' }],
    assigned_roles: ['STORE_MANAGER'],
    assigned_users: ['USR-sameer'],
    store_id: 'BV-PUN-01',
    is_active: true,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-01T10:00:00Z',
    ...overrides,
  };
}

function renderSops() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TasksSopPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = '';
  getSopTemplates.mockResolvedValue({ templates: [template()], total: 1 });
});

describe('SOP assignment to named people', () => {
  it('shows the assigned person by NAME on the card, never the raw user id', async () => {
    const { container } = renderSops();
    await screen.findByText('Close the till');
    // Name resolution can land a tick after staff loads.
    expect(await screen.findByText('Sameer')).toBeInTheDocument();
    expect(container.textContent).not.toContain('USR-sameer');
  });

  it('editing an SOP passes its stored assigned_users into the editor (no wipe)', async () => {
    renderSops();
    await screen.findByText('Close the till');
    fireEvent.click(screen.getByRole('button', { name: /Edit SOP/i }));
    const probe = await screen.findByTestId('editor-assigned-users');
    // Reverting toForm's pass-through (or the assignedUsers mapping in
    // tasksQueries) renders [] here and this fails.
    expect(probe.textContent).toContain('USR-sameer');
  });

  it('assigning a person posts their user id and returns roles unchanged', async () => {
    renderSops();
    await screen.findByText('Close the till');
    fireEvent.click(screen.getByRole('button', { name: /Assign people/i }));

    // People are listed by name; tick Rupesh (Sameer is already ticked).
    const rupesh = await screen.findByRole('checkbox', { name: 'Rupesh' });
    expect((screen.getByRole('checkbox', { name: 'Sameer' }) as HTMLInputElement).checked).toBe(true);
    expect((rupesh as HTMLInputElement).checked).toBe(false);
    fireEvent.click(rupesh);
    fireEvent.click(screen.getByRole('button', { name: /Save assignment/i }));

    await waitFor(() =>
      expect(assignSop).toHaveBeenCalledWith('SOP-1', {
        assigned_roles: ['STORE_MANAGER'],
        assigned_users: ['USR-sameer', 'USR-rupesh'],
      }),
    );
  });

  it('a stored assignee missing from the store list is still shown and removable', async () => {
    getSopTemplates.mockResolvedValue({
      templates: [template({ assigned_users: ['USR-ghost'] })],
      total: 1,
    });
    renderSops();
    await screen.findByText('Close the till');
    fireEvent.click(screen.getByRole('button', { name: /Assign people/i }));

    // No name is known for the ghost, so the id itself is the label - but the
    // row must exist, or saving would silently drop the assignment.
    const ghost = await screen.findByRole('checkbox', { name: 'USR-ghost' });
    expect((ghost as HTMLInputElement).checked).toBe(true);
    fireEvent.click(ghost);
    fireEvent.click(screen.getByRole('button', { name: /Save assignment/i }));

    await waitFor(() =>
      expect(assignSop).toHaveBeenCalledWith('SOP-1', {
        assigned_roles: ['STORE_MANAGER'],
        assigned_users: [],
      }),
    );
  });
});
