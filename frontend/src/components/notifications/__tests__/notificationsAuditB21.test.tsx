// Audit b21 - notifications read honestly.
//   R06: the bell reads a zone-less server instant as UTC (as the full page
//        does) - a notification made 2 minutes ago is "2m ago", not "5h ago"
//        on an IST browser.
//   R40: a failed list read is "couldn't load" with a Retry on all three
//        surfaces - never "No notifications" / "You're all caught up" / "0 unread".
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach, beforeAll, afterAll } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../services/api/notifications', () => ({
  MAX_SNOOZE: 3,
  notificationsApi: {
    list: vi.fn(),
    unreadCount: vi.fn(async () => ({ unread_count: 1 })),
    markRead: vi.fn(),
    snooze: vi.fn(),
  },
}));
const toast = vi.hoisted(() => ({ success: () => {}, error: () => {}, info: () => {}, warning: () => {} }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

import { notificationsApi } from '../../../services/api/notifications';
import { NotificationBell } from '../../shell/NotificationBell';
import { DashboardNotifications } from '../DashboardNotifications';
import { NotificationsPage } from '../../../pages/notifications/NotificationsPage';

const list = notificationsApi.list as unknown as ReturnType<typeof vi.fn>;
const count = notificationsApi.unreadCount as unknown as ReturnType<typeof vi.fn>;
const down = async () => { throw new Error('503'); };

// A UTC instant written the way Mongo hands it back: no zone suffix.
function naiveUtcMinutesAgo(m: number): string {
  return new Date(Date.now() - m * 60_000).toISOString().replace('Z', '');
}
const item = (created_at: string) => ({
  notification_id: 'N1', title: 'Approval required', message: 'x', status: 'PENDING',
  priority: 'HIGH', created_at,
});

let tz: string | undefined;
beforeAll(() => { tz = process.env.TZ; process.env.TZ = 'Asia/Kolkata'; });
afterAll(() => { process.env.TZ = tz; });

const wrap = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('notifications (audit b21)', () => {
  // Each test sets its own implementation. (mockReset/mockClear here made
  // vitest 4 report the deliberately rejected read as unhandled.)
  afterEach(() => cleanup());

  it('R06: bell shows a 2-minute-old zone-less instant as 2m ago on an IST clock', async () => {
    list.mockResolvedValue({ notifications: [item(naiveUtcMinutesAgo(2))], unread_count: 1, total: 1 });
    wrap(<NotificationBell />);
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }));
    expect(await screen.findByText('2m ago')).toBeTruthy();
  });

  it('R40: bell list failure says it could not load, never "No notifications"', async () => {
    list.mockImplementation(down);
    wrap(<NotificationBell />);
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }));
    expect(await screen.findByText(/Couldn't load notifications/)).toBeTruthy();
    expect(screen.queryByText('No notifications')).toBeNull();
    // the last known count is kept, not zeroed
    await waitFor(() => expect(screen.getByLabelText('1 unread')).toBeTruthy());
  });

  it('R40: bell count failure shows "unavailable", never a clear bell; a good read clears it', async () => {
    count.mockImplementation(down);
    wrap(<NotificationBell />);
    expect(await screen.findByLabelText('Unread count unavailable')).toBeTruthy();
    expect(screen.queryByLabelText(/unread$/)).toBeNull();
    list.mockResolvedValue({ notifications: [item(naiveUtcMinutesAgo(1))], unread_count: 1, total: 1 });
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }));
    await waitFor(() => expect(screen.getByLabelText('1 unread')).toBeTruthy());
    expect(screen.queryByLabelText('Unread count unavailable')).toBeNull();
    count.mockImplementation(async () => ({ unread_count: 1 }));
  });

  it('R40: Hub widget failure is not "all caught up", and Retry recovers', async () => {
    list.mockImplementation(down);
    wrap(<DashboardNotifications />);
    expect(await screen.findByText(/Couldn't load notifications/)).toBeTruthy();
    expect(screen.queryByText(/all caught up/)).toBeNull();
    list.mockResolvedValue({ notifications: [item(naiveUtcMinutesAgo(1))], unread_count: 1, total: 1 });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Approval required')).toBeTruthy();
    expect(screen.queryByText(/Couldn't load/)).toBeNull();
  });

  it('R40: full page failure is not "0 unread" / "No notifications yet"', async () => {
    list.mockImplementation(down);
    wrap(<NotificationsPage />);
    expect(await screen.findByText(/Couldn't load notifications/)).toBeTruthy();
    expect(screen.queryByText('No notifications yet')).toBeNull();
    expect(screen.queryByText(/^0 unread/)).toBeNull();
    expect(screen.getByText(/Unread count unavailable/)).toBeTruthy();
  });
});
