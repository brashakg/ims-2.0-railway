// ============================================================================
// Messaging preflight panel - honest rows with the owner's named next step
// ============================================================================
// The MSG91 + Coexistence preflight must (1) render every row the backend
// reports, (2) show the named next step on every not-ok row, and (3) render
// NOTHING for an older backend that has no messaging_preflight block, so the
// status card never invents readiness it was not told about.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ hasRole: () => true, user: { id: 'u1', roles: ['SUPERADMIN'] } }),
}));

import { MessagingPreflightPanel } from '../IntegrationStatusCard';
import type { IntegrationStatusReport } from '../../../services/api/integrations';

const BASE: IntegrationStatusReport = {
  generated_at: '2026-08-30T00:00:00Z',
  dispatch_mode: 'off',
  test_phone_set: false,
  summary: { total: 0, configured: 0, live: 0 },
  integrations: [],
};

describe('MessagingPreflightPanel', () => {
  it('renders each row and surfaces the next step on not-ok rows', () => {
    render(
      <MessagingPreflightPanel
        report={{
          ...BASE,
          messaging_preflight: {
            ok: false,
            rows: [
              { id: 'creds', label: 'MSG91 auth key', ok: true, detail: 'auth key present', next_step: '' },
              {
                id: 'store_numbers',
                label: 'Per-store WhatsApp numbers',
                ok: false,
                detail: '1 of 2 active stores mapped; missing: WO-PUN-01',
                next_step: 'Map each shop\'s own WhatsApp number in the MSG91 tile.',
              },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText(/Messaging preflight/i)).toBeInTheDocument();
    expect(screen.getByText('Not ready')).toBeInTheDocument();
    expect(screen.getByTestId('preflight-creds')).toBeInTheDocument();
    // The failing row names the missing store AND hands the owner a step.
    expect(screen.getByText(/missing: WO-PUN-01/i)).toBeInTheDocument();
    expect(screen.getByText(/Next step: Map each shop/i)).toBeInTheDocument();
  });

  it('marks an all-ok preflight Ready', () => {
    render(
      <MessagingPreflightPanel
        report={{
          ...BASE,
          messaging_preflight: {
            ok: true,
            rows: [
              { id: 'creds', label: 'MSG91 auth key', ok: true, detail: 'auth key present', next_step: '' },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText('Ready')).toBeInTheDocument();
    expect(screen.queryByText(/Next step:/i)).toBeNull();
  });

  it('renders nothing when the backend reports no preflight block', () => {
    const { container } = render(<MessagingPreflightPanel report={BASE} />);
    expect(container).toBeEmptyDOMElement();
  });
});
