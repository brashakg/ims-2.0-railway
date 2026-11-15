// ============================================================================
// Settings forms: a failed read never lets Save overwrite the stored record
// (audit L1-F05) and a real zero is shown as zero (audit L1-F06)
// ============================================================================
// The audit saved GST rate 5 / warranty 90 / custom terms, failed only the two
// GETs, pressed Save, and the page sent {} to both APIs: the server wrote its
// defaults (18 / 365 / default terms). Printers did the same (58 -> 80, stage
// auto-print off -> on). Tax & Invoice, Printers and Business Profile now read
// through useLoadedSettings: a failed read shows an error with Retry and no
// form at all, so there is nothing to save until the stored values are known.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { unlabelledControls } from '../../../test/unlabelledControls';

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'me', name: 'Owner', roles: ['SUPERADMIN'] }, hasRole: () => true }),
}));
vi.mock('../../../services/api/auth', () => ({ authApi: {} }));
vi.mock('../../../components/labels/unitLabel', () => ({
  LABEL_WIDTH_MM: 100,
  PRINTABLE_MM: 70,
  getLabelOffsetMm: () => 0,
  setLabelOffsetMm: () => true,
  printTestLabel: () => ({ method: 'html', message: '' }),
}));

const api = {
  getTaxSettings: vi.fn(),
  getInvoiceSettings: vi.fn(),
  updateTaxSettings: vi.fn(),
  updateInvoiceSettings: vi.fn(),
  getPrinterSettings: vi.fn(),
  getAvailablePrinters: vi.fn(),
  updatePrinterSettings: vi.fn(),
  getBusinessSettings: vi.fn(),
  updateBusinessSettings: vi.fn(),
  getLogoObjectUrl: vi.fn(),
  uploadLogo: vi.fn(),
};
vi.mock('../../../services/api', () => ({
  settingsApi: new Proxy({}, { get: (_t, k: string) => (...a: unknown[]) => (api as Record<string, (...x: unknown[]) => unknown>)[k](...a) }),
}));

import { TaxInvoiceSettingsPage } from '../SettingsTaxInvoice';
import { PrinterSettingsPage } from '../SettingsPrinters';
import { BusinessSection } from '../SettingsProfile';

const TAX = { gst_enabled: true, company_gstin: '20ABCDE1234F1Z5', default_gst_rate: 5, hsn_validation: true };
const INVOICE = {
  invoice_prefix: 'BV',
  show_logo_on_invoice: false,
  default_terms: 'AUDIT LANE1 SAVED TERMS',
  default_warranty_days: 90,
  show_qr_code: false,
};
const PRINTER = {
  receipt_printer_name: null,
  receipt_printer_width: 58,
  label_printer_name: null,
  label_size: '50x25',
  auto_print_receipt: false,
  auto_print_job_card: true,
  copies_per_print: 2,
  qz_enabled: true,
  auto_print_stage_sticker: false,
};
const BUSINESS = {
  company_name: 'Saved Optics Pvt Ltd',
  company_short_name: 'SO',
  tagline: 'Saved tagline',
  logo_url: '',
  primary_color: '#112233',
  secondary_color: '#445566',
  support_email: 'owner@example.com',
  support_phone: '+91 90000 00000',
  website: 'https://example.com',
  address: '12 Saved Road',
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getTaxSettings.mockResolvedValue({ ...TAX });
  api.getInvoiceSettings.mockResolvedValue({ ...INVOICE });
  api.updateTaxSettings.mockResolvedValue({});
  api.updateInvoiceSettings.mockResolvedValue({});
  api.getPrinterSettings.mockResolvedValue({ ...PRINTER });
  api.getAvailablePrinters.mockResolvedValue({ printers: [] });
  api.updatePrinterSettings.mockResolvedValue({});
  api.getBusinessSettings.mockResolvedValue({ ...BUSINESS });
  api.updateBusinessSettings.mockResolvedValue({});
});

describe('Tax & Invoice (L1-F05, L1-F06)', () => {
  it.each([
    ['the tax read fails', () => api.getTaxSettings.mockRejectedValue(new Error('500'))],
    ['the invoice read fails', () => api.getInvoiceSettings.mockRejectedValue(new Error('500'))],
    ['both reads fail', () => {
      api.getTaxSettings.mockRejectedValue(new Error('500'));
      api.getInvoiceSettings.mockRejectedValue(new Error('500'));
    }],
  ])('offers no form and no Save when %s', async (_name, arrange) => {
    arrange();
    render(<TaxInvoiceSettingsPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load the saved tax and invoice settings/i);
    expect(screen.queryByRole('button', { name: /save tax/i })).toBeNull();
    expect(screen.queryByLabelText('Default GST Rate (%)')).toBeNull();
    expect(api.updateTaxSettings).not.toHaveBeenCalled();
    expect(api.updateInvoiceSettings).not.toHaveBeenCalled();
  });

  it('Retry loads the stored values, and Save then sends them (never {})', async () => {
    api.getTaxSettings.mockRejectedValueOnce(new Error('500'));
    render(<TaxInvoiceSettingsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));

    expect(await screen.findByLabelText('Default GST Rate (%)')).toHaveValue(5);
    expect(screen.getByLabelText('Default Warranty (days)')).toHaveValue(90);
    expect(screen.getByLabelText('Default Terms & Conditions')).toHaveValue('AUDIT LANE1 SAVED TERMS');

    fireEvent.click(screen.getByRole('button', { name: /save tax & invoice settings/i }));
    await waitFor(() => expect(api.updateInvoiceSettings).toHaveBeenCalledTimes(1));
    expect(api.updateTaxSettings).toHaveBeenCalledWith(expect.objectContaining({ default_gst_rate: 5, company_gstin: '20ABCDE1234F1Z5' }));
    expect(api.updateInvoiceSettings).toHaveBeenCalledWith(
      expect.objectContaining({ default_warranty_days: 90, default_terms: 'AUDIT LANE1 SAVED TERMS' }),
    );
    expect(toast.success).toHaveBeenCalledWith('Settings saved');
  });

  it('shows a zero GST rate and a zero warranty as 0, and saves them as 0', async () => {
    render(<TaxInvoiceSettingsPage />);
    const rate = await screen.findByLabelText('Default GST Rate (%)');
    const days = screen.getByLabelText('Default Warranty (days)');
    fireEvent.change(rate, { target: { value: '0' } });
    fireEvent.change(days, { target: { value: '0' } });
    expect(rate).toHaveValue(0); // was 18
    expect(days).toHaveValue(0); // was 365

    fireEvent.click(screen.getByRole('button', { name: /save tax & invoice settings/i }));
    await waitFor(() => expect(api.updateInvoiceSettings).toHaveBeenCalled());
    expect(api.updateTaxSettings.mock.calls[0][0].default_gst_rate).toBe(0);
    expect(api.updateInvoiceSettings.mock.calls[0][0].default_warranty_days).toBe(0);
  });

  it('a stored zero is shown as zero on load', async () => {
    api.getTaxSettings.mockResolvedValue({ ...TAX, default_gst_rate: 0 });
    api.getInvoiceSettings.mockResolvedValue({ ...INVOICE, default_warranty_days: 0 });
    render(<TaxInvoiceSettingsPage />);
    expect(await screen.findByLabelText('Default GST Rate (%)')).toHaveValue(0);
    expect(screen.getByLabelText('Default Warranty (days)')).toHaveValue(0);
  });

  it('refuses a blank or out-of-range number instead of saving a default', async () => {
    render(<TaxInvoiceSettingsPage />);
    const rate = await screen.findByLabelText('Default GST Rate (%)');
    const save = screen.getByRole('button', { name: /save tax & invoice settings/i });

    fireEvent.change(rate, { target: { value: '' } });
    expect(rate).toHaveValue(null); // stays blank while retyping, no 18 snaps back
    fireEvent.click(save);
    fireEvent.change(rate, { target: { value: '-1' } });
    fireEvent.click(save);
    fireEvent.change(rate, { target: { value: '12' } });
    fireEvent.change(screen.getByLabelText('Default Warranty (days)'), { target: { value: '4000' } });
    fireEvent.click(save);

    expect(toast.error).toHaveBeenCalledTimes(3);
    expect(api.updateTaxSettings).not.toHaveBeenCalled();
    expect(api.updateInvoiceSettings).not.toHaveBeenCalled();
  });

  it('says which half was not saved when one of the two writes fails, and keeps the entries', async () => {
    api.updateInvoiceSettings.mockRejectedValue(new Error('server said no'));
    render(<TaxInvoiceSettingsPage />);
    const terms = await screen.findByLabelText('Default Terms & Conditions');
    fireEvent.change(terms, { target: { value: 'NEW TERMS' } });
    fireEvent.click(screen.getByRole('button', { name: /save tax & invoice settings/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0][0]).toMatch(/tax settings were saved, but the invoice settings were not/i);
    expect(toast.success).not.toHaveBeenCalled();
    expect(terms).toHaveValue('NEW TERMS');
  });

  it('the GST switch is a real switch and every field is tied to its label', async () => {
    render(<TaxInvoiceSettingsPage />);
    const sw = await screen.findByRole('switch', { name: 'GST Enabled' });
    expect(sw).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(sw);
    expect(sw).toHaveAttribute('aria-checked', 'false');
    expect(unlabelledControls()).toEqual([]);
  });
});

describe('Printers (L1-F05)', () => {
  it('offers no printer form and no Save when the read fails', async () => {
    api.getPrinterSettings.mockRejectedValue(new Error('500'));
    render(<PrinterSettingsPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load the saved printer settings/i);
    expect(screen.queryByRole('button', { name: /save printer settings/i })).toBeNull();
    expect(api.updatePrinterSettings).not.toHaveBeenCalled();
    // The label offset is kept on this computer, so it stays usable.
    expect(screen.getByRole('button', { name: /save offset/i })).toBeInTheDocument();
  });

  it('shows the stored width and switches and saves them back unchanged', async () => {
    render(<PrinterSettingsPage />);
    expect(await screen.findByLabelText('Receipt Width (mm)')).toHaveValue('58');
    expect(screen.getByRole('checkbox', { name: /auto-print stage sticker/i })).not.toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: /save printer settings/i }));
    await waitFor(() => expect(api.updatePrinterSettings).toHaveBeenCalledTimes(1));
    expect(api.updatePrinterSettings).toHaveBeenCalledWith(
      expect.objectContaining({ receipt_printer_width: 58, auto_print_stage_sticker: false, auto_print_receipt: false }),
    );
    expect(unlabelledControls()).toEqual([]);
  });

  it('a failed printer-detection read is only an empty list, not a load failure', async () => {
    api.getAvailablePrinters.mockRejectedValue(new Error('500'));
    render(<PrinterSettingsPage />);
    expect(await screen.findByRole('button', { name: /save printer settings/i })).toBeEnabled();
    expect(screen.getByText(/no printers detected/i)).toBeInTheDocument();
  });
});

describe('Business Profile (same rule)', () => {
  it('offers no form and no Save when the read fails, then loads on Retry', async () => {
    api.getBusinessSettings.mockRejectedValueOnce(new Error('500'));
    render(<BusinessSection />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load the saved company profile/i);
    expect(screen.queryByRole('button', { name: /save settings/i })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByLabelText('Company Name')).toHaveValue('Saved Optics Pvt Ltd');
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));
    await waitFor(() => expect(api.updateBusinessSettings).toHaveBeenCalledTimes(1));
    expect(api.updateBusinessSettings).toHaveBeenCalledWith(expect.objectContaining({ company_name: 'Saved Optics Pvt Ltd' }));
    expect(unlabelledControls()).toEqual([]);
  });
});
