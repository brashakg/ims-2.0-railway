// FE-7: a new journal entry and a new expense default their date to the IST
// day. At 01:00 IST on 1 April the UTC day is 31 March: the closed year.
import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.stubGlobal('requestIdleCallback', () => 0);

const fin = vi.hoisted(() => ({ listJournalEntries: vi.fn(), getChartOfAccounts: vi.fn() }));
const exp = vi.hoisted(() => ({
  getExpenses: vi.fn(), getPendingApproval: vi.fn(), getToEnter: vi.fn(), getAging: vi.fn(),
  getDuplicateBills: vi.fn(), createExpense: vi.fn(),
}));
vi.mock('../../../services/api/finance', () => ({ financeApi: fin }));
vi.mock('../../../services/api/expenses', () => ({ expensesApi: exp }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: 'BV-DHN-01' } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

import JournalEntriesPanel from '../JournalEntriesPanel';
import { ExpensesLayout } from '../expenses/ExpensesLayout';

beforeEach(() => {
  fin.listJournalEntries.mockResolvedValue({ entries: [] });
  fin.getChartOfAccounts.mockResolvedValue({ accounts: [] });
  for (const f of Object.values(exp)) f.mockResolvedValue({ expenses: [] });
});
afterEach(() => vi.useRealTimers());

const CLOCKS = [
  ['2027-03-31T19:30:00Z', '2027-04-01'], // night: 01:00 IST on 1 Apr, new FY
  ['2027-03-31T09:00:00Z', '2027-03-31'], // day control
] as const;

function dateInput(container: HTMLElement) {
  return container.querySelector('input[type="date"]') as HTMLInputElement;
}

it.each(CLOCKS)('a new journal entry at %s is dated %s', async (clock, day) => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(clock));
  const { container } = render(<JournalEntriesPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /New Journal Entry/i }));
  expect(dateInput(container).value).toBe(day);
});

it.each(CLOCKS)('a new expense at %s is dated %s', async (clock, day) => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(clock));
  const { container } = render(<MemoryRouter><ExpensesLayout /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: /Add expense/i }));
  expect(dateInput(container).value).toBe(day);
});
