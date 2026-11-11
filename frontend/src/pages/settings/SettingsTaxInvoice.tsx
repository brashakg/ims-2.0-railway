// ============================================================================
// IMS 2.0 - Settings · Tax & Invoice (/settings/tax-invoice)
// ============================================================================
// Wave 1 split: the inline TaxInvoiceSection + its data loading moved verbatim
// out of the old SettingsPage tab container. Renders inside SettingsLayout.

import { useState } from 'react';
import { RefreshCw, Save } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api';
import { useLoadedSettings, settingsLoadErrorText } from '../../hooks/useLoadedSettings';
import { SettingsLoadError } from '../../components/settings/SettingsLoadError';

// A number box holds '' while it is being retyped (a number input fed NaN or
// a fallback default fights the typing); Save refuses '' with a reason.
type NumBox = number | '';
const numBox = (raw: string): NumBox => (raw.trim() === '' || Number.isNaN(Number(raw)) ? '' : Number(raw));

interface TaxForm {
  gst_enabled: boolean;
  company_gstin: string;
  default_gst_rate: NumBox;
}
interface InvoiceForm {
  show_logo_on_invoice: boolean;
  default_terms: string;
  default_warranty_days: NumBox;
  show_qr_code: boolean;
}

export function TaxInvoiceSettingsPage() {
  // Audit L1-F05: BOTH records must be read before anything can be edited or
  // saved. A failed read used to leave an empty form whose Save sent {} and
  // the server wrote its defaults over the stored GST rate, terms and warranty.
  const { data, status, reload, update } = useLoadedSettings(async () => {
    const [tax, invoice] = await Promise.all([settingsApi.getTaxSettings(), settingsApi.getInvoiceSettings()]);
    if (!tax || !invoice) throw new Error('empty settings answer');
    return { tax: tax as TaxForm, invoice: invoice as InvoiceForm };
  });

  if (status === 'loading') {
    return (
      <div className="flex items-center justify-center h-48">
        <RefreshCw className="w-8 h-8 animate-spin" style={{ color: 'var(--bv)' }} />
      </div>
    );
  }
  if (status === 'error' || !data) {
    return <SettingsLoadError message={settingsLoadErrorText('tax and invoice settings')} onRetry={() => void reload()} />;
  }
  return (
    <TaxInvoiceSection
      taxSettings={data.tax}
      setTaxSettings={(fn) => update((d) => ({ ...d, tax: fn(d.tax) }))}
      invoiceSettings={data.invoice}
      setInvoiceSettings={(fn) => update((d) => ({ ...d, invoice: fn(d.invoice) }))}
    />
  );
}

function TaxInvoiceSection({
  taxSettings,
  setTaxSettings,
  invoiceSettings,
  setInvoiceSettings,
}: {
  taxSettings: TaxForm;
  setTaxSettings: (fn: (prev: TaxForm) => TaxForm) => void;
  invoiceSettings: InvoiceForm;
  setInvoiceSettings: (fn: (prev: InvoiceForm) => InvoiceForm) => void;
}) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const rate = taxSettings.default_gst_rate;
    const days = invoiceSettings.default_warranty_days;
    // 0 is a real value for both (the server accepts it); blank is not.
    if (rate === '' || rate < 0 || rate > 100) {
      toast.error('Enter a default GST rate from 0 to 100');
      return;
    }
    if (days === '' || !Number.isInteger(days) || days < 0 || days > 3650) {
      toast.error('Enter the default warranty as whole days from 0 to 3650');
      return;
    }
    setSaving(true);
    try {
      // Two records, two writes: report each half honestly.
      const [tax, invoice] = await Promise.allSettled([
        settingsApi.updateTaxSettings({ ...taxSettings, default_gst_rate: rate }),
        settingsApi.updateInvoiceSettings({ ...invoiceSettings, default_warranty_days: days }),
      ]);
      const why = (r: PromiseSettledResult<unknown>) =>
        r.status === 'rejected' && r.reason instanceof Error && r.reason.message ? ` (${r.reason.message})` : '';
      if (tax.status === 'fulfilled' && invoice.status === 'fulfilled') {
        toast.success('Settings saved');
      } else if (tax.status === 'rejected' && invoice.status === 'rejected') {
        toast.error(`Failed to save settings${why(tax)}`);
      } else if (tax.status === 'rejected') {
        toast.error(`Invoice settings were saved, but the tax settings were NOT${why(tax)}. Your entries are still here - press Save again.`);
      } else {
        toast.error(`Tax settings were saved, but the invoice settings were NOT${why(invoice)}. Your entries are still here - press Save again.`);
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Tax Settings</h2>
        <div className="space-y-4">
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
            <div>
              <p className="font-medium text-gray-900">GST Enabled</p>
              <p className="text-sm text-gray-500">Apply GST to all transactions</p>
            </div>
            {/* A real switch (keyboard + screen reader), not a clickable icon. */}
            <button
              type="button"
              role="switch"
              aria-checked={!!taxSettings.gst_enabled}
              aria-label="GST Enabled"
              onClick={() => setTaxSettings((prev) => ({ ...prev, gst_enabled: !prev.gst_enabled }))}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                taxSettings.gst_enabled ? 'bg-green-600' : 'bg-gray-300'
              }`}
            >
              <span
                className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                  taxSettings.gst_enabled ? 'translate-x-5' : 'translate-x-0.5'
                }`}
              />
            </button>
          </div>
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label htmlFor="tax-company-gstin" className="block text-sm font-medium text-gray-600 mb-1">Company GSTIN</label>
              <input
                id="tax-company-gstin"
                type="text"
                value={taxSettings.company_gstin || ''}
                onChange={e => setTaxSettings((prev) => ({ ...prev, company_gstin: e.target.value.toUpperCase() }))}
                placeholder="19ABCDE1234F1Z5"
                className="input-field font-mono"
              />
            </div>
            <div>
              <label htmlFor="tax-default-gst-rate" className="block text-sm font-medium text-gray-600 mb-1">Default GST Rate (%)</label>
              <input
                id="tax-default-gst-rate"
                type="number"
                min={0}
                max={100}
                value={taxSettings.default_gst_rate ?? ''}
                onChange={e => setTaxSettings((prev) => ({ ...prev, default_gst_rate: numBox(e.target.value) }))}
                className="input-field"
              />
            </div>
          </div>
          {/* COUNCIL RULING §3: HIDE until wired. E-Invoice (IRN / digital
              signature), E-Way Bill auto-generate, and HSN-validation are inert
              toggles — a control that does nothing is a false-security lie. They
              are intentionally not rendered until the integration is actually
              wired. (The underlying fields stay in the settings model so no data
              is lost; nothing reads them yet.) */}
        </div>
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Invoice Settings</h2>
        <div className="space-y-4">
          {/* COUNCIL RULING §3: HIDE until wired. Invoice-numbering config
              (prefix / current number / financial year) is NOT GST-compliant
              yet (the serial generator is a separate, sign-off-gated change) and
              editing it here does not change the numbers the system actually
              issues. Hidden so it can't read as a working control. */}
          <div>
            <label htmlFor="invoice-default-terms" className="block text-sm font-medium text-gray-600 mb-1">Default Terms & Conditions</label>
            <textarea
              id="invoice-default-terms"
              value={invoiceSettings.default_terms || ''}
              onChange={e => setInvoiceSettings((prev) => ({ ...prev, default_terms: e.target.value }))}
              rows={3}
              className="input-field"
            />
          </div>
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label htmlFor="invoice-default-warranty" className="block text-sm font-medium text-gray-600 mb-1">Default Warranty (days)</label>
              <input
                id="invoice-default-warranty"
                type="number"
                min={0}
                max={3650}
                step={1}
                value={invoiceSettings.default_warranty_days ?? ''}
                onChange={e => setInvoiceSettings((prev) => ({ ...prev, default_warranty_days: numBox(e.target.value) }))}
                className="input-field"
              />
            </div>
          </div>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={invoiceSettings.show_logo_on_invoice ?? false}
                onChange={e => setInvoiceSettings((prev) => ({ ...prev, show_logo_on_invoice: e.target.checked }))}
                className="rounded border-gray-300"
              />
              <span className="text-sm">Show logo on invoice</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={invoiceSettings.show_qr_code ?? false}
                onChange={e => setInvoiceSettings((prev) => ({ ...prev, show_qr_code: e.target.checked }))}
                className="rounded border-gray-300"
              />
              <span className="text-sm">Show QR code</span>
            </label>
          </div>
          <button type="button" onClick={save} disabled={saving} className="btn-primary">
            <Save className="w-4 h-4 mr-2" />
            {saving ? 'Saving…' : 'Save Tax & Invoice Settings'}
          </button>
        </div>
      </div>
    </div>
  );
}
