// ============================================================================
// IMS 2.0 - transfer lifecycle client payload tests
// ============================================================================
// These lock the FE->backend CONTRACT for stock transfers (routers/transfers.py).
// The bugs this guards against were real 422/400s in production:
//   * create sent transfer_type:'inter_store' — not a TransferType enum value.
//   * receive sent quantity_received built from a non-existent `item.quantity`
//     field and had to key each line on the transfer LINE id, not product_id.
//   * ship/complete/cancel take QUERY params, not a JSON body.

import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../client';
import { inventoryApi } from '../inventory';

const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;
const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  mockPost.mockResolvedValue({ data: { transfer: {} } });
  mockGet.mockResolvedValue({ data: { transfer: {} } });
});

describe('inventoryApi.getTransfer — fresh single-transfer read', () => {
  it('GETs /transfers/{id} and returns the envelope', async () => {
    mockGet.mockResolvedValue({ data: { transfer: { id: 'trf_1', status: 'packed' } } });
    const res = await inventoryApi.getTransfer('trf_1');
    expect(mockGet).toHaveBeenCalledWith('/transfers/trf_1');
    expect(res.transfer.status).toBe('packed');
  });
});

describe('inventoryApi.createTransfer — create payload', () => {
  it('sends the store_to_store TransferType enum value (never inter_store)', async () => {
    await inventoryApi.createTransfer({
      fromStoreId: 'STORE-A',
      fromStoreName: 'Store A',
      toStoreId: 'STORE-B',
      toStoreName: 'Store B',
      notes: 'urgent',
      items: [
        { productId: 'PROD-1', productName: 'Ray-Ban Aviator', sku: 'RB-1', quantity: 3 },
      ],
    });

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body] = mockPost.mock.calls[0];
    expect(url).toBe('/transfers');
    // The core bug fix: enum value must be store_to_store.
    expect(body.transfer_type).toBe('store_to_store');
    expect(body.transfer_type).not.toBe('inter_store');
    expect(body.from_location_id).toBe('STORE-A');
    expect(body.from_location_name).toBe('Store A');
    expect(body.to_location_id).toBe('STORE-B');
    expect(body.to_location_name).toBe('Store B');
    expect(body.notes).toBe('urgent');
    // Items map to the backend TransferItemInput shape.
    expect(body.items).toEqual([
      {
        product_id: 'PROD-1',
        product_name: 'Ray-Ban Aviator',
        sku: 'RB-1',
        quantity_requested: 3,
        unit_cost: 0,
      },
    ]);
  });

  it('falls back store names to ids and omits notes when absent', async () => {
    await inventoryApi.createTransfer({
      fromStoreId: 'STORE-A',
      toStoreId: 'STORE-B',
      items: [{ productId: 'PROD-9', quantity: 1 }],
    });
    const [, body] = mockPost.mock.calls[0];
    expect(body.from_location_name).toBe('STORE-A');
    expect(body.to_location_name).toBe('STORE-B');
    expect('notes' in body).toBe(false);
    expect(body.items[0].product_name).toBe('PROD-9');
    expect(body.items[0].sku).toBe('');
  });
});

describe('inventoryApi.receiveTransfer — receive payload', () => {
  it('posts a bare LIST keyed on the transfer LINE id with the real receive fields', async () => {
    await inventoryApi.receiveTransfer('trf_1', [
      { transfer_item_id: 'trfi_abc', quantity_received: 2, quantity_damaged: 1 },
      { transfer_item_id: 'trfi_def', quantity_received: 5 },
    ]);

    const [url, body] = mockPost.mock.calls[0];
    expect(url).toBe('/transfers/trf_1/receive');
    // Body is a top-level array (List[TransferItemReceive]).
    expect(Array.isArray(body)).toBe(true);
    expect(body[0]).toEqual({
      transfer_item_id: 'trfi_abc',
      quantity_received: 2,
      quantity_damaged: 1,
    });
    // quantity_damaged defaults to 0 when not supplied.
    expect(body[1]).toEqual({
      transfer_item_id: 'trfi_def',
      quantity_received: 5,
      quantity_damaged: 0,
    });
  });
});

describe('inventoryApi — lifecycle endpoint contracts', () => {
  it('approve sends a JSON body { approved, rejection_reason }', async () => {
    await inventoryApi.approveTransfer('trf_1', true);
    expect(mockPost).toHaveBeenCalledWith('/transfers/trf_1/approve', {
      approved: true,
      rejection_reason: null,
    });
  });

  it('reject carries the rejection reason', async () => {
    await inventoryApi.approveTransfer('trf_1', false, 'no stock');
    expect(mockPost).toHaveBeenCalledWith('/transfers/trf_1/approve', {
      approved: false,
      rejection_reason: 'no stock',
    });
  });

  it('ship sends tracking + carrier as QUERY params with a null body', async () => {
    await inventoryApi.shipTransfer('trf_1', {
      trackingNumber: 'AWB1',
      courierName: 'Delhivery',
    });
    expect(mockPost).toHaveBeenCalledWith('/transfers/trf_1/ship', null, {
      params: { tracking_number: 'AWB1', courier_name: 'Delhivery' },
    });
  });

  it('complete sends notes as a QUERY param (omitted when absent)', async () => {
    await inventoryApi.completeTransfer('trf_1');
    expect(mockPost).toHaveBeenCalledWith('/transfers/trf_1/complete', null, {
      params: {},
    });
  });

  it('cancel sends the required reason QUERY param', async () => {
    await inventoryApi.cancelTransfer('trf_1', 'duplicate');
    expect(mockPost).toHaveBeenCalledWith('/transfers/trf_1/cancel', null, {
      params: { reason: 'duplicate' },
    });
  });
});
