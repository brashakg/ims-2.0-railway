// ============================================================================
// IMS 2.0 - Settings > Shopify live sync (owner ruling 2026-09-06)
// ============================================================================
// The section is a thin view over the policy engine: it must READ the three
// keys the backend scheduler reads, WRITE them back through the same door at
// GLOBAL scope, refuse an obviously bad form before the round-trip, and show
// the server's validation message when the backend says no. Fixtures use
// non-default values so a screen that rendered code defaults would fail.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastCalls: { kind: string; msg: string }[] = [];
const mockGetAll = vi.fn();
const mockSet = vi.fn();

vi.mock('../../../services/api/settings', () => ({
  policiesApi: {
    getAll: (...a: unknown[]) => mockGetAll(...a),
    set: (...a: unknown[]) => mockSet(...a),
  },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({
    success: (m: string) => toastCalls.push({ kind: 'success', msg: m }),
    error: (m: string) => toastCalls.push({ kind: 'error', msg: m }),
    info: (m: string) => toastCalls.push({ kind: 'info', msg: m }),
    warning: (m: string) => toastCalls.push({ kind: 'warning', msg: m }),
  }),
}));

vi.mock('react-router-dom', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

import { ShopifyLiveSyncSection, nextRunLabel, MAX_SLOTS } from '../SettingsShopifyLiveSync';

function policies(over: Record<string, unknown> = {}) {
  const v = {
    'shopify.live_sync.enabled': false,
    'shopify.live_sync.slots': ['02:30', '11:15'],
    'shopify.live_sync.max_products_per_run': 75,
    ...over,
  };
  return {
    scope: 'global',
    policies: Object.fromEntries(
      Object.entries(v).map(([k, value]) => [k, { key: k, value, source: 'global', scope: 'global', type: 'x' }]),
    ),
  };
}

describe('Settings > Shopify live sync', () => {
  beforeEach(() => {
    toastCalls.length = 0;
    mockGetAll.mockReset();
    mockSet.mockReset();
    mockSet.mockResolvedValue({});
  });

  it('renders the stored values, not the code defaults', async () => {
    mockGetAll.mockResolvedValue(policies());
    render(<ShopifyLiveSyncSection />);

    const toggle = await screen.findByRole('switch', { name: /scheduled sync enabled/i });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByLabelText('Sync time 1')).toHaveValue('02:30');
    expect(screen.getByLabelText('Sync time 2')).toHaveValue('11:15');
    expect(screen.getByLabelText('Max products per run')).toHaveValue(75);
    expect(mockGetAll).toHaveBeenCalledWith('global');
    // Schedule OFF -> no next-run preview.
    expect(screen.getByTestId('next-run-preview')).toHaveTextContent(/schedule is off/i);
  });

  it('saves the three keys through the policy door at GLOBAL scope', async () => {
    mockGetAll.mockResolvedValue(policies({ 'shopify.live_sync.enabled': true }));
    render(<ShopifyLiveSyncSection />);
    await screen.findByLabelText('Sync time 1');

    fireEvent.change(screen.getByLabelText('Sync time 2'), { target: { value: '09:00' } });
    fireEvent.change(screen.getByLabelText('Max products per run'), { target: { value: '120' } });
    await userEvent.click(screen.getByRole('button', { name: /save live sync settings/i }));

    await waitFor(() => expect(mockSet).toHaveBeenCalledTimes(3));
    expect(mockSet).toHaveBeenCalledWith('shopify.live_sync.enabled', true, null);
    expect(mockSet).toHaveBeenCalledWith('shopify.live_sync.slots', ['02:30', '09:00'], null);
    expect(mockSet).toHaveBeenCalledWith('shopify.live_sync.max_products_per_run', 120, null);
    expect(toastCalls.some((t) => t.kind === 'success')).toBe(true);
    // The preview follows the form (schedule ON, two valid slots).
    expect(screen.getByTestId('next-run-preview')).toHaveTextContent(/next run: (today|tomorrow)/i);
  });

  it('caps the slot list at six and never below one', async () => {
    mockGetAll.mockResolvedValue(policies({ 'shopify.live_sync.slots': ['01:00'] }));
    render(<ShopifyLiveSyncSection />);
    await screen.findByLabelText('Sync time 1');

    expect(screen.getByRole('button', { name: /remove sync time 1/i })).toBeDisabled();
    const add = screen.getByRole('button', { name: /add time/i });
    for (let i = 1; i < MAX_SLOTS; i++) await userEvent.click(add);
    expect(screen.getByLabelText(`Sync time ${MAX_SLOTS}`)).toBeInTheDocument();
    expect(add).toBeDisabled();
  });

  it('refuses to save an out-of-range cap and shows the server message on a 400', async () => {
    mockGetAll.mockResolvedValue(policies({ 'shopify.live_sync.enabled': true }));
    render(<ShopifyLiveSyncSection />);
    await screen.findByLabelText('Sync time 1');

    fireEvent.change(screen.getByLabelText('Max products per run'), { target: { value: '0' } });
    expect(screen.getByRole('button', { name: /save live sync settings/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Max products per run'), { target: { value: '10' } });

    mockSet.mockRejectedValueOnce({ response: { data: { detail: 'shopify.live_sync.slots: slot 25:00 is not a valid 24h time' } } });
    await userEvent.click(screen.getByRole('button', { name: /save live sync settings/i }));
    await waitFor(() => expect(toastCalls.some((t) => t.kind === 'error' && /25:00/.test(t.msg))).toBe(true));
  });
});

describe('nextRunLabel (IST)', () => {
  // 2026-09-07 03:30 UTC == 09:00 IST exactly -> strictly after now -> 13:00 today.
  const at0900 = new Date('2026-09-07T03:30:00Z');
  it('picks the next slot strictly after now on the IST clock and rolls over', () => {
    expect(nextRunLabel(['01:00', '09:00', '13:00'], at0900)).toBe('Today 13:00 IST');
    expect(nextRunLabel(['01:00', '09:00'], at0900)).toBe('Tomorrow 01:00 IST');
    // 2026-09-07 19:40 UTC == 01:10 IST next day -> 09:00 today (IST).
    expect(nextRunLabel(['01:00', '09:00'], new Date('2026-09-07T19:40:00Z'))).toBe('Today 09:00 IST');
    expect(nextRunLabel(['25:00'], at0900)).toBeNull();
  });
});
