// ============================================================================
// Settings > Printers: the label offset + "print a test label" (owner 09-28)
// ============================================================================
// The printable side of the 100 x 15 mm stock is found on the real printer:
// the shop saves an offset (mm) and prints a test label whose box shows where
// the 70 mm window lands. The test label must use the offset just typed.

(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const printMock = vi.hoisted(() => vi.fn(() => ({ method: 'html', message: '' })));
vi.mock('../../../services/printWindow', () => ({ printHtmlFallback: printMock }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../services/api', () => ({
  settingsApi: {
    getPrinterSettings: vi.fn().mockResolvedValue({ receipt_printer_width: 80, label_size: '50x25' }),
    getAvailablePrinters: vi.fn().mockResolvedValue({ printers: [] }),
    updatePrinterSettings: vi.fn(),
  },
}));

import { PrinterSettingsPage } from '../SettingsPrinters';
import { getLabelOffsetMm } from '../../../components/labels/unitLabel';

describe('label calibration', () => {
  it('saves the offset on this computer and prints the test label at it', async () => {
    render(<PrinterSettingsPage />);
    const input = await screen.findByLabelText(/printable area starts/i);
    expect(input).toHaveValue(0); // default: flush left
    fireEvent.change(input, { target: { value: '6.5' } });
    fireEvent.click(screen.getByRole('button', { name: /print a test label/i }));
    expect(getLabelOffsetMm()).toBe(6.5);
    expect(printMock).toHaveBeenCalledTimes(1);
    const html = printMock.mock.calls[0][0] as string;
    expect(html).toContain('class="win outline" style="left:6.5mm"');
    expect(html).toMatch(/size:\s*100mm 15mm/);
  });

  it('"Save offset" keeps the offset, and names the web address it is kept for', async () => {
    // The offset lives in browser storage, which is per web address: IMS at
    // its other address starts flush left. The card must not say "this
    // computer" as if that covered both.
    render(<PrinterSettingsPage />);
    const input = await screen.findByLabelText(/printable area starts/i);
    fireEvent.change(input, { target: { value: '7' } });
    printMock.mockClear();
    fireEvent.click(screen.getByRole('button', { name: /save offset/i }));
    expect(getLabelOffsetMm()).toBe(7);
    expect(printMock).not.toHaveBeenCalled();
    expect(screen.getByText(window.location.host)).toBeInTheDocument();
    expect(screen.getByText(/at another web address, set it there too/i)).toBeInTheDocument();
  });

  it('offers no label-size choice (it drove nothing; stock labels are 100 x 15 mm)', async () => {
    render(<PrinterSettingsPage />);
    await screen.findByLabelText(/printable area starts/i);
    expect(screen.queryByRole('option', { name: /50 x 25 mm/i })).not.toBeInTheDocument();
  });
});
