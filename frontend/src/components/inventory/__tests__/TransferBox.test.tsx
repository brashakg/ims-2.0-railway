// ============================================================================
// S3: one box panel packs (scan OUT) and receives (scan IN) piece by piece
// ============================================================================

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const packTransfer = vi.fn();
const scanInPiece = vi.fn();
const toastError = vi.fn();

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: toastError, success: () => {}, warning: () => {}, info: () => {} }),
}));
vi.mock('../../../services/api', () => ({
  inventoryApi: {
    packTransfer: (...a: unknown[]) => packTransfer(...a),
    scanInPiece: (...a: unknown[]) => scanInPiece(...a),
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
    await waitFor(() => expect(packTransfer).toHaveBeenCalledWith('trf_1', ['BV1', 'BV2']));
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
