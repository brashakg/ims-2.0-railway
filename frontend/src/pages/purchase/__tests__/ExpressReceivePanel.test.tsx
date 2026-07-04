// ============================================================================
// IMS 2.0 - ExpressReceivePanel state-machine tests (procurement Phase 2)
// ============================================================================
// Pins the council-ruled guided receive:
//   - step 1 gating (strict no-paper-no-stock: file + invoice no before items)
//   - clean box -> POST /vendors/grn/express with received=accepted, rejected=0
//   - ANY edit that breaks clean (rejected>0) -> automatic two-step fallback
//   - server EXPRESS_NOT_CLEAN -> automatic two-step fallback
//   - EXPRESS_PARTIAL -> bold recovery banner -> pending-receipts panel
//   - ATTACHMENT_REQUIRED -> back to step 1 with the toast

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock('../../../services/api/grnCockpit', () => ({
  grnCockpitApi: {
    uploadDoc: vi.fn(),
    expressReceive: vi.fn(),
  },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => toastMock,
}));

import { ExpressReceivePanel } from '../ExpressReceivePanel';
import { grnCockpitApi } from '../../../services/api/grnCockpit';
import type { CockpitOpenPO } from '../../../services/api/grnCockpit';

const uploadDocMock = grnCockpitApi.uploadDoc as unknown as ReturnType<typeof vi.fn>;
const expressMock = grnCockpitApi.expressReceive as unknown as ReturnType<typeof vi.fn>;

const PO: CockpitOpenPO = {
  po_id: 'po-1',
  po_number: 'PO-BV-26-0007',
  status: 'SENT',
  expected_date: '2026-07-06',
  lines: [
    {
      product_id: 'prod-a',
      product_name: 'Ray-Ban RX5154',
      sku: 'RB5154',
      ordered_qty: 10,
      received_qty: 4,
      pending_qty: 6,
      unit_price: 3200,
      tax_rate: 18,
    },
    {
      product_id: 'prod-b',
      product_name: 'Crizal Rock 1.5',
      sku: 'CRZ-15',
      ordered_qty: 20,
      received_qty: 0,
      pending_qty: 20,
      unit_price: 900,
      tax_rate: 18,
    },
  ],
};

const UPLOAD_OK = {
  file_id: 'file-1',
  filename: 'bill.pdf',
  mime: 'application/pdf',
  size: 2048,
  sha256: 'abc',
  persisted: true,
};

const EXPRESS_OK = {
  grn_id: 'grn-1',
  grn_number: 'GRN-BOK-26-0042',
  accepted_units: 26,
  po_status: 'RECEIVED',
  invoice_draft: {
    vendor_id: 'v-1',
    invoice_number: 'JJ/26/07/001',
    place_of_supply: '20',
    lines_count: 2,
    totals: {
      taxable_total: 37200,
      cgst_total: 3348,
      sgst_total: 3348,
      igst_total: 0,
      tax_total: 6696,
      total: 43896,
    },
  },
  match_preview: { match_status: 'MATCHED', exception_count: 0 },
  accountant_task_id: 'task-1',
};

function renderPanel(overrides: Partial<Parameters<typeof ExpressReceivePanel>[0]> = {}) {
  const props = {
    po: PO,
    vendorName: 'Luxottica India',
    onCancel: vi.fn(),
    onReceived: vi.fn(),
    onFallbackToTwoStep: vi.fn(),
    onOpenPendingReceipts: vi.fn(),
    ...overrides,
  };
  const utils = render(<ExpressReceivePanel {...props} />);
  return { ...utils, props };
}

/** Complete STEP 1: upload the bill + enter the invoice number, then continue. */
async function passStep1(container: HTMLElement) {
  uploadDocMock.mockResolvedValue(UPLOAD_OK);
  const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(['x'], 'bill.pdf', { type: 'application/pdf' });
  fireEvent.change(fileInput, { target: { files: [file] } });
  await waitFor(() => expect(screen.getByText('bill.pdf')).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText('Vendor invoice number'), {
    target: { value: 'JJ/26/07/001' },
  });
  fireEvent.click(screen.getByRole('button', { name: /continue — check items/i }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: /everything arrived as ordered/i }),
    ).toBeInTheDocument(),
  );
}

/** From STEP 2 (untouched) to STEP 3. */
async function passStep2() {
  fireEvent.click(screen.getByRole('button', { name: /everything arrived as ordered/i }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: /put on shelf & send to accounts/i }),
    ).toBeInTheDocument(),
  );
}

describe('ExpressReceivePanel — step 1 gating (bill first)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('blocks continuing without both the file and the invoice number', async () => {
    const { container } = renderPanel();
    const continueBtn = screen.getByRole('button', { name: /continue — check items/i });
    // Nothing yet -> disabled
    expect(continueBtn).toBeDisabled();
    // Invoice number alone -> still disabled (no paper, no stock)
    fireEvent.change(screen.getByLabelText('Vendor invoice number'), {
      target: { value: 'JJ/26/07/001' },
    });
    expect(continueBtn).toBeDisabled();
    // File alone (invoice cleared) -> still disabled
    fireEvent.change(screen.getByLabelText('Vendor invoice number'), {
      target: { value: '   ' },
    });
    uploadDocMock.mockResolvedValue(UPLOAD_OK);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(['x'], 'bill.pdf', { type: 'application/pdf' })] },
    });
    await waitFor(() => expect(screen.getByText('bill.pdf')).toBeInTheDocument());
    expect(continueBtn).toBeDisabled();
    // Both -> enabled
    fireEvent.change(screen.getByLabelText('Vendor invoice number'), {
      target: { value: 'JJ/26/07/001' },
    });
    expect(continueBtn).toBeEnabled();
  });
});

describe('ExpressReceivePanel — clean box goes express', () => {
  beforeEach(() => vi.clearAllMocks());

  it('sends received=accepted=residual, rejected=0 and shows the success panel', async () => {
    expressMock.mockResolvedValue(EXPRESS_OK);
    const { container } = renderPanel();
    await passStep1(container);
    // Lines prefilled from the PO residuals
    expect(screen.getByText('Ray-Ban RX5154')).toBeInTheDocument();
    expect(screen.getByText('Crizal Rock 1.5')).toBeInTheDocument();
    await passStep2();
    fireEvent.click(
      screen.getByRole('button', { name: /put on shelf & send to accounts/i }),
    );

    await waitFor(() => expect(expressMock).toHaveBeenCalledTimes(1));
    expect(expressMock).toHaveBeenCalledWith(
      expect.objectContaining({
        po_id: 'po-1',
        vendor_invoice_no: 'JJ/26/07/001',
        attachment_file_id: 'file-1',
        items: [
          expect.objectContaining({
            product_id: 'prod-a',
            received_qty: 6,
            accepted_qty: 6,
            rejected_qty: 0,
          }),
          expect.objectContaining({
            product_id: 'prod-b',
            received_qty: 20,
            accepted_qty: 20,
            rejected_qty: 0,
          }),
        ],
      }),
    );

    // Success panel: GRN number, units, PO chip (RECEIVED -> "On shelf"),
    // match preview + invoice total.
    await waitFor(() =>
      expect(screen.getByText('GRN-BOK-26-0042')).toBeInTheDocument(),
    );
    expect(screen.getByText(/26 units added to stock/i)).toBeInTheDocument();
    expect(screen.getByText('On shelf')).toBeInTheDocument();
    expect(screen.getByText(/sent to accounts/i)).toBeInTheDocument();
    expect(screen.getByText('MATCHED')).toBeInTheDocument();
    expect(screen.getByText(/43,896/)).toBeInTheDocument();
  });
});

describe('ExpressReceivePanel — non-clean edits flip to the two-step path', () => {
  beforeEach(() => vi.clearAllMocks());

  it('fires onFallbackToTwoStep the moment a rejection is entered', async () => {
    const onFallback = vi.fn();
    const { container } = renderPanel({ onFallbackToTwoStep: onFallback });
    await passStep1(container);

    // Expand the first line and reject one unit
    fireEvent.click(screen.getByRole('button', { name: /edit line ray-ban rx5154/i }));
    fireEvent.change(screen.getByLabelText('Rejected qty for Ray-Ban RX5154'), {
      target: { value: '1' },
    });

    expect(onFallback).toHaveBeenCalledTimes(1);
    const prefill = onFallback.mock.calls[0][0];
    expect(prefill.reason).toBe('edited');
    expect(prefill.vendorInvoiceNo).toBe('JJ/26/07/001');
    expect(prefill.upload).toEqual(expect.objectContaining({ file_id: 'file-1' }));
    // accepted = received - rejected on the edited line
    expect(prefill.lines[0]).toEqual(
      expect.objectContaining({
        product_id: 'prod-a',
        received_qty: 6,
        accepted_qty: 5,
        rejected_qty: 1,
      }),
    );
    expect(expressMock).not.toHaveBeenCalled();
  });

  it('reducing the received qty stays clean (no fallback) and goes express', async () => {
    expressMock.mockResolvedValue(EXPRESS_OK);
    const onFallback = vi.fn();
    const { container } = renderPanel({ onFallbackToTwoStep: onFallback });
    await passStep1(container);

    fireEvent.click(screen.getByRole('button', { name: /edit line ray-ban rx5154/i }));
    fireEvent.change(screen.getByLabelText('Received qty for Ray-Ban RX5154'), {
      target: { value: '4' },
    });
    expect(onFallback).not.toHaveBeenCalled();

    // Touched-but-clean -> button label switches to quantities variant
    fireEvent.click(
      screen.getByRole('button', { name: /continue with these quantities/i }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /put on shelf & send to accounts/i }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(
      screen.getByRole('button', { name: /put on shelf & send to accounts/i }),
    );
    await waitFor(() => expect(expressMock).toHaveBeenCalledTimes(1));
    expect(expressMock.mock.calls[0][0].items[0]).toEqual(
      expect.objectContaining({ received_qty: 4, accepted_qty: 4, rejected_qty: 0 }),
    );
  });

  it('falls back to two-step when the server answers EXPRESS_NOT_CLEAN', async () => {
    expressMock.mockRejectedValue({
      response: {
        data: {
          detail: { code: 'EXPRESS_NOT_CLEAN', message: 'Line 1 is not clean' },
        },
      },
    });
    const onFallback = vi.fn();
    const { container } = renderPanel({ onFallbackToTwoStep: onFallback });
    await passStep1(container);
    await passStep2();
    fireEvent.click(
      screen.getByRole('button', { name: /put on shelf & send to accounts/i }),
    );
    await waitFor(() => expect(onFallback).toHaveBeenCalledTimes(1));
    expect(onFallback.mock.calls[0][0].reason).toBe('server-rejected');
  });
});

describe('ExpressReceivePanel — server failure modes', () => {
  beforeEach(() => vi.clearAllMocks());

  it('EXPRESS_PARTIAL shows the bold recovery banner linking pending receipts', async () => {
    expressMock.mockRejectedValue({
      response: {
        data: {
          detail: {
            code: 'EXPRESS_PARTIAL',
            grn_id: 'grn-9',
            grn_number: 'GRN-BOK-26-0099',
            message:
              'Receipt GRN-BOK-26-0099 was created but not accepted -- open the receiving screen to accept or void it.',
          },
        },
      },
    });
    const onOpenPending = vi.fn();
    const { container } = renderPanel({ onOpenPendingReceipts: onOpenPending });
    await passStep1(container);
    await passStep2();
    fireEvent.click(
      screen.getByRole('button', { name: /put on shelf & send to accounts/i }),
    );

    await waitFor(() =>
      expect(
        screen.getByText(/GRN-BOK-26-0099 was saved but is NOT on the shelf yet/i),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /open pending receipts/i }));
    expect(onOpenPending).toHaveBeenCalledWith('GRN-BOK-26-0099');
  });

  it('ATTACHMENT_REQUIRED returns to step 1 with the toast', async () => {
    expressMock.mockRejectedValue({
      response: {
        data: {
          detail: {
            code: 'ATTACHMENT_REQUIRED',
            message: 'Attach the vendor invoice or delivery challan.',
          },
        },
      },
    });
    const { container } = renderPanel();
    await passStep1(container);
    await passStep2();
    fireEvent.click(
      screen.getByRole('button', { name: /put on shelf & send to accounts/i }),
    );

    // Back at step 1: the upload zone is empty again and the toast fired.
    await waitFor(() =>
      expect(
        screen.getByLabelText('Upload vendor invoice or challan'),
      ).toBeInTheDocument(),
    );
    expect(toastMock.error).toHaveBeenCalledWith(
      'Attach the vendor invoice or delivery challan.',
    );
    expect(
      screen.getByRole('button', { name: /continue — check items/i }),
    ).toBeDisabled();
  });
});
