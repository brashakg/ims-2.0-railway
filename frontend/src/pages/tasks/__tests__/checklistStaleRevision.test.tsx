// ============================================================================
// A STALE CHECKLIST PAGE MUST NOT SIGN A DIFFERENT INSTRUCTION (R28, D47)
// ============================================================================
// Codex PR-1436 round 1: the page sent only step_number, so after a colleague
// reordered the SOP, ticking "Count display pieces" signed "Verify safe is
// locked". The page now sends the step's id and the revision it was read
// with; the server refuses a stale one (409) and the page reloads.

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const toastError = vi.fn();

vi.mock('../../../services/api', () => ({
  tasksApi: {
    getSopTemplates: vi.fn(async () => ({
      templates: [{ template_id: 'SOP-1', title: 'Closing', frequency: 'DAILY', steps: [] }],
      total: 1,
    })),
    getSopChecklist: vi.fn(),
    toggleSopChecklistItem: vi.fn(),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'USR-1', activeStoreId: 'AUDTA', roles: ['STORE_MANAGER'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: toastError, info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

import { tasksApi } from '../../../services/api';
import { TasksChecklistPage } from '../TasksChecklistPage';

const getSopChecklist = tasksApi.getSopChecklist as unknown as ReturnType<typeof vi.fn>;
const toggleItem = tasksApi.toggleSopChecklistItem as unknown as ReturnType<typeof vi.fn>;

const opened = {
  template_id: 'SOP-1', title: 'Closing', date: '2026-11-14', status: 'IN_PROGRESS', revision: 'rev-old',
  progress: { done: 0, total: 2, percent: 0 },
  items: [
    { step_id: 'id-count', step_number: 1, instruction: 'Count display pieces', completed: false },
    { step_id: 'id-safe', step_number: 2, instruction: 'Verify safe is locked', completed: false },
  ],
};
const reordered = {
  ...opened, revision: 'rev-new',
  items: [
    { step_id: 'id-safe', step_number: 1, instruction: 'Verify safe is locked', completed: false },
    { step_id: 'id-count', step_number: 2, instruction: 'Count display pieces', completed: false },
  ],
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TasksChecklistPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  document.body.innerHTML = '';
  vi.clearAllMocks();
});

describe('daily checklist tick', () => {
  it('names the step and the revision the page was read with', async () => {
    getSopChecklist.mockResolvedValue(opened);
    toggleItem.mockResolvedValue({ ...opened, items: [{ ...opened.items[0], completed: true }, opened.items[1]] });
    renderPage();
    await userEvent.click(await screen.findByRole('checkbox', { name: /Count display pieces/ }));
    expect(toggleItem).toHaveBeenCalledWith(
      expect.objectContaining({ template_id: 'SOP-1', step_number: 1, step_id: 'id-count', revision: 'rev-old', completed: true }),
    );
  });

  it('on a 409 shows the server sentence and reloads the changed checklist', async () => {
    getSopChecklist.mockResolvedValueOnce(opened).mockResolvedValue(reordered);
    const detail = 'This checklist was changed by an editor; refresh to continue';
    toggleItem.mockRejectedValue({ isAxiosError: true, response: { status: 409, data: { detail } } });
    renderPage();
    await userEvent.click(await screen.findByRole('checkbox', { name: /Count display pieces/ }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(detail));
    await waitFor(() => expect(getSopChecklist).toHaveBeenCalledTimes(2));
    const boxes = await screen.findAllByRole('checkbox');
    await waitFor(() => expect(boxes[0].closest('label')).toHaveTextContent('Verify safe is locked'));
  });
});
