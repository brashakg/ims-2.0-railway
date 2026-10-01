// ============================================================================
// IMS 2.0 - Settings · Printers (/settings/printers)
// ============================================================================
// Wave 1 split: the inline PrinterSection + its data loading moved verbatim
// out of the old SettingsPage tab container. Renders inside SettingsLayout.

/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState, useEffect } from 'react';
import { RefreshCw, Printer, Save, AlertCircle } from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api';
import {
  LABEL_WIDTH_MM,
  PRINTABLE_MM,
  getLabelOffsetMm,
  printTestLabel,
  setLabelOffsetMm,
} from '../../components/labels/unitLabel';

export function PrinterSettingsPage() {
  const [isLoading, setIsLoading] = useState(true);
  const [printerSettings, setPrinterSettings] = useState<{
    receipt_printer_name: string;
    receipt_printer_width: number;
    label_printer_name: string;
    label_size: string;
    auto_print_receipt: boolean;
    auto_print_job_card: boolean;
    copies_per_print: number;
    qz_enabled?: boolean;
    auto_print_stage_sticker?: boolean;
  } | null>(null);
  const [availablePrinters, setAvailablePrinters] = useState<Array<{ name: string; type: string; status: string }>>([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      setIsLoading(true);
      try {
        const [printerRes, availableRes] = await Promise.all([
          settingsApi.getPrinterSettings().catch(() => null),
          settingsApi.getAvailablePrinters().catch(() => ({ printers: [] })),
        ]);
        if (!alive) return;
        if (printerRes) setPrinterSettings(printerRes);
        setAvailablePrinters(availableRes.printers || []);
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
    <PrinterSection
      printerSettings={printerSettings}
      setPrinterSettings={setPrinterSettings}
      availablePrinters={availablePrinters}
    />
  );
}

function PrinterSection({
  printerSettings,
  setPrinterSettings,
  availablePrinters,
}: {
  printerSettings: any;
  setPrinterSettings: (fn: any) => void;
  availablePrinters: Array<{ name: string; type: string; status: string }>;
}) {
  const toast = useToast();

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Printer Configuration</h2>
        {/* COUNCIL RULING §3: KEEP printer settings, with an honesty note. */}
        <div className="mb-4 flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-3">
          <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>
            These preferences are saved, but the silent-print path (QZ Tray) only
            takes effect on terminals where QZ + a signing certificate are
            installed. Where it is not yet wired, labels open in a print window.
          </span>
        </div>
        <div className="space-y-4">
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Receipt Printer</label>
              <select
                value={printerSettings?.receipt_printer_name || ''}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, receipt_printer_name: e.target.value } : null)}
                className="input-field"
              >
                <option value="">Select printer...</option>
                {availablePrinters.filter(p => p.type === 'RECEIPT').map(p => (
                  <option key={p.name} value={p.name}>{p.name} ({p.status})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Receipt Width (mm)</label>
              <select
                value={printerSettings?.receipt_printer_width || 80}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, receipt_printer_width: parseInt(e.target.value) } : null)}
                className="input-field"
              >
                <option value={58}>58mm (2 inch)</option>
                <option value={80}>80mm (3 inch)</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Label Printer</label>
              <select
                value={printerSettings?.label_printer_name || ''}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, label_printer_name: e.target.value } : null)}
                className="input-field"
              >
                <option value="">Select printer...</option>
                {availablePrinters.filter(p => p.type === 'LABEL').map(p => (
                  <option key={p.name} value={p.name}>{p.name} ({p.status})</option>
                ))}
              </select>
            </div>
          </div>

          <div className="space-y-2">
            <label className="flex items-center gap-2 cursor-pointer p-2 hover:bg-gray-100 rounded">
              <input
                type="checkbox"
                checked={printerSettings?.auto_print_receipt}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, auto_print_receipt: e.target.checked } : null)}
                className="rounded border-gray-300 text-bv-red-600"
              />
              <span className="text-sm">Auto-print receipt after payment</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer p-2 hover:bg-gray-100 rounded">
              <input
                type="checkbox"
                checked={printerSettings?.auto_print_job_card}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, auto_print_job_card: e.target.checked } : null)}
                className="rounded border-gray-300 text-bv-red-600"
              />
              <span className="text-sm">Auto-print job card for workshop orders</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer p-2 hover:bg-gray-100 rounded">
              <input
                type="checkbox"
                checked={printerSettings?.auto_print_stage_sticker ?? true}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, auto_print_stage_sticker: e.target.checked } : null)}
                className="rounded border-gray-300 text-bv-red-600"
              />
              <span className="text-sm">Auto-print stage sticker when a job advances</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer p-2 hover:bg-gray-100 rounded">
              <input
                type="checkbox"
                checked={printerSettings?.qz_enabled ?? true}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, qz_enabled: e.target.checked } : null)}
                className="rounded border-gray-300 text-bv-red-600"
              />
              <span className="text-sm">
                Use QZ Tray for silent label printing
                <span className="block text-xs text-gray-500">When off (or QZ/cert not configured), labels open in a print window.</span>
              </span>
            </label>
          </div>

          <button
            onClick={async () => {
              try {
                await settingsApi.updatePrinterSettings(printerSettings || {});
                toast.success('Printer settings saved');
              } catch {
                toast.error('Failed to save settings');
              }
            }}
            className="btn-primary"
          >
            <Save className="w-4 h-4 mr-2" />
            Save Printer Settings
          </button>
        </div>
      </div>

      <UnitLabelCard />

      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Available Printers</h2>
        <div className="space-y-2">
          {availablePrinters.length === 0 ? (
            <p className="text-gray-500 text-center py-4">No printers detected on network</p>
          ) : (
            availablePrinters.map(printer => (
              <div key={printer.name} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                <div className="flex items-center gap-3">
                  <Printer className="w-5 h-5 text-gray-500" />
                  <div>
                    <p className="font-medium text-gray-900">{printer.name}</p>
                    <p className="text-xs text-gray-500">{printer.type}</p>
                  </div>
                </div>
                <span className={clsx(
                  'text-xs px-2 py-1 rounded',
                  printer.status === 'online' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                )}>
                  {printer.status}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}


/** Owner ruling 2026-09-28: stock labels on the TSC TE244, 100 x 15 mm stock
 *  with a 70 mm printable area, through the normal Windows driver. Where the
 *  printable side of the stock sits is calibrated on the real printer, so the
 *  offset is saved on THIS computer (the printer attached here) -- in browser
 *  storage, which is per web address: IMS opened at its other address starts
 *  flush left, so the card names the address it saved for.
 *  ponytail: per-browser + per-address; a server-side per-PC setting only if
 *  shops keep switching addresses. */
function UnitLabelCard() {
  const toast = useToast();
  const host = window.location.host;
  const [offset, setOffset] = useState(() => String(getLabelOffsetMm()));
  /** True when the offset was kept; when this browser blocks site data it is
   *  not, the labels print flush left, and the card says exactly that. */
  const save = () => {
    const kept = setLabelOffsetMm(Number(offset));
    setOffset(String(getLabelOffsetMm()));
    if (!kept) {
      toast.error(
        `This browser did not keep the offset (site data is blocked for ${host}), so labels print flush left. Allow site data for IMS and save again.`,
      );
    }
    return kept;
  };
  return (
    <div className="card">
      <h2 className="text-lg font-semibold text-gray-900 mb-1">Barcode labels (TSC TE244)</h2>
      <p className="text-sm text-gray-600 mb-4">
        Stock labels are {LABEL_WIDTH_MM} x 15 mm with a {PRINTABLE_MM} mm printable area and print
        through the normal Windows driver. In the TSC printer preferences set the paper to{' '}
        {LABEL_WIDTH_MM} x 15 mm; in the print dialog pick that printer with Margins: None.
      </p>
      <label htmlFor="label-offset" className="block text-sm font-medium text-gray-600 mb-1">
        Printable area starts this far from the left edge (mm)
      </label>
      <input
        id="label-offset"
        type="number"
        min={0}
        max={LABEL_WIDTH_MM - PRINTABLE_MM}
        step={0.5}
        value={offset}
        onChange={(e) => setOffset(e.target.value)}
        className="input-field max-w-[10rem]"
      />
      <p className="text-xs text-gray-500 mt-1 mb-4">
        0 = flush left. Print a test label: its box shows where the {PRINTABLE_MM} mm area lands.
        Shift it until the box sits on the printable part of the stock. Saved on this computer for{' '}
        <strong>{host}</strong> only: if this shop also opens IMS at another web address, set it there too.
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn-primary"
          onClick={() => {
            if (save()) toast.success(`Label offset saved on this computer for ${host}`);
          }}
        >
          <Save className="w-4 h-4 mr-2" />
          Save offset
        </button>
        <button
          type="button"
          className="btn-outline"
          onClick={() => {
            save();
            const r = printTestLabel();
            if (r.method !== 'html') toast.error(`${r.message} Allow pop-ups for IMS and try again.`);
          }}
        >
          <Printer className="w-4 h-4 mr-2" />
          Print a test label
        </button>
      </div>
    </div>
  );
}
