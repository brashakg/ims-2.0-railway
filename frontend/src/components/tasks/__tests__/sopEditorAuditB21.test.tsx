// Audit b21 - the SOP editor's save contract and archive door.
//   R12: clearing Description sends description: '' (it used to send
//        undefined, which the server reads as "unchanged" - the old text came back).
//   R28: a reorder sends each step back WITH its stable step_id, so the server
//        keeps each tick on its own instruction.
//   R42: Archive is offered only to ADMIN / SUPERADMIN (the only roles the
//        server lets archive); a Store Manager is told who can, and never gets
//        a button that can only fail.
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const roles = vi.hoisted(() => ({ current: ['STORE_MANAGER'] as string[] }));

vi.mock('../../../services/api', () => ({
  tasksApi: {
    updateSopTemplate: vi.fn(async () => ({})),
    createSopTemplate: vi.fn(async () => ({})),
    deleteSopTemplate: vi.fn(async () => ({})),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'U1', activeStoreId: 'AUDTA', roles: roles.current } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../pages/tasks/tasksQueries', () => ({
  useStoreStaff: () => ({ data: [], isLoading: false }),
}));

import { tasksApi } from '../../../services/api';
import { SopEditorModal, type SopTemplateForm } from '../SopEditorModal';

const updateSop = tasksApi.updateSopTemplate as unknown as ReturnType<typeof vi.fn>;

const initial: SopTemplateForm = {
  template_id: 'SOP-1',
  title: 'Closing',
  description: 'Old description',
  category: 'Operations',
  frequency: 'DAILY',
  estimated_time: 15,
  steps: [
    { step_id: 'id-count', step_number: 1, instruction: 'Count display pieces' },
    { step_id: 'id-safe', step_number: 2, instruction: 'Verify safe is locked' },
  ],
  assigned_roles: [],
  assigned_users: [],
  store_id: 'AUDTA',
};

function open() {
  render(<SopEditorModal isOpen onClose={() => {}} initial={initial} onSaved={() => {}} />);
}

describe('SOP editor (audit b21)', () => {
  beforeEach(() => {
    updateSop.mockClear();
    roles.current = ['STORE_MANAGER'];
  });
  afterEach(() => cleanup());

  it('R12: a cleared description is sent as an empty string, not left out', async () => {
    open();
    fireEvent.change(screen.getByPlaceholderText(/One-line summary/i), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Save changes/i }));
    await waitFor(() => expect(updateSop).toHaveBeenCalledTimes(1));
    const payload = updateSop.mock.calls[0][1];
    expect('description' in payload).toBe(true);
    expect(payload.description).toBe('');
  });

  it('R28: a reorder sends every step back with its own step_id', async () => {
    open();
    fireEvent.click(screen.getAllByRole('button', { name: 'Move down' })[0]);
    fireEvent.click(screen.getByRole('button', { name: /Save changes/i }));
    await waitFor(() => expect(updateSop).toHaveBeenCalledTimes(1));
    expect(updateSop.mock.calls[0][1].steps).toEqual([
      { step_id: 'id-safe', step_number: 1, instruction: 'Verify safe is locked', warning: undefined },
      { step_id: 'id-count', step_number: 2, instruction: 'Count display pieces', warning: undefined },
    ]);
  });

  it('R42: a Store Manager gets no Archive button, and is told who can archive', () => {
    open();
    expect(screen.queryByRole('button', { name: /Archive/i })).toBeNull();
    expect(screen.getByText(/Only an Admin can archive/i)).toBeTruthy();
  });

  it('R42: an Admin still gets the Archive action', () => {
    roles.current = ['ADMIN'];
    open();
    expect(screen.getByRole('button', { name: /Archive/i })).toBeTruthy();
  });
});
