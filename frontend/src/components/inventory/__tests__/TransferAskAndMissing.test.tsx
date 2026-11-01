// ============================================================================
// S3: the Transfers page cards -- "Ask another shop" and "Missing on the way"
// ============================================================================
// The card wiring, not only the gates: who sees which button, what the From
// list offers, the quantity cap, the confirm before a write-off, and the page
// showing the Ask card (and the box to the sending side while on the way).

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const auth = { user: null as null | Record<string, unknown> };
const getStores = vi.fn();
const searchProducts = vi.fn();
const getFreeStock = vi.fn();
const createTransfer = vi.fn();
const getMissingPieces = vi.fn();
const foundMissingPiece = vi.fn();
const writeOffMissingPiece = vi.fn();
const requestMissingWriteOff = vi.fn();
const getTransfers = vi.fn();

vi.mock('../../../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));
vi.mock('../../../services/api', () => ({
  inventoryApi: {
    getFreeStock: (...a: unknown[]) => getFreeStock(...a),
    createTransfer: (...a: unknown[]) => createTransfer(...a),
    getMissingPieces: (...a: unknown[]) => getMissingPieces(...a),
    foundMissingPiece: (...a: unknown[]) => foundMissingPiece(...a),
    writeOffMissingPiece: (...a: unknown[]) => writeOffMissingPiece(...a),
    requestMissingWriteOff: (...a: unknown[]) => requestMissingWriteOff(...a),
    getTransfers: (...a: unknown[]) => getTransfers(...a),
  },
  productApi: { searchProducts: (...a: unknown[]) => searchProducts(...a) },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: (...a: unknown[]) => getStores(...a) },
}));
vi.mock('../../../services/api/printDocuments', () => ({
  printDocumentsApi: { openTransferChallan: () => {}, openTransferInvoice: () => {} },
}));

import { AskForStock, MissingOnTheWay } from '../TransferAskAndMissing';
import { StockTransferManagement } from '../StockTransferManagement';

const MGR_DHN = { id: 'u1', roles: ['STORE_MANAGER'], storeIds: ['ST-DHN-1'], activeStoreId: 'ST-DHN-1' };
const MGR_BOK = { id: 'u2', roles: ['STORE_MANAGER'], storeIds: ['ST-BOK-1'], activeStoreId: 'ST-BOK-1' };
const ADMIN = { id: 'u3', roles: ['ADMIN'], storeIds: [], activeStoreId: 'ST-DHN-1' };
const actorOf = (u: typeof MGR_DHN) => ({ roles: u.roles, storeIds: u.storeIds, activeStoreId: u.activeStoreId });

const STORES = [
  { store_id: 'ST-DHN-1', store_name: 'Hirapur Dhanbad', store_type: 'PHYSICAL' },
  { store_id: 'ST-BOK-1', store_name: 'Sector 4 Bokaro', store_type: 'PHYSICAL' },
  { store_id: 'BV-ONLINE-01', store_name: 'bettervision.in', store_type: '' },
  { store_id: 'WEB-2', store_name: 'Web shop', store_type: 'ONLINE' },
];
const PIECE = {
  stock_id: 'SU-3',
  barcode: 'BVQX003',
  product_name: 'Carrera Frame',
  status: 'missing',
  transfer_id: 'trf_1',
  transfer_number: 'TRF-1',
  from_location_id: 'ST-DHN-1',
  from_location_name: 'Hirapur Dhanbad',
  to_location_id: 'ST-BOK-1',
  to_location_name: 'Sector 4 Bokaro',
};

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = MGR_DHN;
  getStores.mockResolvedValue(STORES);
  searchProducts.mockResolvedValue([{ product_id: 'P-1', name: 'Carrera Frame', sku: 'FR-1' }]);
  getFreeStock.mockResolvedValue({ free: 2, reserved: 1 });
  createTransfer.mockResolvedValue({});
  getMissingPieces.mockResolvedValue({ pieces: [PIECE] });
  foundMissingPiece.mockResolvedValue({});
  writeOffMissingPiece.mockResolvedValue({});
  getTransfers.mockResolvedValue({ transfers: [] });
});
afterEach(() => vi.restoreAllMocks());

async function pickProductFrom(shop: string) {
  fireEvent.change(screen.getByLabelText('Product'), { target: { value: 'Carrera' } });
  fireEvent.click(await screen.findByText('Carrera Frame'));
  await screen.findByRole('option', { name: shop });
  fireEvent.change(screen.getByLabelText('From shop'), { target: { value: 'ST-BOK-1' } });
  await screen.findByText(/has 2 free/);
}

describe('Ask another shop for stock', () => {
  it('offers neither the online shops nor this shop as the source', async () => {
    render(<AskForStock onAsked={() => {}} />);
    await screen.findByRole('option', { name: 'Sector 4 Bokaro' });
    const names = within(screen.getByLabelText('From shop'))
      .getAllByRole('option')
      .map((o) => o.textContent);
    expect(names).toEqual(['From shop', 'Sector 4 Bokaro']);
  });

  it('caps the quantity at what is free there and asks for it', async () => {
    render(<AskForStock onAsked={() => {}} />);
    await pickProductFrom('Sector 4 Bokaro');
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '5' } });
    expect(screen.getByLabelText('Quantity')).toHaveValue(2);
    fireEvent.click(screen.getByRole('button', { name: /Send request/ }));
    await waitFor(() => expect(createTransfer).toHaveBeenCalledTimes(1));
    expect(createTransfer.mock.calls[0][0]).toMatchObject({
      fromStoreId: 'ST-BOK-1',
      toStoreId: 'ST-DHN-1',
      items: [{ productId: 'P-1', quantity: 2 }],
    });
  });

  it('cannot send when nothing is free there', async () => {
    getFreeStock.mockResolvedValue({ free: 0, reserved: 1 });
    render(<AskForStock onAsked={() => {}} />);
    fireEvent.change(screen.getByLabelText('Product'), { target: { value: 'Carrera' } });
    fireEvent.click(await screen.findByText('Carrera Frame'));
    await screen.findByRole('option', { name: 'Sector 4 Bokaro' });
    fireEvent.change(screen.getByLabelText('From shop'), { target: { value: 'ST-BOK-1' } });
    await screen.findByText(/has 0 free/);
    expect(screen.getByRole('button', { name: /Send request/ })).toBeDisabled();
  });

  it.each([
    ['no active shop', { ...ADMIN, activeStoreId: '' }],
    ['the online shop', { ...ADMIN, activeStoreId: 'BV-ONLINE-01' }],
    ['an online-type shop', { ...ADMIN, activeStoreId: 'WEB-2' }],
  ])('is not offered with %s (there is nowhere for the stock to go)', async (_why, user) => {
    auth.user = user;
    render(<AskForStock onAsked={() => {}} />);
    await waitFor(() => expect(getStores).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText('Ask another shop for stock')).toBeNull();
  });
});

describe('Missing on the way', () => {
  it('a receiving manager sees Found at their shop and Send to admin, never Write off', async () => {
    render(<MissingOnTheWay actor={actorOf(MGR_BOK)} refreshKey={0} />);
    await screen.findByText('BVQX003');
    expect(screen.getByRole('button', { name: 'Found at Sector 4 Bokaro' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Found at Hirapur Dhanbad' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Write off' })).toBeNull();
    expect(screen.getByRole('button', { name: /Send to admin/ })).toBeInTheDocument();
  });

  it('a sending manager sees Found at their shop only', async () => {
    render(<MissingOnTheWay actor={actorOf(MGR_DHN)} refreshKey={0} />);
    await screen.findByText('BVQX003');
    expect(screen.getByRole('button', { name: 'Found at Hirapur Dhanbad' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Found at Sector 4 Bokaro' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Write off' })).toBeNull();
  });

  it('Write off asks first; cancelling sends nothing', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<MissingOnTheWay actor={actorOf(ADMIN)} refreshKey={0} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Write off' }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Write off BVQX003?'));
    expect(writeOffMissingPiece).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Write off' }));
    await waitFor(() => expect(writeOffMissingPiece).toHaveBeenCalledWith('trf_1', 'SU-3'));
  });

  it('Found back at the sender asks first; found at the receiver does not', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<MissingOnTheWay actor={actorOf(ADMIN)} refreshKey={0} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Found at Hirapur Dhanbad' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(foundMissingPiece).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Found at Sector 4 Bokaro' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(foundMissingPiece).toHaveBeenCalledWith('trf_1', 'SU-3', 'destination'));
  });
});

describe('the Transfers page', () => {
  it('shows the Ask card to a store manager', async () => {
    getMissingPieces.mockResolvedValue({ pieces: [] });
    render(<StockTransferManagement />);
    expect(await screen.findByText('Ask another shop for stock')).toBeInTheDocument();
  });

  it('lets the sending manager open an in-transit box to type the e-way bill no.', async () => {
    getMissingPieces.mockResolvedValue({ pieces: [] });
    getTransfers.mockResolvedValue({
      transfers: [
        {
          id: 'trf_9',
          transfer_number: 'TRF-9',
          from_location_id: 'ST-DHN-1',
          from_location_name: 'Hirapur Dhanbad',
          to_location_id: 'ST-BOK-1',
          to_location_name: 'Sector 4 Bokaro',
          status: 'in_transit',
          items: [],
          created_by: 'u1',
          created_at: '2026-10-13T05:00:00',
          total_items: 1,
          tax_invoice_number: 'DHN01/T2627/0001',
        },
      ],
    });
    render(<StockTransferManagement />);
    fireEvent.click(await screen.findByText('#TRF-9'));
    fireEvent.click(await screen.findByRole('button', { name: /Open the box/ }));
    expect(await screen.findByLabelText('E-way bill no.')).toBeInTheDocument();
  });
});
