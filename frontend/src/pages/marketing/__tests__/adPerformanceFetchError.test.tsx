// ============================================================================
// Ad Performance: a failed fetch is said out loud (audit L1-F33)
// ============================================================================
// A connected Google Ads / Meta account whose report could not be fetched used
// to read as "not connected" with zero spend. The API now returns
// google_error / meta_error; the page must show it, and must NOT show the
// "connect your account" empty state for a connected channel.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { AdPerformanceResponse } from '../../../services/api/marketing';

const getAdPerformance = vi.fn();
vi.mock('../../../services/api/marketing', () => ({
  marketingApi: { getAdPerformance: (p: unknown) => getAdPerformance(p) },
}));

import { AdPerformancePage } from '../AdPerformancePage';

const BASE: AdPerformanceResponse = {
  rows: [],
  total_spend: 0,
  total_impressions: 0,
  total_clicks: 0,
  total_conversions: 0,
  blended_roas: 0,
  total_cpl: 0,
  google_configured: false,
  meta_configured: false,
  google_error: '',
  meta_error: '',
  fetched_at: '2026-11-11T00:00:00+00:00',
  note: '',
};

beforeEach(() => getAdPerformance.mockReset());

describe('Ad Performance fetch errors', () => {
  it('shows why a connected channel has no figures', async () => {
    getAdPerformance.mockResolvedValue({
      ...BASE,
      google_configured: true,
      google_error: 'Google Ads did not answer in time. Try again.',
      note: 'Google Ads did not answer in time. Try again.',
    });
    render(<AdPerformancePage />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('These figures are incomplete');
    expect(alert).toHaveTextContent('Google Ads did not answer in time. Try again.');
    // Connected, so no "connect your account" card for Google.
    expect(screen.queryByText(/No ad account credentials configured/)).toBeNull();
  });

  it('shows no alert when a connected channel simply has no campaigns', async () => {
    getAdPerformance.mockResolvedValue({ ...BASE, google_configured: true, meta_configured: true });
    render(<AdPerformancePage />);
    await screen.findByText('Total Spend');
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
