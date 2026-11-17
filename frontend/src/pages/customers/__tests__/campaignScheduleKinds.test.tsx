// ============================================================================
// Campaign Manager offers only schedules that actually run (audit L1-F28)
// ============================================================================
// The builder offered "Recurring" (Daily / Weekly / Monthly) and "Triggered"
// and the list showed such a campaign as Scheduled, but nothing on the server
// ever runs a recurring or triggered campaign. The builder now offers only
// what runs, and a campaign stored earlier with such a schedule says plainly
// that it does not run by itself.
//
// Drives the REAL screen; only the network is mocked.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const list = vi.fn();

vi.mock('../../../services/api/marketing', () => ({
  campaignsApi: {
    list: (...a: unknown[]) => list(...a),
    listSegments: vi.fn().mockResolvedValue({ segments: [] }),
    previewSegment: vi.fn().mockResolvedValue({ count: 0 }),
    create: vi.fn(),
    schedule: vi.fn(),
    send: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    duplicate: vi.fn(),
    remove: vi.fn(),
    analytics: vi.fn(),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', roles: ['ADMIN'], activeStoreId: 'BV-PUN-01' }, hasRole: () => true }),
}));
vi.mock('../../../context/ToastContext', () => {
  // One stable object: the screen lists `toast` in a hook dependency array.
  const toast = { error: () => {}, success: () => {}, warning: () => {}, info: () => {} };
  return { useToast: () => toast };
});

import { CampaignManager } from '../CampaignManager';

const campaign = (over: Record<string, unknown>) => ({
  campaign_id: 'CMP-1',
  name: 'Legacy weekly',
  type: 'custom',
  segment_key: 'by_store',
  channels: ['WHATSAPP'],
  template_id: 'BIRTHDAY_WISH',
  status: 'SCHEDULED',
  audience_count: 0,
  sent_count: 0,
  failed_count: 0,
  skipped_count: 0,
  opened_count: 0,
  converted_count: 0,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  list.mockResolvedValue({ campaigns: [], summary: null });
});

describe('Campaign builder schedule', () => {
  it('offers only "no schedule" and "one-time"', async () => {
    render(<CampaignManager />);
    await waitFor(() => expect(list).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Campaign Builder' }));

    const select = (await screen.findByTitle('Schedule kind')) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(['NONE', 'ONE_TIME']);
    expect(screen.getByText(/Repeating and event-triggered campaigns are not available yet/i)).toBeInTheDocument();
  });
});

describe('Campaign list', () => {
  it('tells the truth about a stored repeating schedule', async () => {
    list.mockResolvedValue({
      campaigns: [campaign({ schedule: { kind: 'RECURRING', frequency: 'WEEKLY' } })],
      summary: null,
    });
    render(<CampaignManager />);

    expect(await screen.findByText(/does not run by itself/i)).toBeInTheDocument();
    expect(screen.getByText(/repeating schedule/i)).toBeInTheDocument();
  });

  it('says nothing extra about a one-time schedule', async () => {
    list.mockResolvedValue({
      campaigns: [campaign({ name: 'Diwali', schedule: { kind: 'ONE_TIME', send_at: '2026-11-20T04:30:00Z' } })],
      summary: null,
    });
    render(<CampaignManager />);

    expect(await screen.findByText('Diwali')).toBeInTheDocument();
    expect(screen.queryByText(/does not run by itself/i)).toBeNull();
  });
});
