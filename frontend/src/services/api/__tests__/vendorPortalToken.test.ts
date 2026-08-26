// ============================================================================
// IMS 2.0 - vendorsApi portal-token wire-contract tests
// ============================================================================
// The prod bug the owner reported ("Vendor Portal link is just blank"): the
// backend has always answered POST /vendors/{id}/portal-token with
// `portal_path` (a RELATIVE path -- backend/api/routers/vendors.py, pinned by
// backend/tests/test_vendor_portal.py::test_token_issue_admin_only), while the
// client here declared and the modal rendered `portal_url`. That key does not
// exist on the response, so the "Portal URL" box rendered `undefined` -> an
// empty input, and Copy put nothing on the clipboard.
//
// These tests feed the EXACT backend body and pin (a) that a shareable
// absolute URL comes out, and (b) that the TTL is sent under the name the
// backend model actually reads (`ttl_days`, not `expires_days`).

import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../client';
import { vendorsApi } from '../inventory';

const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

// Verbatim shape of the 201 body from issue_portal_token().
const BACKEND_BODY = {
  token_id: '3f2a1b4c-5d6e-4f70-8a91-b2c3d4e5f607',
  vendor_id: 'vendor-zeiss',
  vendor_name: 'Zeiss India',
  expires_at: '2027-08-26T10:00:00',
  portal_path: '/vendor-portal/3f2a1b4c-5d6e-4f70-8a91-b2c3d4e5f607',
  message: 'Vendor portal token issued',
};

beforeEach(() => {
  vi.clearAllMocks();
  mockPost.mockResolvedValue({ data: { ...BACKEND_BODY } });
});

describe('vendorsApi.generatePortalToken', () => {
  it('turns the backend portal_path into a shareable absolute portal_url', async () => {
    const r = await vendorsApi.generatePortalToken('vendor-zeiss');
    expect(r.portal_url).toBe(`${window.location.origin}${BACKEND_BODY.portal_path}`);
    // The link must be openable as-is when pasted into WhatsApp.
    expect(r.portal_url.startsWith('http')).toBe(true);
    expect(r.portal_url).toContain(BACKEND_BODY.token_id);
  });

  it('still carries token_id and expires_at through unchanged', async () => {
    const r = await vendorsApi.generatePortalToken('vendor-zeiss');
    expect(r.token_id).toBe(BACKEND_BODY.token_id);
    expect(r.expires_at).toBe(BACKEND_BODY.expires_at);
  });

  it('sends the validity under the key the backend reads (ttl_days)', async () => {
    await vendorsApi.generatePortalToken('vendor-zeiss', 90);
    expect(mockPost).toHaveBeenCalledWith(
      '/vendors/vendor-zeiss/portal-token',
      { ttl_days: 90 },
    );
  });
});
