// ============================================================================
// IMS 2.0 - a refused delivery challan says why
// ============================================================================
// A transfer between two GST registrations prints a valued challan (owner
// ruling D13): the server refuses it before ship, without both GSTINs, or to
// a counter role -- and the screen must show that reason, not a bare status.

import { vi, beforeEach, afterEach, describe, it, expect } from 'vitest';
import { printDocumentsApi } from '../printDocuments';

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('localStorage', { getItem: () => null });
  fetchMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('printDocumentsApi.openTransferChallan', () => {
  it("rejects with the server's own reason", async () => {
    const detail = 'Ship the transfer first: a challan between two GST registrations is valued.';
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 409 }));
    await expect(printDocumentsApi.openTransferChallan('trf_1')).rejects.toThrow(detail);
  });

  it('falls back to the status when the body has no reason', async () => {
    fetchMock.mockResolvedValue(new Response('oops', { status: 502 }));
    await expect(printDocumentsApi.openTransferChallan('trf_1')).rejects.toThrow(
      'Failed to render document (502)',
    );
  });
});
