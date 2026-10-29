// Status readers behind the shared client: every rejection is an ApiError
// carrying `.status` and no axios `.response`, so a reader of
// `err.response.status` never fires. These reject through the REAL transform.
import { describe, expect, it, vi } from 'vitest';
import { buildApiError } from '../client';

const getMock = vi.fn();
const postMock = vi.fn();
vi.mock('../client', async (importOriginal) => {
  const real = await importOriginal<typeof import('../client')>();
  return { ...real, default: { ...real.default, get: (...a: unknown[]) => getMock(...a), post: (...a: unknown[]) => postMock(...a) } };
});

import { classifyLoadError } from '../onlineStore';
import { familyWalletApi, walletErrorText } from '../familyWallet';
import { catalogProductsApi } from '../catalog';

const rejected = (status: number, detail?: unknown) => buildApiError({
  message: 'Request failed',
  response: { status, data: { detail } },
} as Parameters<typeof buildApiError>[0]);

describe('classifyLoadError reads the ApiError status', () => {
  it('tells forbidden, unavailable and error apart', () => {
    expect(classifyLoadError(rejected(403, 'Forbidden: GET /api/v1/online-store/orders requires one of ADMIN'))).toBe('forbidden');
    expect(classifyLoadError(rejected(404))).toBe('unavailable');
    expect(classifyLoadError(rejected(501))).toBe('unavailable');
    expect(classifyLoadError(rejected(500))).toBe('error');
  });
});

describe('familyWalletApi.getByCustomer', () => {
  it('treats the 404 "household not found" as no household', async () => {
    getMock.mockRejectedValueOnce(rejected(404, 'household not found'));
    await expect(familyWalletApi.getByCustomer('C1')).resolves.toBeNull();
  });

  it('still rejects any other failure', async () => {
    const err = rejected(500);
    getMock.mockRejectedValueOnce(err);
    await expect(familyWalletApi.getByCustomer('C1')).rejects.toBe(err);
  });
});

describe('walletErrorText', () => {
  it('turns router codes into sentences, never raw snake_case', () => {
    expect(walletErrorText(rejected(409, 'already_in_household'))).toBe('This customer is already in another household.');
    expect(walletErrorText(rejected(409, 'insufficient_balance'))).toBe('The household does not have enough points.');
    expect(walletErrorText(rejected(400, 'some_new_code'))).toBe('');
    expect(walletErrorText(rejected(409, 'This redemption was already attempted.'))).toBe('This redemption was already attempted.');
  });
});

describe('catalog validateStatus refusals use the one message parser', () => {
  it("names the structured refusal's own message and the invalid field", async () => {
    postMock.mockResolvedValueOnce({ status: 409, data: { detail: { code: 'ALREADY_PROMOTED', message: 'Already a billing product.' } } });
    await expect(catalogProductsApi.promote('P1')).rejects.toThrow('Already a billing product.');
    postMock.mockResolvedValueOnce({ status: 422, data: { detail: [{ loc: ['body', 'mrp'], msg: 'Must be above zero' }] } });
    await expect(catalogProductsApi.promote('P1')).rejects.toThrow('mrp: Must be above zero');
  });
});
