// ============================================================================
// Manage Barcode holds the MANUFACTURER's code (owner ruling 2026-09-28, F115)
// ============================================================================
// "We maintain our own inventory through IMS barcodes and manufacturer UPC and
// GTIN for reference and online stock keeping/Google." IMS barcodes are minted
// per unit at receipt; the product's barcode is only the maker's UPC/EAN. So
// the modal must not invent one: no random 'Generate', no CODE128/CODE39 picker.
// Whether a value IS a GTIN is decided by the server (services/gtin.py, one
// rule); the modal shows the server's refusal in words.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { BarcodeManagementModal } from '../BarcodeManagementModal';

function renderModal(onSave = vi.fn().mockResolvedValue(undefined), currentBarcode = '') {
  render(
    <BarcodeManagementModal
      isOpen
      onClose={() => {}}
      productName="Carrera CA 8895 Havana"
      currentBarcode={currentBarcode}
      onSave={onSave}
    />,
  );
  return onSave;
}

describe('Manage Barcode (manufacturer UPC / EAN only)', () => {
  it('offers no Generate button and no symbology picker', () => {
    renderModal();
    expect(screen.queryByRole('button', { name: /generate/i })).toBeNull();
    for (const f of ['CODE128', 'CODE39', 'EAN13', 'UPC']) {
      expect(screen.queryByRole('button', { name: f })).toBeNull();
    }
  });

  it('saves the typed manufacturer code as-is', async () => {
    const onSave = renderModal();
    fireEvent.change(screen.getByLabelText(/manufacturer barcode/i), {
      target: { value: '4006381333931' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save barcode/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('4006381333931'));
  });

  it("shows the server's reason when the code is not a manufacturer barcode", async () => {
    const onSave = vi
      .fn()
      .mockRejectedValue(new Error("'2000000000015' is not a manufacturer barcode (RESTRICTED)."));
    renderModal(onSave);
    fireEvent.change(screen.getByLabelText(/manufacturer barcode/i), {
      target: { value: '2000000000015' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save barcode/i }));
    expect(await screen.findByText(/not a manufacturer barcode/i)).toBeInTheDocument();
  });
});
