// ============================================================================
// IMS 2.0 - Settings · Tax & Invoice (/settings/tax-invoice)
// ============================================================================
// Wave 1 split: the inline TaxInvoiceSection + its data loading moved verbatim
// out of the old SettingsPage tab container. Renders inside SettingsLayout.

/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState, useEffect } from 'react';
import { RefreshCw, ToggleLeft, ToggleRight, Save } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api';

export function TaxInvoiceSettingsPage() {
  const [isLoading, setIsLoading] = useState(true);
  const [taxSettings, setTaxSettings] = useState<{
    gst_enabled: boolean;
    company_gstin: string;
    default_gst_rate: number;
    hsn_validation: boolean;
    e_invoice_enabled: boolean;
    e_way_bill_enabled: boolean;
    e_way_bill_threshold: number;
  } | null>(null);
  const [invoiceSettings, setInvoiceSettings] = useState<{
    invoice_prefix: string;
    current_invoice_number: number;
    financial_year: string;
    show_logo_on_invoice: boolean;
    show_terms_on_invoice: boolean;
    default_terms: string;
    default_warranty_days: number;
    show_qr_code: boolean;
  } | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      setIsLoading(true);
      try {
        const [taxRes, invoiceRes] = await Promise.all([
          settingsApi.getTaxSettings().catch(() => null),
          settingsApi.getInvoiceSettings().catch(() => null),
        ]);
        if (!alive) return;
        if (taxRes) setTaxSettings(taxRes);
        if (invoiceRes) setInvoiceSettings(invoiceRes);
      } finally {
        if (alive) setIsLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-48">
        <RefreshCw className="w-8 h-8 animate-spin" style={{ color: 'var(--bv)' }} />
      </div>
    );
  }
  return (
    <TaxInvoiceSection
      taxSettings={taxSettings}
      setTaxSettings={setTaxSettings}
      invoiceSettings={invoiceSettings}
      setInvoiceSettings={setInvoiceSettings}
    />
  );
}

function TaxInvoiceSection({
  taxSettings,
  setTaxSettings,
  invoiceSettings,
  setInvoiceSettings,
}: {
  taxSettings: any;
  setTaxSettings: (fn: any) => void;
  invoiceSettings: any;
  setInvoiceSettings: (fn: any) => void;
}) {
  const toast = useToast();

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
            {taxSettings?.gst_enabled ? (
              <ToggleRight className="w-8 h-8 text-green-600 cursor-pointer" onClick={() => setTaxSettings((prev: any) => prev ? { ...prev, gst_enabled: false } : null)} />
            ) : (
              <ToggleLeft className="w-8 h-8 text-gray-500 cursor-pointer" onClick={() => setTaxSettings((prev: any) => prev ? { ...prev, gst_enabled: true } : null)} />
            )}
          </div>
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Company GSTIN</label>
              <input
                type="text"
                value={taxSettings?.company_gstin || ''}
                onChange={e => setTaxSettings((prev: any) => prev ? { ...prev, company_gstin: e.target.value.toUpperCase() } : null)}
                placeholder="19ABCDE1234F1Z5"
                className="input-field font-mono"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Default GST Rate (%)</label>
              <input
                type="number"
                value={taxSettings?.default_gst_rate || 18}
                onChange={e => setTaxSettings((prev: any) => prev ? { ...prev, default_gst_rate: parseFloat(e.target.value) } : null)}
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
            <label className="block text-sm font-medium text-gray-600 mb-1">Default Terms & Conditions</label>
            <textarea
              value={invoiceSettings?.default_terms || ''}
              onChange={e => setInvoiceSettings((prev: any) => prev ? { ...prev, default_terms: e.target.value } : null)}
              rows={3}
              className="input-field"
            />
          </div>
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Default Warranty (days)</label>
              <input
                type="number"
                value={invoiceSettings?.default_warranty_days || 365}
                onChange={e => setInvoiceSettings((prev: any) => prev ? { ...prev, default_warranty_days: parseInt(e.target.value) } : null)}
                className="input-field"
              />
            </div>
          </div>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={invoiceSettings?.show_logo_on_invoice ?? false}
                onChange={e => setInvoiceSettings((prev: any) => prev ? { ...prev, show_logo_on_invoice: e.target.checked } : null)}
                className="rounded border-gray-300"
              />
              <span className="text-sm">Show logo on invoice</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={invoiceSettings?.show_qr_code ?? false}
                onChange={e => setInvoiceSettings((prev: any) => prev ? { ...prev, show_qr_code: e.target.checked } : null)}
                className="rounded border-gray-300"
              />
              <span className="text-sm">Show QR code</span>
            </label>
          </div>
          <button
            onClick={async () => {
              try {
                await Promise.all([
                  settingsApi.updateTaxSettings(taxSettings || {}),
                  settingsApi.updateInvoiceSettings(invoiceSettings || {}),
                ]);
                toast.success('Settings saved');
              } catch {
                toast.error('Failed to save settings');
              }
            }}
            className="btn-primary"
          >
            <Save className="w-4 h-4 mr-2" />
            Save Tax & Invoice Settings
          </button>
        </div>
      </div>
    </div>
  );
}

