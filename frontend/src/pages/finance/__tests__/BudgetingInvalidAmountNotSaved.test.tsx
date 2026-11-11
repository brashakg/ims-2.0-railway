// ============================================================================
// IMS 2.0 - Budgeting never says "Budget saved" over a row it did not save
// ============================================================================
// Audit L3-09. A custom head with a negative amount (AUDIT_TEST = -100) was
// skipped inside the save loop and the toast still said "Budget saved": the
// user believed a head was planned that was never stored.
//
// The rule now: every entered amount is checked BEFORE anything is sent. If
// any is not a number of zero or more, NOTHING is saved and the message names
// the head. The positive control proves a clean table still saves every row.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/budgets', () => ({
  budgetsApi: { list: vi.fn(), variance: vi.fn(), upsert: vi.fn(), remove: vi.fn() },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u-acc', roles: ['ACCOUNTANT'], activeStoreId: 'ZZ-SOLO' },
  }),
}));
const toast = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

import BudgetingPage from '../BudgetingPage';
import { budgetsApi } from '../../../services/api/budgets';

const api = budgetsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const EMPTY_VARIANCE = { store_id: 'ZZ-SOLO', period: '2026-11', lines: [], totals: {} };

beforeEach(() => {
  vi.clearAllMocks();
  api.list.mockResolvedValue({ budgets: [], total: 0 });
  api.variance.mockResolvedValue(EMPTY_VARIANCE);
  api.upsert.mockResolvedValue({ budget: {}, persisted: true });
});

/** The amount box on a head's row. */
function amountBox(head: string): HTMLInputElement {
  const row = screen.getByText(head, { selector: 'td' }).closest('tr')!;
  return row.querySelector('input[type="number"]') as HTMLInputElement;
}

async function renderWithCustomHead(head: string) {
  render(<BudgetingPage />);
  const box = await screen.findByPlaceholderText('New head…');
  fireEvent.change(box, { target: { value: head } });
  fireEvent.click(screen.getByRole('button', { name: /add/i }));
  await waitFor(() => expect(screen.getByText(head, { selector: 'td' })).toBeInTheDocument());
}

describe('Budgeting save - an invalid amount', () => {
  it('the audit case: a negative custom head saves NOTHING and is named', async () => {
    await renderWithCustomHead('AUDIT_TEST');
    fireEvent.change(amountBox('AUDIT_TEST'), { target: { value: '-100' } });
    fireEvent.change(amountBox('rent'), { target: { value: '250' } });

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    expect(toast.error.mock.calls[0][0]).toContain('AUDIT_TEST');
    expect(toast.error.mock.calls[0][0]).toMatch(/not saved/i);
    // The lie this test exists for.
    expect(toast.success).not.toHaveBeenCalled();
    // Nothing went to the server -- not even the valid row -- so the screen
    // and the saved plan cannot disagree about what "saved" covered.
    expect(api.upsert).not.toHaveBeenCalled();
    // What the user typed is still on screen to correct.
    expect(amountBox('AUDIT_TEST').value).toBe('-100');
  });

  it('names every invalid head, not just the first', async () => {
    await renderWithCustomHead('AUDIT_TEST');
    fireEvent.change(amountBox('AUDIT_TEST'), { target: { value: '-100' } });
    fireEvent.change(amountBox('marketing'), { target: { value: '-5' } });

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    const said = toast.error.mock.calls[0][0] as string;
    expect(said).toContain('AUDIT_TEST');
    expect(said).toContain('marketing');
    expect(api.upsert).not.toHaveBeenCalled();
  });

  it('POSITIVE CONTROL: valid amounts (zero included) all save, then it says saved', async () => {
    await renderWithCustomHead('AUDIT_TEST');
    fireEvent.change(amountBox('AUDIT_TEST'), { target: { value: '250' } });
    fireEvent.change(amountBox('rent'), { target: { value: '0' } });

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Budget saved'));
    expect(toast.error).not.toHaveBeenCalled();
    const sent = api.upsert.mock.calls.map((c) => [c[0].head, c[0].planned_amount]);
    expect(sent).toEqual(
      expect.arrayContaining([['AUDIT_TEST', 250], ['rent', 0]]),
    );
    expect(sent).toHaveLength(2);
  });
});
