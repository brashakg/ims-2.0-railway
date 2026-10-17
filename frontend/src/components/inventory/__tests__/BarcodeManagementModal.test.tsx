// ============================================================================
// Manage Barcode holds the MANUFACTURER's code (owner ruling 2026-09-28, F115)
// ============================================================================
// "We maintain our own inventory through IMS barcodes and manufacturer UPC and
// GTIN for reference and online stock keeping/Google." IMS barcodes are minted
// per unit at receipt; the product keeps only the maker's UPC/EAN, as its `gtin`
// attribute -- the field the Add Product "GTIN (mfr)" box writes and the
// Shopify push reads. So the modal must not invent one (no random 'Generate',
// no CODE128/CODE39 picker), and it must show the server's refusal in words.
// The refusal test rejects with a REAL ApiError from the api client (it has no
// .response; its message is the server's detail), the shape the page gets.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { AxiosError } from 'axios';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const updateProduct = vi.fn();
vi.mock('../../../services/api/products', () => ({
  productApi: { updateProduct: (...a: unknown[]) => updateProduct(...a) },
}));

import { buildApiError } from '../../../services/api/client';
import { BarcodeManagementModal } from '../BarcodeManagementModal';

function renderModal(currentGtin = '', onSaved = vi.fn(), oldCode = '') {
  render(
    <BarcodeManagementModal
      isOpen
      onClose={() => {}}
      productId="P-42"
      productName="Carrera CA 8895 Havana"
      currentGtin={currentGtin}
      oldCode={oldCode}
      onSaved={onSaved}
    />,
  );
  return onSaved;
}

function typeAndSave(code: string) {
  fireEvent.change(screen.getByLabelText(/manufacturer barcode/i), { target: { value: code } });
  fireEvent.click(screen.getByRole('button', { name: /save barcode/i }));
}

describe('Manage Barcode (manufacturer UPC / EAN only)', () => {
  beforeEach(() => updateProduct.mockReset());

  it('offers no Generate button and no symbology picker', () => {
    renderModal();
    expect(screen.queryByRole('button', { name: /generate/i })).toBeNull();
    for (const f of ['CODE128', 'CODE39', 'EAN13', 'UPC']) {
      expect(screen.queryByRole('button', { name: f })).toBeNull();
    }
  });

  it('opens with the saved GTIN', () => {
    renderModal('4006381333931');
    expect(screen.getByLabelText(/manufacturer barcode/i)).toHaveValue('4006381333931');
  });

  it("saves the code as the product's GTIN, the field that goes to Shopify", async () => {
    updateProduct.mockResolvedValue({});
    const onSaved = renderModal();
    typeAndSave('4006381333931');
    await waitFor(() =>
      expect(updateProduct).toHaveBeenCalledWith('P-42', { attributes: { gtin: '4006381333931' } }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("shows the server's reason when the code is not a manufacturer barcode", async () => {
    const detail = "'BV0000000042' is not a valid GTIN (NONNUMERIC).";
    const refusal = buildApiError({
      isAxiosError: true,
      message: 'Request failed with status code 422',
      response: { status: 422, data: { detail } },
    } as unknown as AxiosError<{ detail?: string }>);
    updateProduct.mockRejectedValueOnce(refusal);
    const onSaved = renderModal();
    typeAndSave('BV0000000042');
    expect(await screen.findByText(detail)).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('removes a wrong saved GTIN when the box is emptied', async () => {
    // A valid-but-wrong GTIN (the neighbouring frame's box) could only be
    // overwritten, never removed, so it kept going to Shopify and Google.
    updateProduct.mockResolvedValue({});
    const onSaved = renderModal('4006381333931');
    fireEvent.change(screen.getByLabelText(/manufacturer barcode/i), { target: { value: '' } });
    const remove = screen.getByRole('button', { name: /remove barcode/i });
    expect(remove).toBeEnabled();
    fireEvent.click(remove);
    await waitFor(() =>
      expect(updateProduct).toHaveBeenCalledWith('P-42', { attributes: { gtin: '' } }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('shows an old product barcode apart and never puts it in the box to save', () => {
    // Main's old Generate wrote random EAN-13s to the product barcode; most pass
    // the format check. Offered as the saved GTIN, Save on the untouched box sent
    // one to Shopify as the maker's barcode.
    renderModal('', vi.fn(), '5260181590836');
    expect(screen.getByText(/not a maker barcode/i)).toHaveTextContent(
      "Old IMS code 5260181590836 - not a maker barcode; type the code printed on the maker's box",
    );
    expect(screen.getByLabelText(/manufacturer barcode/i)).toHaveValue('');
    const save = screen.getByRole('button', { name: /save barcode/i });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(updateProduct).not.toHaveBeenCalled();
  });

  it('removes an old product barcode only on its own button', async () => {
    updateProduct.mockResolvedValue({});
    const onSaved = renderModal('', vi.fn(), '5260181590836');
    fireEvent.click(screen.getByRole('button', { name: /remove old code/i }));
    await waitFor(() =>
      expect(updateProduct).toHaveBeenCalledWith('P-42', { attributes: { gtin: '' } }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('has nothing to save when no GTIN was saved and the box is empty', () => {
    renderModal('');
    expect(screen.getByRole('button', { name: /save barcode/i })).toBeDisabled();
  });
});
