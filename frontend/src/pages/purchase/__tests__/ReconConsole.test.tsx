// ============================================================================
// IMS 2.0 - ReconConsole queue tests (procurement Phase 3 rework)
// ============================================================================
// Pins the council-ruled accountant queue:
//   - ONE filterable queue with chips (All / Needs review / On hold /
//     Unlinked / Auto-matched / Settled) + counts
//   - NO PO LINKED red badge only on invoices with no PO, no GRN and no DCs
//   - Unlinked chip filters to exactly those rows
//   - AUTO-MATCHED rows are batch-confirmable: "Confirm N selected" loops the
//     EXISTING per-invoice recon endpoint sequentially, continue-on-error,
//     per-row failure surfaced (never a bulk endpoint)
//   - attestation note saved via the same per-invoice POST ({ note })

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock('../../../services/api/purchaseRecon', () => ({
  purchaseReconApi: {
    getRecon: vi.fn(),
    upsertRecon: vi.fn(),
    getWorklists: vi.fn(),
    markSchemeCnReceived: vi.fn(),
  },
}));

vi.mock('../../../services/api/vendorAp', () => ({
  purchaseInvoicesApi: {
    list: vi.fn(),
    approveException: vi.fn(),
  },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { activeStoreId: 'store-1', roles: ['ACCOUNTANT'] },
    hasRole: () => true,
  }),
}));

import ReconConsole, {
  isUnlinked,
  isSettled,
  chipMatches,
  attentionRank,
  isBatchEligible,
} from '../ReconConsole';
import { purchaseReconApi, type ReconBlock } from '../../../services/api/purchaseRecon';
import { purchaseInvoicesApi, type PurchaseInvoice } from '../../../services/api/vendorAp';

const listMock = purchaseInvoicesApi.list as unknown as ReturnType<typeof vi.fn>;
const upsertMock = purchaseReconApi.upsertRecon as unknown as ReturnType<typeof vi.fn>;
const getReconMock = purchaseReconApi.getRecon as unknown as ReturnType<typeof vi.fn>;
const worklistsMock = purchaseReconApi.getWorklists as unknown as ReturnType<typeof vi.fn>;

const EMPTY_RECON: ReconBlock = {
  reconciled: false,
  entered_tally: false,
  filed_gst: false,
  payment_settled: false,
};

const SETTLED_RECON: ReconBlock = {
  reconciled: true,
  entered_tally: true,
  filed_gst: true,
  payment_settled: true,
};

function inv(partial: Partial<PurchaseInvoice> & { purchase_invoice_id: string }): PurchaseInvoice {
  return {
    vendor_id: 'v-1',
    vendor_name: 'Luxottica India',
    vendor_invoice_no: partial.purchase_invoice_id.toUpperCase(),
    vendor_invoice_date: '2026-07-01',
    lines: [],
    taxable_amount: 1000,
    cgst: 25,
    sgst: 25,
    igst: 0,
    tax_amount: 50,
    total_amount: 1050,
    status: 'OUTSTANDING',
    recon: EMPTY_RECON,
    ...partial,
  } as PurchaseInvoice;
}

// Fixtures: one of each queue species.
const UNLINKED = inv({
  purchase_invoice_id: 'pi-ul-1',
  vendor_invoice_no: 'INV-UL-1',
  vendor_invoice_date: '2026-07-02',
});
const LINKED = inv({
  purchase_invoice_id: 'pi-lk-1',
  vendor_invoice_no: 'INV-LK-1',
  po_id: 'po-1',
  po_number: 'PO-BV-26-0001',
  vendor_invoice_date: '2026-07-01',
});
const MATCHED_A = inv({
  purchase_invoice_id: 'pi-am-1',
  vendor_invoice_no: 'INV-AM-1',
  po_id: 'po-2',
  grn_id: 'grn-2',
  match_status: 'MATCHED',
  vendor_invoice_date: '2026-06-30',
});
const MATCHED_B = inv({
  purchase_invoice_id: 'pi-am-2',
  vendor_invoice_no: 'INV-AM-2',
  po_id: 'po-3',
  grn_id: 'grn-3',
  match_status: 'MATCHED',
  vendor_invoice_date: '2026-06-29',
});
const ON_HOLD = inv({
  purchase_invoice_id: 'pi-oh-1',
  vendor_invoice_no: 'INV-OH-1',
  po_id: 'po-4',
  grn_id: 'grn-4',
  match_status: 'ON_HOLD_EXCEPTION',
  match_detail: {
    match_status: 'ON_HOLD_EXCEPTION',
    lines: [],
    exceptions: ['Qty variance 20% on Ray-Ban RX5154'],
  },
  vendor_invoice_date: '2026-06-28',
});
const SETTLED = inv({
  purchase_invoice_id: 'pi-st-1',
  vendor_invoice_no: 'INV-ST-1',
  po_id: 'po-5',
  recon: SETTLED_RECON,
  status: 'PAID',
  vendor_invoice_date: '2026-06-27',
});

const ALL_ROWS = [UNLINKED, LINKED, MATCHED_A, MATCHED_B, ON_HOLD, SETTLED];

const EMPTY_WORKLISTS = {
  stock_yet_to_receive: [],
  vendor_returns: [],
  pending_credit_notes_scheme: [],
  pending_credit_notes_return: [],
};

function renderConsole() {
  return render(
    <MemoryRouter>
      <ReconConsole />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listMock.mockResolvedValue({ purchase_invoices: ALL_ROWS, total: ALL_ROWS.length });
  worklistsMock.mockResolvedValue(EMPTY_WORKLISTS);
  getReconMock.mockResolvedValue(null);
  upsertMock.mockImplementation(async (id: string, payload: Record<string, unknown>) => ({
    invoice_id: id,
    recon: { ...EMPTY_RECON, ...payload },
  }));
});

// ---------------------------------------------------------------------------
// Pure queue-classification helpers
// ---------------------------------------------------------------------------

describe('queue classification helpers', () => {
  it('isUnlinked: true only when no po_id, no grn_id and no linked DCs', () => {
    expect(isUnlinked(UNLINKED)).toBe(true);
    expect(isUnlinked(LINKED)).toBe(false);
    expect(isUnlinked(inv({ purchase_invoice_id: 'x', grn_id: 'g' }))).toBe(false);
    expect(isUnlinked(inv({ purchase_invoice_id: 'x', linked_dc_ids: ['dc-1'] }))).toBe(false);
    expect(isUnlinked(inv({ purchase_invoice_id: 'x', linked_dc_ids: [] }))).toBe(true);
  });

  it('isSettled: all four attestations must be ticked', () => {
    expect(isSettled(SETTLED_RECON)).toBe(true);
    expect(isSettled(EMPTY_RECON)).toBe(false);
    expect(isSettled({ ...SETTLED_RECON, filed_gst: false })).toBe(false);
  });

  it('chipMatches routes each species to the right chips', () => {
    expect(chipMatches('all', UNLINKED, EMPTY_RECON)).toBe(true);
    expect(chipMatches('unlinked', UNLINKED, EMPTY_RECON)).toBe(true);
    expect(chipMatches('unlinked', LINKED, EMPTY_RECON)).toBe(false);
    expect(chipMatches('auto_matched', MATCHED_A, EMPTY_RECON)).toBe(true);
    expect(chipMatches('auto_matched', LINKED, EMPTY_RECON)).toBe(false);
    expect(chipMatches('on_hold', ON_HOLD, EMPTY_RECON)).toBe(true);
    expect(chipMatches('on_hold', MATCHED_A, EMPTY_RECON)).toBe(false);
    expect(chipMatches('settled', SETTLED, SETTLED_RECON)).toBe(true);
    expect(chipMatches('needs_review', SETTLED, SETTLED_RECON)).toBe(false);
    expect(chipMatches('needs_review', LINKED, EMPTY_RECON)).toBe(true);
  });

  it('attentionRank: on hold < unlinked < in-progress < settled', () => {
    expect(attentionRank(ON_HOLD, EMPTY_RECON)).toBe(0);
    expect(attentionRank(UNLINKED, EMPTY_RECON)).toBe(1);
    expect(attentionRank(LINKED, EMPTY_RECON)).toBe(2);
    expect(attentionRank(SETTLED, SETTLED_RECON)).toBe(3);
  });

  it('isBatchEligible: MATCHED and Reconciled not yet ticked', () => {
    expect(isBatchEligible(MATCHED_A, EMPTY_RECON)).toBe(true);
    expect(isBatchEligible(MATCHED_A, { ...EMPTY_RECON, reconciled: true })).toBe(false);
    expect(isBatchEligible(LINKED, EMPTY_RECON)).toBe(false);
    expect(isBatchEligible(ON_HOLD, EMPTY_RECON)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Queue rendering + filter chips
// ---------------------------------------------------------------------------

describe('unified queue', () => {
  it('renders every invoice in one queue with filter chips + counts', async () => {
    renderConsole();
    await screen.findByText('INV-UL-1');

    // All six species in the single table
    for (const label of ['INV-UL-1', 'INV-LK-1', 'INV-AM-1', 'INV-AM-2', 'INV-OH-1', 'INV-ST-1']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }

    // Chips present with counts
    expect(screen.getByRole('tab', { name: /All\s*6/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Needs review\s*5/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /On hold \(exceptions\)\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Unlinked\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Auto-matched\s*2/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Settled\s*1/ })).toBeInTheDocument();

    // Embedded recon blocks used -> no N+1 per-invoice GETs
    expect(getReconMock).not.toHaveBeenCalled();
  });

  it('shows the NO PO LINKED badge only on truly unlinked rows', async () => {
    renderConsole();
    await screen.findByText('INV-UL-1');

    const badges = screen.getAllByText('NO PO LINKED');
    expect(badges).toHaveLength(1);
    // The badge sits in the unlinked invoice's row
    const row = badges[0].closest('tr') as HTMLElement;
    expect(within(row).getByText('INV-UL-1')).toBeInTheDocument();
  });

  it('Unlinked chip filters the queue to unlinked rows only', async () => {
    renderConsole();
    await screen.findByText('INV-UL-1');

    fireEvent.click(screen.getByRole('tab', { name: /Unlinked/ }));

    expect(screen.getByText('INV-UL-1')).toBeInTheDocument();
    expect(screen.queryByText('INV-LK-1')).not.toBeInTheDocument();
    expect(screen.queryByText('INV-AM-1')).not.toBeInTheDocument();
    expect(screen.queryByText('INV-ST-1')).not.toBeInTheDocument();
  });

  it('Settled chip shows only fully-attested invoices', async () => {
    renderConsole();
    await screen.findByText('INV-UL-1');

    fireEvent.click(screen.getByRole('tab', { name: /Settled/ }));

    expect(screen.getByText('INV-ST-1')).toBeInTheDocument();
    expect(screen.queryByText('INV-UL-1')).not.toBeInTheDocument();
    expect(screen.queryByText('INV-AM-1')).not.toBeInTheDocument();
  });

  it('marks auto-matched rows with the AUTO-MATCHED chip and a checkbox', async () => {
    renderConsole();
    await screen.findByText('INV-AM-1');

    expect(screen.getAllByText('AUTO-MATCHED')).toHaveLength(2);
    expect(screen.getByLabelText('Select INV-AM-1 for batch confirm')).toBeInTheDocument();
    expect(screen.getByLabelText('Select INV-AM-2 for batch confirm')).toBeInTheDocument();
    // Non-matched rows get no batch checkbox
    expect(screen.queryByLabelText('Select INV-UL-1 for batch confirm')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Select INV-OH-1 for batch confirm')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Batch confirm: sequential per-invoice writes, continue-on-error
// ---------------------------------------------------------------------------

describe('batch confirm', () => {
  it('loops the EXISTING per-invoice endpoint once per selected row', async () => {
    renderConsole();
    await screen.findByText('INV-AM-1');

    fireEvent.click(screen.getByLabelText('Select INV-AM-1 for batch confirm'));
    fireEvent.click(screen.getByLabelText('Select INV-AM-2 for batch confirm'));

    const confirmBtn = await screen.findByRole('button', { name: /Confirm 2 selected/ });
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(upsertMock).toHaveBeenCalledTimes(2));
    expect(upsertMock).toHaveBeenNthCalledWith(1, 'pi-am-1', { reconciled: true });
    expect(upsertMock).toHaveBeenNthCalledWith(2, 'pi-am-2', { reconciled: true });
    await waitFor(() =>
      expect(toastMock.success).toHaveBeenCalledWith('Confirmed 2 invoices as Reconciled'),
    );
  });

  it('continues past a failure and surfaces the per-row error', async () => {
    upsertMock.mockImplementation(async (id: string, payload: Record<string, unknown>) => {
      if (id === 'pi-am-1') throw new Error('period locked');
      return { invoice_id: id, recon: { ...EMPTY_RECON, ...payload } };
    });

    renderConsole();
    await screen.findByText('INV-AM-1');

    fireEvent.click(screen.getByLabelText('Select INV-AM-1 for batch confirm'));
    fireEvent.click(screen.getByLabelText('Select INV-AM-2 for batch confirm'));
    fireEvent.click(await screen.findByRole('button', { name: /Confirm 2 selected/ }));

    // BOTH rows attempted despite the first failing (continue-on-error)
    await waitFor(() => expect(upsertMock).toHaveBeenCalledTimes(2));
    expect(upsertMock).toHaveBeenNthCalledWith(1, 'pi-am-1', { reconciled: true });
    expect(upsertMock).toHaveBeenNthCalledWith(2, 'pi-am-2', { reconciled: true });

    // Failure surfaced: summary toast + per-row badge; failed row stays selected
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        '1 confirmed, 1 failed — failed rows stay selected',
      ),
    );
    const failBadge = await screen.findByText('CONFIRM FAILED');
    const row = failBadge.closest('tr') as HTMLElement;
    expect(within(row).getByText('INV-AM-1')).toBeInTheDocument();
    expect(
      (screen.getByLabelText('Select INV-AM-1 for batch confirm') as HTMLInputElement).checked,
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Attestation note (same per-invoice endpoint, `note` field)
// ---------------------------------------------------------------------------

describe('attestation note', () => {
  it('saves a short note via the per-invoice recon endpoint', async () => {
    renderConsole();
    await screen.findByText('INV-LK-1');

    // Narrow to one row so there is a single note control
    fireEvent.change(screen.getByPlaceholderText('Search vendor or invoice #...'), {
      target: { value: 'INV-LK-1' },
    });
    await waitFor(() => expect(screen.queryByText('INV-UL-1')).not.toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Add attestation note'));
    fireEvent.change(screen.getByPlaceholderText('e.g. "Paid via NEFT ref 5401"'), {
      target: { value: 'Paid via NEFT ref 5401' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save note' }));

    await waitFor(() =>
      expect(upsertMock).toHaveBeenCalledWith('pi-lk-1', { note: 'Paid via NEFT ref 5401' }),
    );
  });
});
