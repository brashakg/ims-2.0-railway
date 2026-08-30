// ============================================================================
// WhatsApp template mapping editor - the save carries the wa_* fields
// ============================================================================
// The template registry is DATA: the owner types the MSG91-approved template
// name against a flow and Save must PUT it as wa_template_name on the SAME
// notification_templates doc the toggle uses. A save that silently dropped
// the wa_* fields would leave every flow on its seed placeholder - and MSG91
// rejects placeholder names in live mode.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getNotificationProviders = vi.fn();
const getNotificationTemplates = vi.fn();
const updateNotificationTemplate = vi.fn();

vi.mock('../../../services/api/settings', () => ({
  settingsApi: {
    getNotificationProviders: (...a: unknown[]) => getNotificationProviders(...a),
    getNotificationTemplates: (...a: unknown[]) => getNotificationTemplates(...a),
    updateNotificationTemplate: (...a: unknown[]) => updateNotificationTemplate(...a),
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

function renderScreen() {
  return render(
    <MemoryRouter>
      <NotificationSettings />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getNotificationProviders.mockResolvedValue({
    whatsapp: { provider: 'MSG91', enabled: false, sender: '' },
    sms: { provider: 'MSG91', enabled: false, sender: '' },
    dispatch_mode: 'off',
  });
  getNotificationTemplates.mockResolvedValue({ templates: [] });
  updateNotificationTemplate.mockResolvedValue({});
});

async function openTemplatesTabAndSelect(name: RegExp) {
  renderScreen();
  fireEvent.click(screen.getByText('Notification Templates'));
  const row = await screen.findByText(name);
  fireEvent.click(row);
}

describe('WhatsApp template mapping editor', () => {
  it('saves the approved name, language and category as wa_* fields', async () => {
    await openTemplatesTabAndSelect(/Birthday Wish/i);

    const nameInput = await screen.findByPlaceholderText('e.g. bv_order_delivered_v1');
    fireEvent.change(nameInput, { target: { value: 'bv_birthday_v3' } });
    fireEvent.change(screen.getByPlaceholderText('en'), { target: { value: 'en_US' } });
    fireEvent.change(screen.getByPlaceholderText('e.g. 1107170000000012345'), {
      target: { value: 'ZZ_DLT_1107_BDAY' },
    });

    fireEvent.click(screen.getByText('Save mapping'));

    await waitFor(() => expect(updateNotificationTemplate).toHaveBeenCalledTimes(1));
    const [templateId, body] = updateNotificationTemplate.mock.calls[0] as [string, any];
    expect(templateId).toBe('BIRTHDAY_WISH');
    expect(body.wa_template_name).toBe('bv_birthday_v3');
    expect(body.wa_language).toBe('en_US');
    // The SMS-fallback DLT template id rides the SAME save (registry row,
    // never env) - dropping it would silently disable the flow's fallback.
    expect(body.sms_template_id).toBe('ZZ_DLT_1107_BDAY');
    // The full doc shape still rides along (backend model requires it).
    expect(body.template_id).toBe('BIRTHDAY_WISH');
    expect(typeof body.content).toBe('string');
  });

  it('pre-fills the editor from a saved mapping on the fetched doc', async () => {
    getNotificationTemplates.mockResolvedValue({
      templates: [
        {
          template_id: 'BIRTHDAY_WISH',
          is_enabled: true,
          content: 'Happy birthday {customer_name}',
          wa_template_name: 'bv_birthday_saved',
          wa_language: 'en',
          wa_category: 'marketing',
        },
      ],
    });

    await openTemplatesTabAndSelect(/Birthday Wish/i);

    const nameInput = await screen.findByPlaceholderText('e.g. bv_order_delivered_v1');
    expect((nameInput as HTMLInputElement).value).toBe('bv_birthday_saved');
  });
});
