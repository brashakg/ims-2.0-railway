// ============================================================================
// HSN & GST rates: an emptied description is CLEARED (audit L1-F13)
// ============================================================================
// The audit cleared a row's Description and saved: the update left the field
// out, the server kept the old text, and the screen said Updated. An emptied
// box is now sent as '' (the server stores ''), for the category hint too.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { unlabelledControls } from '../../../test/unlabelledControls';

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../constants/gstRuntime', () => ({
  categoryHintOptions: () => ['FRAME', 'SERVICE'],
  loadHsnRates: () => Promise.resolve(),
}));

const list = vi.fn();
const update = vi.fn();
vi.mock('../../../services/api/hsn', () => ({
  hsnApi: {
    list: () => list(),
    update: (...a: unknown[]) => update(...a),
    create: vi.fn(),
    remove: vi.fn(),
  },
}));

import { HsnRatesSection } from '../HsnRatesSection';

const ROW = { hsn_id: 'h1', hsn_code: '991199', description: 'AUDIT L1 TAX', category_hint: 'SERVICE', gst_rate: 18 };

beforeEach(() => {
  vi.clearAllMocks();
  list.mockResolvedValue({ hsn_rates: [{ ...ROW }], total: 1 });
  update.mockResolvedValue({});
});

describe('HSN rate edit (L1-F13)', () => {
  it('sends an emptied description and category as empty text, not as "unchanged"', async () => {
    render(<HsnRatesSection />);
    await screen.findByText('AUDIT L1 TAX');
    fireEvent.click(screen.getByTitle('Edit'));

    fireEvent.change(screen.getByLabelText('Description for HSN 991199'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Category for HSN 991199'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('GST rate for HSN 991199'), { target: { value: '12' } });
    expect(unlabelledControls()).toEqual([]);
    fireEvent.click(screen.getByTitle('Save'));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith('h1', { gst_rate: 12, description: '', category_hint: '' });
    expect(toast.success).toHaveBeenCalledWith('Updated 991199 -> 12%');
  });

  it('keeps an untouched description and category in the update', async () => {
    render(<HsnRatesSection />);
    await screen.findByText('AUDIT L1 TAX');
    fireEvent.click(screen.getByTitle('Edit'));
    fireEvent.change(screen.getByLabelText('GST rate for HSN 991199'), { target: { value: '5' } });
    fireEvent.click(screen.getByTitle('Save'));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith('h1', { gst_rate: 5, description: 'AUDIT L1 TAX', category_hint: 'SERVICE' });
  });
});
