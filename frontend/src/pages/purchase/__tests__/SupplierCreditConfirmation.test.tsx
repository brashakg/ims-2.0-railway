import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { SupplierCreditPreview, SupplierCreditResult } from '../../../services/api/supplierCredits';

const calls = vi.hoisted(() => ({ preview: vi.fn(), confirm: vi.fn() }));
vi.mock('../../../services/api/supplierCredits', () => ({ supplierCreditsApi: calls }));
import { SupplierCreditConfirmation } from '../SupplierCreditConfirmation';

const original = { return_line_index: 0, grn_id: 'G1', bill_id: 'B1', bill_line_index: 0, quantity: 2, stock_ids: ['U1', 'U2'] };
const heads = { taxable: 100, cgst: 2.5, sgst: 2.5, igst: 0, total: 105 };
const resolved = (): SupplierCreditPreview => ({
  return_id: 'R1', source_status: 'RESOLVED', pending_reasons: [],
  sources: [{ source_key: 'source1', ...original, product_id: 'P1', product_name: 'Frame',
    bill_number: 'INV-OLD', bill_date: '2026-09-01', claims_credit: true, reverse_charge: false,
    suggested: heads, remaining_caps: heads }],
  choices: [], source_selections: [original], expected_credit_amount: 105, posted_amount: 0,
  remaining_amount: 105, confirmation: null, confirmations: [],
});
const posted: SupplierCreditResult = {
  idempotent: false,
  confirmation: { confirmation_id: 'C1', credit_note_number: 'SUP-CN-1', credit_note_date: '2026-10-18',
    received_amount: 105, posting_status: 'POSTED', pending_reasons: [] },
  return: { return_id: 'R1', status: 'credit_issued' },
  summary: { posted_amount: 105, remaining_amount: 0, settlement_status: 'CONFIRMED' },
};
const enterNote = async () => {
  await screen.findByLabelText('Supplier note number');
  fireEvent.change(screen.getByLabelText('Supplier note number'), { target: { value: 'SUP-CN-1' } });
  fireEvent.change(screen.getByLabelText('Supplier note date'), { target: { value: '2026-10-18' } });
};
const setup = () => {
  const saved = vi.fn();
  const view = render(<SupplierCreditConfirmation returnId="R1" canRecord onClose={vi.fn()} onSaved={saved} />);
  return { saved, ...view };
};

beforeEach(() => {
  vi.clearAllMocks();
  calls.preview.mockResolvedValue(resolved());
  calls.confirm.mockResolvedValue(posted);
});

describe('supplier-confirmed credit, separate from internal return notes', () => {
  it('never fetches or displays supplier money for a role without permission', async () => {
    render(<SupplierCreditConfirmation returnId="R1" canRecord={false} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect(calls.preview).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('fills the original bill and confirms the actual supplier number/date with a stable request id', async () => {
    const { saved } = setup();
    await enterNote();
    expect(screen.getByText(/Original bill: INV-OLD/)).toBeInTheDocument();
    expect(calls.confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(posted));
    expect(calls.confirm).toHaveBeenCalledWith('R1', {
      idempotency_key: expect.stringMatching(/^[a-f0-9-]{36}$/i), credit_note_number: 'SUP-CN-1',
      credit_note_date: '2026-10-18', received_amount: 105, notes: '', source_selections: [original],
      allocations: [{ source_key: 'source1', taxable: 100, cgst: 2.5, sgst: 2.5, igst: 0 }],
    });
  });

  it('sends only a partial confirmed amount and preserves the partial response', async () => {
    const partial = { ...posted, summary: { posted_amount: 52.5, remaining_amount: 52.5, settlement_status: 'PARTIAL' } };
    calls.confirm.mockResolvedValue(partial);
    const { saved } = setup();
    await enterNote();
    fireEvent.change(screen.getByLabelText('Actual credited amount (₹)'), { target: { value: '52.50' } });
    for (const [label, value] of [['Goods value', '50'], ['CGST', '1.25'], ['SGST', '1.25']]) {
      fireEvent.change(screen.getByLabelText(`${label} for Frame (INV-OLD)`), { target: { value } });
    }
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(partial));
    expect(calls.confirm.mock.calls[0][1].received_amount).toBe(52.5);
    expect(calls.confirm.mock.calls[0][1].allocations[0].taxable).toBe(50);
  });

  it('rejects amounts beyond the original cap and a mismatch with the actual note', async () => {
    setup();
    await enterNote();
    const goods = screen.getByLabelText('Goods value for Frame (INV-OLD)');
    fireEvent.change(goods, { target: { value: '101' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('within the remaining original-bill amount');
    fireEvent.change(goods, { target: { value: '50' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    expect(screen.getByRole('alert')).toHaveTextContent('must add up');
    expect(calls.confirm).not.toHaveBeenCalled();
  });

  it('keeps an untraceable note pending without fabricating tax or allocations', async () => {
    calls.preview.mockResolvedValue({ ...resolved(), source_status: 'PENDING_EVIDENCE', sources: [],
      source_selections: [], expected_credit_amount: null, remaining_amount: null,
      pending_reasons: [{ code: 'UNBOOKED_ORIGIN', message: 'The original bill is not booked.' }] });
    setup();
    await enterNote();
    fireEvent.change(screen.getByLabelText('Actual credited amount (₹)'), { target: { value: '80' } });
    expect(screen.getByText(/Supplier dues and GST will stay unchanged/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save supplier note as pending' }));
    await waitFor(() => expect(calls.confirm).toHaveBeenCalled());
    expect(calls.confirm.mock.calls[0][1]).toMatchObject({ received_amount: 80, allocations: [], source_selections: [] });
  });

  it('retries a lost response with the same UUID and payload, locking edits in between', async () => {
    calls.confirm.mockRejectedValueOnce({ response: { status: 503 } }).mockResolvedValueOnce(posted);
    const { saved } = setup();
    await enterNote();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    const retry = await screen.findByRole('button', { name: 'Retry same note' });
    expect(screen.getByLabelText('Supplier note number')).toBeDisabled();
    expect(screen.getByLabelText('Actual credited amount (₹)')).toBeDisabled();
    fireEvent.click(retry);
    await waitFor(() => expect(saved).toHaveBeenCalledWith(posted));
    expect(calls.confirm.mock.calls[1]).toEqual(calls.confirm.mock.calls[0]);
  });

  it('does not submit two copies while confirmation is in flight', async () => {
    let resolve!: (result: SupplierCreditResult) => void;
    calls.confirm.mockReturnValue(new Promise<SupplierCreditResult>(done => { resolve = done; }));
    const { saved } = setup();
    await enterNote();
    const button = screen.getByRole('button', { name: 'Confirm supplier credit' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(calls.confirm).toHaveBeenCalledTimes(1);
    resolve(posted);
    await waitFor(() => expect(saved).toHaveBeenCalled());
  });

  it('resumes an existing pending confirmation with the same note identity', async () => {
    const existing = { ...posted.confirmation, confirmation_id: 'original-confirmation-id', posting_status: 'PENDING_EVIDENCE' as const };
    calls.preview.mockResolvedValueOnce({ ...resolved(), confirmations: [existing] })
      .mockResolvedValueOnce({ ...resolved(), confirmations: [existing], confirmation: existing });
    setup();
    await screen.findByLabelText('Supplier note to complete');
    fireEvent.change(screen.getByLabelText('Supplier note to complete'), { target: { value: existing.confirmation_id } });
    await waitFor(() => expect(screen.getByLabelText('Supplier note number')).toHaveValue('SUP-CN-1'));
    expect(calls.preview).toHaveBeenLastCalledWith('R1', { confirmation_id: existing.confirmation_id });
    expect(screen.getByLabelText('Supplier note number')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(calls.confirm).toHaveBeenCalled());
    expect(calls.confirm.mock.calls[0][1].idempotency_key).toBe(existing.confirmation_id);
  });

  it('requires rechecking the bill after a source conflict', async () => {
    calls.confirm.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'Original bill changed.' } } });
    setup();
    await enterNote();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    const refresh = await screen.findByRole('button', { name: 'Refresh original bills' });
    expect(screen.getByRole('button', { name: 'Confirm supplier credit' })).toBeDisabled();
    fireEvent.click(refresh);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Confirm supplier credit' })).toBeEnabled());
    expect(calls.confirm).toHaveBeenCalledTimes(1);
  });

  it.each([503, 401, 403])('keeps the exact reopened request through a %s reply instead of normalized preview selections', async status => {
    const retryPayload = { idempotency_key: '81311dbd-c9ad-4eec-89fd-e52b590be44a',
      credit_note_number: 'INTERRUPTED-CN', credit_note_date: '2026-10-16', received_amount: 52.5,
      notes: 'Actual supplier note', source_selections: [],
      allocations: [{ source_key: 'source1', taxable: 50, cgst: 1.25, sgst: 1.25, igst: 0 }] };
    const existing = { ...retryPayload, confirmation_id: retryPayload.idempotency_key,
      posting_status: 'PENDING_EVIDENCE' as const, pending_reasons: [],
      retry_unchanged: true, retry_payload: retryPayload };
    calls.preview.mockResolvedValueOnce({ ...resolved(), confirmations: [existing] })
      .mockResolvedValueOnce({ ...resolved(), confirmations: [existing], confirmation: existing });
    calls.confirm.mockRejectedValueOnce({ response: { status } }).mockResolvedValueOnce(posted);
    const { saved } = setup();
    await screen.findByLabelText('Supplier note to complete');
    fireEvent.change(screen.getByLabelText('Supplier note to complete'), { target: { value: existing.confirmation_id } });
    await screen.findByRole('button', { name: 'Retry same note' });
    for (const label of ['Supplier note number', 'Supplier note date', 'Actual credited amount (₹)',
      'Notes (optional)', 'Supplier note to complete', 'Goods value for Frame (INV-OLD)']) {
      expect(screen.getByLabelText(label)).toBeDisabled();
    }
    fireEvent.click(screen.getByRole('button', { name: 'Retry same note' }));
    await screen.findByRole('alert');
    expect(calls.confirm).toHaveBeenLastCalledWith('R1', retryPayload);
    expect(screen.getByLabelText('Notes (optional)')).toBeDisabled();
    expect(screen.getByLabelText('Goods value for Frame (INV-OLD)')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry same note' }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(posted));
    expect(calls.confirm.mock.calls[1]).toEqual(calls.confirm.mock.calls[0]);
  });

  it('unlocks pending evidence only after a fresh server preview confirms the interrupted request is no longer posting', async () => {
    const retryPayload = { idempotency_key: '81311dbd-c9ad-4eec-89fd-e52b590be44a',
      credit_note_number: 'INTERRUPTED-CN', credit_note_date: '2026-10-16', received_amount: 105,
      notes: '', source_selections: [], allocations: [{ source_key: 'source1', taxable: 100, cgst: 2.5, sgst: 2.5, igst: 0 }] };
    const existing = { ...retryPayload, confirmation_id: retryPayload.idempotency_key,
      posting_status: 'PENDING_EVIDENCE' as const, pending_reasons: [], retry_unchanged: true, retry_payload: retryPayload };
    const pending = { ...existing, retry_unchanged: false, retry_payload: null };
    calls.preview.mockResolvedValueOnce({ ...resolved(), confirmation: existing, confirmations: [existing] })
      .mockResolvedValueOnce({ ...resolved(), confirmation: pending, confirmations: [pending] });
    calls.confirm.mockRejectedValueOnce({ response: { status: 409 } }).mockResolvedValueOnce(posted);
    setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry same note' }));
    const refresh = await screen.findByRole('button', { name: 'Refresh original bills' });
    expect(screen.getByLabelText('Notes (optional)')).toBeDisabled();
    fireEvent.click(refresh);
    await waitFor(() => expect(screen.getByLabelText('Notes (optional)')).toBeEnabled());
    expect(calls.preview).toHaveBeenLastCalledWith('R1', { confirmation_id: existing.confirmation_id });
    expect(screen.getByLabelText('Supplier note number')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(calls.confirm).toHaveBeenCalledTimes(2));
    expect(calls.confirm.mock.calls[1][1]).toMatchObject({ idempotency_key: existing.confirmation_id, source_selections: [original] });
  });

  it('does not invent a retry when an interrupted note is missing its original request', async () => {
    const existing = { ...posted.confirmation, posting_status: 'PENDING_EVIDENCE' as const,
      retry_unchanged: true, retry_payload: null };
    calls.preview.mockResolvedValue({ ...resolved(), confirmations: [existing], confirmation: existing });
    setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry same note' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('original request could not be recovered');
    expect(calls.confirm).not.toHaveBeenCalled();
  });

  it('clears a locally uncertain retry when the same pending note is freshly verified as editable', async () => {
    const existing = { ...posted.confirmation, posting_status: 'PENDING_EVIDENCE' as const,
      retry_unchanged: false, retry_payload: null };
    calls.preview.mockResolvedValue({ ...resolved(), confirmation: existing, confirmations: [existing] });
    calls.confirm.mockRejectedValueOnce({ response: { status: 503 } })
      .mockRejectedValueOnce({ response: { status: 409 } }).mockResolvedValueOnce(posted);
    setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm supplier credit' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Retry same note' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh original bills' }));
    await waitFor(() => expect(screen.getByLabelText('Notes (optional)')).toBeEnabled());
    expect(screen.getByLabelText('Supplier note number')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Notes (optional)'), { target: { value: 'Evidence rechecked' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(calls.confirm).toHaveBeenCalledTimes(3));
    expect(calls.confirm.mock.calls[2][1]).toMatchObject({ idempotency_key: existing.confirmation_id, notes: 'Evidence rechecked' });
  });

  it('restores the saved note identity after its first lookup fails and the bill refresh succeeds', async () => {
    const existing = { ...posted.confirmation, confirmation_id: 'saved-note-id',
      credit_note_number: 'SAVED-CN', credit_note_date: '2026-10-16', received_amount: 52.5,
      posting_status: 'PENDING_EVIDENCE' as const,
      allocations: [{ source_key: 'source1', taxable: 50, cgst: 1.25, sgst: 1.25, igst: 0 }] };
    calls.preview.mockResolvedValueOnce({ ...resolved(), confirmations: [existing] })
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce({ ...resolved(), confirmations: [existing], confirmation: existing });
    setup();
    await enterNote();
    fireEvent.change(screen.getByLabelText('Supplier note to complete'), { target: { value: existing.confirmation_id } });
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh original bills' }));
    await waitFor(() => expect(screen.getByLabelText('Supplier note number')).toHaveValue('SAVED-CN'));
    expect(screen.getByLabelText('Supplier note date')).toHaveValue('2026-10-16');
    expect(screen.getByLabelText('Actual credited amount (₹)')).toHaveValue('52.5');
    expect(screen.getByLabelText('Supplier note number')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(calls.confirm).toHaveBeenCalled());
    expect(calls.confirm.mock.calls[0][1]).toMatchObject({ idempotency_key: existing.confirmation_id,
      credit_note_number: 'SAVED-CN', credit_note_date: '2026-10-16', received_amount: 52.5,
      allocations: existing.allocations });
  });

  it('discards the saved note identity when switching back to a new note needs a refresh', async () => {
    const existing = { ...posted.confirmation, confirmation_id: 'saved-note-id', posting_status: 'PENDING_EVIDENCE' as const };
    calls.preview.mockResolvedValueOnce({ ...resolved(), confirmations: [existing] })
      .mockResolvedValueOnce({ ...resolved(), confirmations: [existing], confirmation: existing })
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce({ ...resolved(), confirmations: [existing] });
    setup();
    await screen.findByLabelText('Supplier note to complete');
    fireEvent.change(screen.getByLabelText('Supplier note to complete'), { target: { value: existing.confirmation_id } });
    await waitFor(() => expect(screen.getByLabelText('Supplier note number')).toBeDisabled());
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Supplier note to complete'), { target: { value: '' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh original bills' }));
    await waitFor(() => expect(screen.getByLabelText('Supplier note number')).toBeEnabled());
    expect(screen.getByLabelText('Supplier note number')).toHaveValue('');
    expect(screen.getByLabelText('Supplier note date')).toHaveValue('');
    await enterNote();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(calls.confirm).toHaveBeenCalled());
    expect(calls.confirm.mock.calls[0][1].idempotency_key).not.toBe(existing.confirmation_id);
    expect(calls.confirm.mock.calls[0][1].idempotency_key).toMatch(/^[a-f0-9-]{36}$/i);
  });

  it('uses the checked bill and physical units only after the server verifies them', async () => {
    const ambiguous: SupplierCreditPreview = { ...resolved(), source_status: 'PENDING_EVIDENCE', sources: [],
      source_selections: [], pending_reasons: [{ code: 'AMBIGUOUS', message: 'Select the original units.' }],
      choices: [{ return_line_index: 0, product_name: 'Frame', quantity: 2, candidates: [{ ...original,
        bill_number: 'INV-OLD', bill_date: '2026-09-01',
        stock_units: [{ stock_id: 'U1', label: 'FRAME-001' }, { stock_id: 'U2', label: 'FRAME-002' }] }] }] };
    calls.preview.mockResolvedValueOnce(ambiguous).mockResolvedValueOnce(resolved());
    setup();
    await enterNote();
    fireEvent.click(screen.getByRole('checkbox', { name: /INV-OLD/ }));
    expect(screen.getByRole('checkbox', { name: 'FRAME-001' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'FRAME-002' })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Check selected bills' }));
    await screen.findByRole('button', { name: 'Confirm supplier credit' });
    expect(calls.preview).toHaveBeenLastCalledWith('R1', { source_selections: [original] });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(calls.confirm).toHaveBeenCalled());
    expect(calls.confirm.mock.calls[0][1].source_selections).toEqual([original]);
  });

  it('does not save against the old preview when re-verification fails', async () => {
    calls.confirm.mockRejectedValueOnce({ response: { status: 409 } });
    calls.preview.mockResolvedValueOnce(resolved()).mockRejectedValueOnce({ response: { status: 503 } });
    setup();
    await enterNote();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh original bills' }));
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Confirm supplier credit' })).toBeDisabled();
    expect(calls.confirm).toHaveBeenCalledTimes(1);
  });

  it('keeps reverse-charge tax outside the amount actually credited by the supplier', async () => {
    const preview = resolved();
    preview.sources[0].reverse_charge = true;
    preview.sources[0].suggested = { ...heads, total: 100 };
    preview.sources[0].remaining_caps = { ...heads, total: 100 };
    preview.expected_credit_amount = preview.remaining_amount = 100;
    calls.preview.mockResolvedValue(preview);
    setup();
    await enterNote();
    expect(screen.getByLabelText('Actual credited amount (₹)')).toHaveValue('100');
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(calls.confirm).toHaveBeenCalled());
    expect(calls.confirm.mock.calls[0][1]).toMatchObject({ received_amount: 100,
      allocations: [{ source_key: 'source1', taxable: 100, cgst: 2.5, sgst: 2.5, igst: 0 }] });
  });

  it('explains unclaimed original tax without changing its commercial credit amount', async () => {
    const preview = resolved();
    preview.sources[0].claims_credit = false;
    calls.preview.mockResolvedValue(preview);
    setup();
    await enterNote();
    expect(screen.getByText(/will not reverse it/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    await waitFor(() => expect(calls.confirm).toHaveBeenCalled());
    expect(calls.confirm.mock.calls[0][1].received_amount).toBe(105);
  });

  it('cannot reuse a previous return preview after the next return fails to load', async () => {
    const { rerender } = setup();
    await enterNote();
    calls.preview.mockRejectedValueOnce({ response: { status: 503 } });
    rerender(<SupplierCreditConfirmation returnId="R2" canRecord onClose={vi.fn()} onSaved={vi.fn()} />);
    await screen.findByRole('button', { name: 'Retry bill check' });
    expect(screen.queryByLabelText('Supplier note number')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirm supplier credit' })).not.toBeInTheDocument();
    expect(calls.confirm).not.toHaveBeenCalled();
  });

  it('ignores an old confirmation response after the money form loses permission', async () => {
    let complete!: (result: SupplierCreditResult) => void;
    calls.confirm.mockReturnValue(new Promise<SupplierCreditResult>(done => { complete = done; }));
    const { rerender, saved } = setup();
    await enterNote();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm supplier credit' }));
    rerender(<SupplierCreditConfirmation returnId="R1" canRecord={false} onClose={vi.fn()} onSaved={saved} />);
    complete(posted);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(saved).not.toHaveBeenCalled();
  });
});
