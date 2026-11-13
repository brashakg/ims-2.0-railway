// L1-F36: a blank Discount % must stay in the form with an error, not create
// an active 0% rule. A deliberate 0 / 12.5 / 100 still saves.
// L1-F37 (UI half): clearing Brand / Sub-brand on edit sends an explicit null
// so the server can tell "cleared" from "not sent".
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { toast, api } = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  api: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() },
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../services/api/onlineDiscountRules', () => ({ onlineDiscountRulesApi: api }));

import DiscountRulesPage from '../DiscountRulesPage';

const RULE = {
  rule_id: 'rule_1', category: 'FRAME', brand: 'audit brand', sub_brand: 'audit sub',
  discount_percentage: 10, priority: 0, active: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.list.mockResolvedValue({ rules: [RULE] });
  const ok = { rule: {}, recompute: { ok: true, products: 1, changed: 1 } };
  api.create.mockResolvedValue(ok);
  api.update.mockResolvedValue(ok);
});

const pctInput = () => screen.getByPlaceholderText('e.g. 20');

async function openCreate() {
  render(<DiscountRulesPage />);
  await screen.findByText('audit brand');
  fireEvent.click(screen.getByRole('button', { name: /Add rule/i }));
}

describe('blank discount percentage (L1-F36)', () => {
  it.each(['', '   '])('refuses %j and keeps the modal open', async (blank) => {
    await openCreate();
    fireEvent.change(pctInput(), { target: { value: blank } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));
    await new Promise((r) => setTimeout(r, 0));
    expect(api.create).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith('Enter a discount % (0 to 100)');
    expect(screen.getByRole('button', { name: 'Create rule' })).toBeInTheDocument();
  });

  it.each([['0', 0], ['12.5', 12.5], ['100', 100]])('saves a deliberate %j', async (raw, want) => {
    await openCreate();
    fireEvent.change(pctInput(), { target: { value: raw } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    expect((api.create.mock.calls[0] as unknown[])[0]).toMatchObject({ discount_percentage: want });
  });
});

describe('clearing brand / sub-brand (L1-F37 UI half)', () => {
  it('sends explicit nulls for the cleared scope', async () => {
    render(<DiscountRulesPage />);
    await screen.findByText('audit brand');
    fireEvent.click(screen.getByTitle('Edit rule'));
    fireEvent.change(screen.getByPlaceholderText('any brand'), { target: { value: '' } });
    fireEvent.change(screen.getByPlaceholderText('any sub-brand'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(api.update).toHaveBeenCalledTimes(1));
    expect((api.update.mock.calls[0] as unknown[])[1]).toMatchObject({ brand: null, sub_brand: null });
  });
});
