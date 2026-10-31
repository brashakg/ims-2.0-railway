// The red DO NOT SHELVE label has ONE door (quarantineLabels.ts): Receive and
// the Quarantine queue both check, record and print through it.
import { describe, it, expect, vi, beforeEach } from 'vitest';

const inv = vi.hoisted(() => ({ printQuarantineLabel: vi.fn() }));
const unit = vi.hoisted(() => ({ printUnitLabels: vi.fn(() => ({ method: 'html', message: '' })) }));
vi.mock('../../../services/api/inventory', () => ({ inventoryApi: inv }));
vi.mock('../unitLabel', async (orig) => ({ ...(await orig<typeof import('../unitLabel')>()), ...unit }));

import { printQuarantineLabels, quarantineLabelData } from '../quarantineLabels';

const PAYLOAD = {
  ok: true, label_type: 'QUARANTINED', header: 'QUARANTINED -- DO NOT SHELVE', stock_id: 'Q1',
  barcode_value: 'BV24CA0101P4', name: 'Cartier CT0001', brand: 'Cartier', quarantine_reason: 'OTHER',
  quarantine_at: '2026-10-31T10:00:00+05:30', store_code: 'BOK02', luxury_brand_line: 'BRAND AUTHORIZATION REQUIRED',
};

beforeEach(() => vi.clearAllMocks());

describe('the DO NOT SHELVE label door', () => {
  it('maps the payload: brand + model, reason / shop / date, the luxury line', () => {
    expect(quarantineLabelData(PAYLOAD)).toEqual({
      barcode: 'BV24CA0101P4', brand: 'Cartier', model: 'Cartier CT0001', doNotShelve: true,
      detail: 'OTHER · BOK02 · 2026-10-31', note: 'BRAND AUTHORIZATION REQUIRED',
    });
  });

  it('records each label, then prints them all once, from the server payloads', async () => {
    inv.printQuarantineLabel.mockResolvedValue(PAYLOAD);
    const r = await printQuarantineLabels([{ stock_id: 'Q1', barcode: 'BV24CA0101P4' }]);
    expect(r.method).toBe('html');
    expect(inv.printQuarantineLabel).toHaveBeenCalledWith('Q1');
    expect(unit.printUnitLabels).toHaveBeenCalledTimes(1);
    expect(unit.printUnitLabels).toHaveBeenCalledWith([quarantineLabelData(PAYLOAD)]);
  });

  it('a barcode no label can carry refuses the batch before anything is recorded', async () => {
    const r = await printQuarantineLabels([{ stock_id: 'Q1', barcode: 'BV24CA0101P4' }, { stock_id: 'Q2', barcode: 'é' }]);
    expect(r.method).toBe('failed');
    expect(inv.printQuarantineLabel).not.toHaveBeenCalled();
    expect(unit.printUnitLabels).not.toHaveBeenCalled();
  });
});
