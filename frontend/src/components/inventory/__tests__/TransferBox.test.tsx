// ============================================================================
// S3: one box panel packs (scan OUT) and receives (scan IN) piece by piece
// ============================================================================

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const packTransfer = vi.fn();
const scanInPiece = vi.fn();
const shipTransfer = vi.fn();
const getTransfer = vi.fn();
const toastError = vi.fn();

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: toastError, success: () => {}, warning: () => {}, info: () => {} }),
}));
vi.mock('../../../services/api', () => ({
  inventoryApi: {
    packTransfer: (...a: unknown[]) => packTransfer(...a),
    scanInPiece: (...a: unknown[]) => scanInPiece(...a),
    shipTransfer: (...a: unknown[]) => shipTransfer(...a),
    getTransfer: (...a: unknown[]) => getTransfer(...a),
  },
}));

import { TransferBox, boxPieces, type BoxTransfer } from '../TransferBox';

const box = (over: Partial<BoxTransfer>): BoxTransfer => ({
  id: 'trf_1',
  status: 'approved',
  from_location_id: 'DHN',
  from_location_name: 'Dhanbad',
  to_location_id: 'BOK',
  to_location_name: 'Bokaro',
  items: [{ id: 'l1', product_id: 'P1', product_name: 'Carrera', quantity_requested: 2, packed_barcodes: ['BV1'] }],
  rev: 4,
  ...over,
});
const sender = { roles: ['STORE_MANAGER'], storeIds: ['DHN'], activeStoreId: 'DHN' };
const receiver = { roles: ['STORE_MANAGER'], storeIds: ['BOK'], activeStoreId: 'BOK' };

beforeEach(() => {
  vi.clearAllMocks();
  packTransfer.mockResolvedValue({ transfer: box({}) });
  scanInPiece.mockResolvedValue({ transfer: box({ status: 'partially_received' }) });
});

describe('TransferBox', () => {
  it('a scan out re-sends the WHOLE box, so a retry never double-packs', async () => {
    render(<TransferBox transfer={box({})} actor={sender} onChanged={() => {}} onDone={() => {}} />);
    const input = screen.getByLabelText('Scan a piece out');
    fireEvent.change(input, { target: { value: ' BV2 ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(packTransfer).toHaveBeenCalledWith('trf_1', ['BV1', 'BV2'], 4));
  });

  it('taking a piece out while a scan is saving queues behind it, each with the box as it then stands', async () => {
    let release: (v: unknown) => void = () => {};
    const afterScan = box({
      rev: 5,
      items: [{ id: 'l1', product_id: 'P1', product_name: 'Carrera', quantity_requested: 3, packed_barcodes: ['BV1', 'BV2'] }],
    });
    packTransfer.mockImplementationOnce(() => new Promise((r) => (release = r)));
    const t = box({ items: [{ id: 'l1', product_id: 'P1', product_name: 'Carrera', quantity_requested: 3, packed_barcodes: ['BV1'] }] });
    render(<TransferBox transfer={t} actor={sender} onChanged={() => {}} onDone={() => {}} />);
    const input = screen.getByLabelText('Scan a piece out');
    fireEvent.change(input, { target: { value: 'BV2' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.click(screen.getByLabelText('Take BV1 out of the box'));
    expect(packTransfer).toHaveBeenCalledTimes(1);
    release({ transfer: afterScan });
    await waitFor(() => expect(packTransfer).toHaveBeenCalledTimes(2));
    expect(packTransfer.mock.calls[0].slice(1)).toEqual([['BV1', 'BV2'], 4]);
    expect(packTransfer.mock.calls[1].slice(1)).toEqual([['BV2'], 5]);
  });

  it('a refused pack reloads the box, so the next scan never re-sends a stale list', async () => {
    const fresh = box({
      rev: 7,
      items: [{ id: 'l1', product_id: 'P1', product_name: 'Carrera', quantity_requested: 3, packed_barcodes: ['BV1', 'BV9'] }],
    });
    packTransfer.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'This box changed' } } });
    getTransfer.mockResolvedValue({ transfer: fresh });
    const onChanged = vi.fn();
    render(<TransferBox transfer={box({})} actor={sender} onChanged={onChanged} onDone={() => {}} />);
    const input = screen.getByLabelText('Scan a piece out');
    fireEvent.change(input, { target: { value: 'BV2' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(fresh));
    expect(getTransfer).toHaveBeenCalledWith('trf_1');
    fireEvent.change(input, { target: { value: 'BV3' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(packTransfer).toHaveBeenCalledTimes(2));
    expect(packTransfer.mock.calls[1].slice(1)).toEqual([['BV1', 'BV9', 'BV3'], 7]);
  });

  it('workshop staff at the sending shop add the courier and tracking no. and ship', async () => {
    const workshop = { roles: ['WORKSHOP_STAFF'], storeIds: ['DHN'], activeStoreId: 'DHN' };
    shipTransfer.mockResolvedValue({ transfer: box({ status: 'in_transit' }) });
    render(<TransferBox transfer={box({ status: 'packed' })} actor={workshop} onChanged={() => {}} onDone={() => {}} />);
    fireEvent.change(screen.getByLabelText('Courier'), { target: { value: 'DTDC' } });
    fireEvent.change(screen.getByLabelText('Tracking no.'), { target: { value: 'D41092277' } });
    expect(screen.queryByLabelText('E-way bill no.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Ship 1 pieces/ }));
    await waitFor(() =>
      expect(shipTransfer).toHaveBeenCalledWith('trf_1', { courierName: 'DTDC', trackingNumber: 'D41092277' }),
    );
  });

  it('a damaged scan in needs a reason before anything is sent', async () => {
    const t = box({
      status: 'in_transit',
      items: [{ id: 'l1', product_id: 'P1', product_name: 'Carrera', shipped_stock_ids: ['S1'], shipped_barcodes: ['BV1'] }],
    });
    render(<TransferBox transfer={t} actor={receiver} onChanged={() => {}} onDone={() => {}} />);
    fireEvent.click(screen.getByLabelText(/Damaged in the box/));
    const input = screen.getByLabelText('Scan a piece in');
    fireEvent.change(input, { target: { value: 'BV1' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(scanInPiece).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/What is wrong/), { target: { value: 'Broken hinge' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() =>
      expect(scanInPiece).toHaveBeenCalledWith('trf_1', { barcode: 'BV1', damaged: true, damageReason: 'Broken hinge' }),
    );
  });

  it('the receiving shop cannot pack and the sending shop cannot scan in', () => {
    render(<TransferBox transfer={box({})} actor={receiver} onChanged={() => {}} onDone={() => {}} />);
    expect(screen.queryByLabelText(/Scan a piece/)).toBeNull();
  });

  it('a scan made while the last one is still saving is queued, never dropped', async () => {
    const t = box({
      status: 'in_transit',
      items: [{ id: 'l1', product_id: 'P1', product_name: 'Carrera', shipped_stock_ids: ['S1', 'S2'], shipped_barcodes: ['BV1', 'BV2'] }],
    });
    let release: (v: unknown) => void = () => {};
    scanInPiece.mockImplementationOnce(() => new Promise((r) => (release = r)));
    render(<TransferBox transfer={t} actor={receiver} onChanged={() => {}} onDone={() => {}} />);
    const input = screen.getByLabelText('Scan a piece in') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'BV1' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    // The gun's next code lands in an empty field while BV1 is still saving.
    expect(input.value).toBe('');
    fireEvent.change(input, { target: { value: 'BV2' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(scanInPiece).toHaveBeenCalledTimes(1);
    release({ transfer: t });
    await waitFor(() => expect(scanInPiece).toHaveBeenCalledTimes(2));
    expect(scanInPiece.mock.calls.map((c) => c[1].barcode)).toEqual(['BV1', 'BV2']);
  });

  it('queued scans out each re-send the box as the server last answered it', async () => {
    let release: (v: unknown) => void = () => {};
    const afterFirst = box({
      items: [{ id: 'l1', product_id: 'P1', product_name: 'Carrera', quantity_requested: 3, packed_barcodes: ['BV1', 'BV2'] }],
    });
    packTransfer.mockImplementationOnce(() => new Promise((r) => (release = r)));
    render(<TransferBox transfer={box({})} actor={sender} onChanged={() => {}} onDone={() => {}} />);
    const input = screen.getByLabelText('Scan a piece out');
    fireEvent.change(input, { target: { value: 'BV2' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.change(input, { target: { value: 'BV3' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    release({ transfer: afterFirst });
    await waitFor(() => expect(packTransfer).toHaveBeenCalledTimes(2));
    expect(packTransfer.mock.calls[1][1]).toEqual(['BV1', 'BV2', 'BV3']);
  });

  it('Accept names how many pieces will be reported missing', () => {
    const t = box({
      status: 'partially_received',
      items: [{ id: 'l1', product_id: 'P1', product_name: 'Carrera', shipped_stock_ids: ['S1', 'S2', 'S3'],
                shipped_barcodes: ['BV1', 'BV2', 'BV3'], received_stock_ids: ['S1'] }],
    });
    render(<TransferBox transfer={t} actor={receiver} onChanged={() => {}} onDone={() => {}} />);
    expect(screen.getByRole('button', { name: /Accept 1, report 2 missing/ })).toBeInTheDocument();
  });

  it('a box does not ship without the courier and the tracking no.', () => {
    render(<TransferBox transfer={box({ status: 'packed' })} actor={sender} onChanged={() => {}} onDone={() => {}} />);
    fireEvent.change(screen.getByLabelText('Courier'), { target: { value: 'DTDC' } });
    fireEvent.click(screen.getByRole('button', { name: /Ship 1 pieces/ }));
    expect(shipTransfer).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith(expect.stringContaining('tracking no.'));
  });

  it('each shipped piece shows where it stands', () => {
    const pieces = boxPieces({
      product_id: 'P1',
      product_name: 'Carrera',
      shipped_stock_ids: ['S1', 'S2', 'S3'],
      shipped_barcodes: ['BV1', 'BV2', 'BV3'],
      received_stock_ids: ['S1'],
      damaged_stock_ids: ['S2'],
    });
    expect(pieces.map((p) => [p.barcode, p.state])).toEqual([
      ['BV1', 'received'],
      ['BV2', 'damaged'],
      ['BV3', 'waiting'],
    ]);
  });
});
