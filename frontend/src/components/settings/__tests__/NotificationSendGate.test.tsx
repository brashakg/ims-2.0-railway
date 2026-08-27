// ============================================================================
// The Sending-mode card must not print a promise it could not verify
// ============================================================================
// The card's caption -- "Nothing is sent to customers yet. Messages are logged
// only. This switch is set on the server, on purpose, so it can never be
// flipped by accident from a screen." -- is an explicit assurance to the owner
// that no message is reaching a real customer. It used to be printed off a
// `useState('off')` default with a silent `catch {}` behind it, so any 401,
// 500 or timeout on GET /settings/notifications/providers printed that
// assurance verbatim over a server that may have been DISPATCH_MODE=live.
//
// These drive the REAL component: mock only the network.

import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getNotificationProviders = vi.fn();
const getNotificationTemplates = vi.fn();

vi.mock('../../../services/api/settings', () => ({
  settingsApi: {
    getNotificationProviders: (...a: unknown[]) => getNotificationProviders(...a),
    getNotificationTemplates: (...a: unknown[]) => getNotificationTemplates(...a),
    updateNotificationTemplate: vi.fn(),
    testNotification: vi.fn(),
  },
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ hasRole: () => true, user: { id: 'u1', roles: ['ADMIN'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));

import { NotificationSettings } from '../NotificationSettings';

const ASSURANCE = /Nothing is sent to customers yet/i;
const NEVER_FLIPPED = /can never be\s+flipped by accident from a screen/i;

function renderCard() {
  return render(
    <MemoryRouter>
      <NotificationSettings />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  getNotificationTemplates.mockResolvedValue({ templates: [] });
});

describe('Sending-mode card', () => {
  it('says it could not read the gate when the request fails, and makes NO promise', async () => {
    // The exact shape the silent catch used to swallow.
    getNotificationProviders.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 500'), { status: 500 }),
    );

    renderCard();

    await waitFor(() => expect(getNotificationProviders).toHaveBeenCalled());
    expect(await screen.findByText(/couldn.t be read/i)).toBeInTheDocument();
    // The dangerous sentences must be absent entirely -- not merely reworded.
    expect(screen.queryByText(ASSURANCE)).toBeNull();
    expect(screen.queryByText(NEVER_FLIPPED)).toBeNull();
    expect(screen.getByText(/Do not assume they are not/i)).toBeInTheDocument();
  });

  it('still prints the real mode, and the assurance, when the server answers', async () => {
    getNotificationProviders.mockResolvedValue({
      whatsapp: { provider: 'MSG91', enabled: false, sender: '' },
      sms: { provider: 'MSG91', enabled: false, sender: '' },
      dispatch_mode: 'off',
    });

    renderCard();

    expect(await screen.findByText(ASSURANCE)).toBeInTheDocument();
    expect(screen.getByText('off')).toBeInTheDocument();
    expect(screen.queryByText(/couldn.t be read/i)).toBeNull();
  });

  it('reports LIVE as live -- the branch the failed read must never stand in for', async () => {
    getNotificationProviders.mockResolvedValue({
      whatsapp: { provider: 'MSG91', enabled: true, sender: '9999999999' },
      sms: { provider: 'MSG91', enabled: true, sender: 'BVOPTS' },
      dispatch_mode: 'live',
    });

    renderCard();

    expect(await screen.findByText(/Messages go to real customers/i)).toBeInTheDocument();
    expect(screen.getByText('live')).toBeInTheDocument();
    expect(screen.queryByText(ASSURANCE)).toBeNull();
  });

  it('renders a value it does not recognise as UNKNOWN, never verbatim with the assurance', async () => {
    // " live" is DISPATCH_MODE pasted into Railway with a leading space. The
    // space is invisible in HTML, so rendering it verbatim shows
    // "Sending mode: live" -- while whether it actually sends is decided by a
    // Python default this screen cannot see. Never print a promise under a
    // value the screen does not understand.
    getNotificationProviders.mockResolvedValue({
      whatsapp: { provider: 'MSG91', enabled: false, sender: '' },
      sms: { provider: 'MSG91', enabled: false, sender: '' },
      dispatch_mode: ' live',
    });

    renderCard();

    expect(await screen.findByText(/unrecognised/i)).toBeInTheDocument();
    expect(screen.queryByText('live')).toBeNull();
    expect(screen.queryByText(/Messages go to real customers/i)).toBeNull();
    expect(screen.queryByText(ASSURANCE)).toBeNull();
    expect(screen.queryByText(NEVER_FLIPPED)).toBeNull();
    expect(screen.getByText(/Do not assume they are not/i)).toBeInTheDocument();
  });

  it('renders an empty-string mode as UNKNOWN, not as a blank with a promise under it', async () => {
    // DISPATCH_MODE="" (the variable exists but is empty) used to render
    // "Sending mode:" followed by nothing, with the full assurance beneath.
    getNotificationProviders.mockResolvedValue({
      whatsapp: { provider: 'MSG91', enabled: false, sender: '' },
      sms: { provider: 'MSG91', enabled: false, sender: '' },
      dispatch_mode: '',
    });

    renderCard();

    expect(await screen.findByText(/unrecognised/i)).toBeInTheDocument();
    expect(screen.queryByText(ASSURANCE)).toBeNull();
    expect(screen.queryByText(NEVER_FLIPPED)).toBeNull();
  });

  it('renders a malformed (non-string) mode as UNKNOWN, never "[object Object]"', async () => {
    getNotificationProviders.mockResolvedValue({
      whatsapp: { provider: 'MSG91', enabled: false, sender: '' },
      sms: { provider: 'MSG91', enabled: false, sender: '' },
      dispatch_mode: { nested: 'garbage' },
    });

    renderCard();

    expect(await screen.findByText(/unrecognised/i)).toBeInTheDocument();
    expect(screen.queryByText(/object Object/i)).toBeNull();
    expect(screen.queryByText(ASSURANCE)).toBeNull();
  });

  it('says "reading" while the request is still in flight, not that the server was unreachable', () => {
    // The failed-read caption states a failure. Before the request settles,
    // no failure has happened -- the card must not claim one.
    getNotificationProviders.mockReturnValue(new Promise(() => {}));

    renderCard();

    expect(screen.getByText(/reading/i)).toBeInTheDocument();
    expect(screen.queryByText(/could not reach the server/i)).toBeNull();
    expect(screen.queryByText(/couldn.t be read/i)).toBeNull();
    expect(screen.queryByText(ASSURANCE)).toBeNull();
  });

  it('treats a missing dispatch_mode key as unread, not as off', async () => {
    // A trimmed/older server response. `?? 'off'` would have printed the
    // assurance here too, from a body that never mentioned the gate.
    getNotificationProviders.mockResolvedValue({
      whatsapp: { provider: 'MSG91', enabled: false, sender: '' },
      sms: { provider: 'MSG91', enabled: false, sender: '' },
    });

    renderCard();

    expect(await screen.findByText(/couldn.t be read/i)).toBeInTheDocument();
    expect(screen.queryByText(ASSURANCE)).toBeNull();
  });
});
