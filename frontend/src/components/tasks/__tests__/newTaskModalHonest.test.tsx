// ============================================================================
// New task modal: audit 2026-11-04 R03, R04, R29, R44
// ============================================================================
// R03  Watchers and an auto-escalation on/off switch were offered but never
//      saved. Neither is offered any more; escalation is stated, always on.
// R04  "Tomorrow 10am" was a flat +20h (10:47 -> 06:47 next day). It is now
//      10:00 India time on the next India calendar day.
// R29  Tab walked out of the open dialog into the covered page.
// R44  Ctrl+Enter during an in-flight create posted a second task.

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  tasksApi: { createTask: vi.fn(), uploadTaskFile: vi.fn() },
  adminStoreApi: {
    getStoreUsers: vi.fn(async () => ({
      users: [
        { user_id: 'u-me', name: 'Meera Manager', roles: ['STORE_MANAGER'] },
        { user_id: 'u-cash', name: 'Cashier One', roles: ['CASHIER'] },
      ],
    })),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-me', name: 'Meera Manager', activeStoreId: 'S1', activeRole: 'STORE_MANAGER' },
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { tasksApi } from '../../../services/api';
import { NewTaskModal, tomorrowAt10Ist } from '../NewTaskModal';

const createTask = tasksApi.createTask as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  createTask.mockReset();
});
afterEach(() => vi.useRealTimers());

async function openModal() {
  const utils = render(<NewTaskModal isOpen onClose={vi.fn()} onCreated={vi.fn()} />);
  await screen.findAllByText('Cashier One');
  return utils;
}

const typeTitle = () =>
  fireEvent.change(screen.getByPlaceholderText(/Reconcile/), {
    target: { value: 'Count display pieces' },
  });

describe('R04 Tomorrow 10am is 10:00 India time on the next India day', () => {
  it.each([
    // [now (UTC), expected (UTC)]
    ['2026-11-04T05:17:45Z', '2026-11-05T04:30:00.000Z'], // 10:47 IST (the audit case)
    ['2026-11-04T18:29:00Z', '2026-11-05T04:30:00.000Z'], // 23:59 IST
    ['2026-11-04T18:31:00Z', '2026-11-06T04:30:00.000Z'], // 00:01 IST on 5 Nov
    ['2026-11-04T02:00:00Z', '2026-11-05T04:30:00.000Z'], // 07:30 IST
    ['2026-12-31T12:00:00Z', '2027-01-01T04:30:00.000Z'], // year end
  ])('at %s', (now, expected) => {
    expect(tomorrowAt10Ist(new Date(now)).toISOString()).toBe(expected);
  });

  it('the preset sends that time, not now + 20h', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-04T05:17:45Z'));
    createTask.mockResolvedValue({ task_id: 'TASK-1' });
    await openModal();
    typeTitle();
    fireEvent.click(screen.getByRole('button', { name: /Tomorrow 10am/ }));
    fireEvent.click(screen.getByRole('button', { name: /Create task/ }));
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1));
    expect(new Date(createTask.mock.calls[0][0].due_date).toISOString()).toBe(
      '2026-11-05T04:30:00.000Z',
    );
  });
});

describe('R03 no control the create does not save', () => {
  it('offers no Watchers picker and no escalation switch', async () => {
    await openModal();
    expect(screen.queryByText('Watchers')).toBeNull();
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.getByText(/Always on: an overdue task escalates/)).toBeInTheDocument();
  });
});

describe('R44 one create in flight', () => {
  it('a second Ctrl+Enter while the first create is pending sends nothing', async () => {
    let release: (v: unknown) => void = () => {};
    createTask.mockImplementation(
      () =>
        new Promise((r) => {
          release = r;
        }),
    );
    await openModal();
    typeTitle();
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true });
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true });
    fireEvent.keyDown(window, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1));
    await act(async () => release({ task_id: 'TASK-1' }));
    expect(createTask).toHaveBeenCalledTimes(1);
    expect(createTask.mock.calls[0][0].client_request_id).toBeTruthy();
  });

  it('a retry after a failed reply reuses the same request key', async () => {
    createTask
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({ task_id: 'TASK-1' });
    await openModal();
    typeTitle();
    fireEvent.click(screen.getByRole('button', { name: /Create task/ }));
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Create task/ })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: /Create task/ }));
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(2));
    expect(createTask.mock.calls[0][0].client_request_id).toBeTruthy();
    expect(createTask.mock.calls[1][0].client_request_id).toBe(
      createTask.mock.calls[0][0].client_request_id,
    );
  });
});

describe('R29 focus stays in the dialog', () => {
  it('Tab wraps inside the dialog both ways and focus returns to the opener on close', async () => {
    const opener = document.createElement('button');
    opener.textContent = 'Open new task';
    document.body.appendChild(opener);
    opener.focus();
    const { rerender } = await openModal();
    const dialog = screen.getByRole('dialog');
    // Opening puts focus on the title field, inside the dialog.
    expect(document.activeElement).toBe(screen.getByPlaceholderText(/Reconcile/));
    const close = screen.getByRole('button', { name: 'Close' });
    // Create is disabled (no title yet), so Cancel is the last focusable.
    const cancel = screen.getByRole('button', { name: 'Cancel' });

    cancel.focus();
    fireEvent.keyDown(document.activeElement as Element, { key: 'Tab' });
    expect(document.activeElement).toBe(close);

    fireEvent.keyDown(document.activeElement as Element, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(cancel);

    opener.focus(); // focus that escaped is pulled back in
    fireEvent.keyDown(opener, { key: 'Tab' });
    expect(dialog.contains(document.activeElement)).toBe(true);

    rerender(<NewTaskModal isOpen={false} onClose={vi.fn()} onCreated={vi.fn()} />);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});
