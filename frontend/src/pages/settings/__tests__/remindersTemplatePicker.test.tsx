// ============================================================================
// Reminders -> New rule -> Template picker (audit L1-F11)
// ============================================================================
// The picker listed only SAVED notification templates. On a fresh database
// there are none, so the required Template field offered just "Select..." and
// no reminder could be created until someone toggled a template on the
// Notifications screen. It now reads the server's effective catalog (its
// built-ins + saved templates).
//
// These drive the REAL screen and the REAL option builder; only the network
// is mocked.

import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { templatePickerOptions, NOTIFICATION_TEMPLATES } from '../../../constants/notifications';

const getNotificationTemplates = vi.fn();
const listRules = vi.fn();

vi.mock('../../../services/api', () => ({
  remindersApi: {
    list: (...a: unknown[]) => listRules(...a),
    create: vi.fn(),
    update: vi.fn(),
    toggle: vi.fn(),
    remove: vi.fn(),
    preview: vi.fn(),
    run: vi.fn(),
  },
  settingsApi: {
    getNotificationTemplates: (...a: unknown[]) => getNotificationTemplates(...a),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', roles: ['ADMIN'], activeRole: 'ADMIN' } }),
}));
vi.mock('../../../context/ToastContext', () => {
  // One stable object: the screen lists `toast` in a hook dependency array.
  const toast = { error: () => {}, success: () => {}, warning: () => {}, info: () => {} };
  return { useToast: () => toast };
});

import { RemindersSettings } from '../RemindersSettings';

// What the server answers on a database with NO saved templates: its own
// built-ins, every one sendable.
const SERVER_BUILTINS = [
  'PRESCRIPTION_EXPIRY',
  'BIRTHDAY_WISH',
  'ANNUAL_CHECKUP_REMINDER',
  'ORDER_CONFIRMED',
  'ORDER_READY',
  'ORDER_DELIVERED',
  'GOOGLE_REVIEW_REQUEST',
  'WALKOUT_RECOVERY',
  'REFERRAL_INVITE',
  'NPS_SURVEY',
];
const freshCatalog = () =>
  SERVER_BUILTINS.map((template_id) => ({
    template_id,
    builtin: true,
    saved: false,
    is_enabled: true,
    sendable: true,
  }));

async function openPicker(): Promise<HTMLSelectElement> {
  render(<RemindersSettings />);
  await waitFor(() => expect(listRules).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole('button', { name: /new rule/i }));
  const label = await screen.findByText('Template');
  return within(label.parentElement as HTMLElement).getByRole('combobox') as HTMLSelectElement;
}

const optionById = (select: HTMLSelectElement, id: string) =>
  Array.from(select.options).find((o) => o.value === id);

beforeEach(() => {
  vi.clearAllMocks();
  listRules.mockResolvedValue({ rules: [] });
});

describe('templatePickerOptions', () => {
  it('offers every server built-in on a fresh database', () => {
    const opts = templatePickerOptions({ templates: [], catalog: freshCatalog() });
    const selectable = opts.filter((o) => o.selectable).map((o) => o.id);
    expect(selectable).toEqual(SERVER_BUILTINS);
    // labelled with the name the Notifications screen uses, where it has one
    expect(opts.find((o) => o.id === 'ORDER_CONFIRMED')?.label).toBe(
      NOTIFICATION_TEMPLATES.ORDER_CONFIRMED.name,
    );
  });

  it('keeps a template the server cannot send in the list, greyed out with the reason', () => {
    const catalog = [
      ...freshCatalog().filter((c) => c.template_id !== 'BIRTHDAY_WISH'),
      { template_id: 'BIRTHDAY_WISH', builtin: true, saved: true, is_enabled: false, sendable: false },
      { template_id: 'ZZ_EMPTY', builtin: false, saved: true, is_enabled: true, sendable: false },
    ];
    const opts = templatePickerOptions({ catalog });
    const off = opts.find((o) => o.id === 'BIRTHDAY_WISH');
    expect(off?.selectable).toBe(false);
    expect(off?.label).toMatch(/turned off in Notifications/);
    const empty = opts.find((o) => o.id === 'ZZ_EMPTY');
    expect(empty?.selectable).toBe(false);
    expect(empty?.label).toMatch(/no message text saved yet/);
  });

  it('never drops a template the Notifications screen lists', () => {
    const ids = templatePickerOptions({ catalog: freshCatalog() }).map((o) => o.id);
    for (const id of Object.keys(NOTIFICATION_TEMPLATES)) expect(ids).toContain(id);
    expect(new Set(ids).size).toBe(ids.length); // and lists none twice
  });

  it('does not offer a template the server holds no wording for', () => {
    // On the Notifications list, but not a server built-in and never saved:
    // choosing it would queue the text "[Template ... not found]".
    const opt = templatePickerOptions({ catalog: freshCatalog() }).find(
      (o) => o.id === 'APPOINTMENT_REMINDER',
    );
    expect(opt?.selectable).toBe(false);
  });

  it('includes a saved template that is not a built-in', () => {
    const catalog = [
      ...freshCatalog(),
      { template_id: 'ZZ_CUSTOM', builtin: false, saved: true, is_enabled: true, sendable: true },
    ];
    const opt = templatePickerOptions({ catalog }).find((o) => o.id === 'ZZ_CUSTOM');
    expect(opt).toEqual({ id: 'ZZ_CUSTOM', label: 'ZZ_CUSTOM', selectable: true });
  });

  it('falls back to the saved rows when an older server sends no catalog', () => {
    const opts = templatePickerOptions({ templates: [{ template_id: 'ORDER_READY' }] });
    expect(opts.find((o) => o.id === 'ORDER_READY')?.selectable).toBe(true);
  });
});

describe('Reminders template picker', () => {
  it('offers the built-in templates on a fresh database (no saved templates)', async () => {
    getNotificationTemplates.mockResolvedValue({ templates: [], catalog: freshCatalog() });

    const select = await openPicker();

    await waitFor(() => expect(optionById(select, 'ORDER_CONFIRMED')).toBeTruthy());
    for (const id of SERVER_BUILTINS) {
      const opt = optionById(select, id);
      expect(opt, id).toBeTruthy();
      expect(opt?.disabled, id).toBe(false);
    }
    // ...and one can actually be chosen.
    fireEvent.change(select, { target: { value: 'BIRTHDAY_WISH' } });
    expect(select.value).toBe('BIRTHDAY_WISH');
  });

  it('greys out a switched-off template instead of hiding it', async () => {
    getNotificationTemplates.mockResolvedValue({
      templates: [],
      catalog: [
        { template_id: 'BIRTHDAY_WISH', builtin: true, saved: true, is_enabled: false, sendable: false },
      ],
    });

    const select = await openPicker();

    await waitFor(() => expect(optionById(select, 'BIRTHDAY_WISH')).toBeTruthy());
    expect(optionById(select, 'BIRTHDAY_WISH')?.disabled).toBe(true);
  });

  it('says so when the template list could not be loaded, and retries', async () => {
    getNotificationTemplates.mockRejectedValueOnce(new Error('500'));
    getNotificationTemplates.mockResolvedValue({ templates: [], catalog: freshCatalog() });

    const select = await openPicker();

    expect(await screen.findByText(/Could not load the template list/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    await waitFor(() => expect(optionById(select, 'ORDER_CONFIRMED')).toBeTruthy());
    expect(screen.queryByText(/Could not load the template list/i)).toBeNull();
  });
});
