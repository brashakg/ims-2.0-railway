// ============================================================================
// IMS 2.0 - DuplicateProductModal tests (duplicate-rescue popup)
// ============================================================================
// Locks the popup contract: shows the EXISTING product from the enriched 409
// payload, Enter fires the default "add a new colour/size" action, Esc fires
// "go back" (the parent leaves the form untouched), the secondary action
// navigates to the existing product, and there is NO "create anyway" path.

import { render, screen, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import { DuplicateProductModal } from '../DuplicateProductModal';
import type { DuplicateProductInfo } from '../../../services/api/products';

const INFO: DuplicateProductInfo = {
  product_id: 'P-1',
  sku: 'SGRAYBANRB2140BLK',
  name: 'Ray-Ban RB-2140',
  brand: 'Ray-Ban',
  model: 'RB-2140',
  colour_code: 'BLK',
  size: '52',
  mrp: 5000,
  offer_price: 4500,
  is_active: true,
  catalog_status: 'ACTIVE',
  image_url: '/api/v1/products/image/img-1',
};

function renderModal(overrides: Partial<Parameters<typeof DuplicateProductModal>[0]> = {}) {
  const onAddVariant = vi.fn();
  const onOpenExisting = vi.fn();
  const onClose = vi.fn();
  render(
    <DuplicateProductModal
      info={INFO}
      onAddVariant={onAddVariant}
      onOpenExisting={onOpenExisting}
      onClose={onClose}
      {...overrides}
    />
  );
  return { onAddVariant, onOpenExisting, onClose };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('DuplicateProductModal', () => {
  it('shows the existing product name, SKU, colour, size, price and status', () => {
    renderModal();
    expect(screen.getByText('Ray-Ban RB-2140')).toBeInTheDocument();
    expect(screen.getByText('SGRAYBANRB2140BLK')).toBeInTheDocument();
    expect(screen.getByText(/Colour BLK/)).toBeInTheDocument();
    expect(screen.getByText(/Size 52/)).toBeInTheDocument();
    expect(screen.getByText(/MRP ₹5,000/)).toBeInTheDocument();
    expect(screen.getByText(/Offer ₹4,500/)).toBeInTheDocument();
    expect(screen.getByText('Active in your catalog')).toBeInTheDocument();
  });

  it('flags an inactive / draft existing row', () => {
    renderModal({ info: { ...INFO, is_active: false } });
    expect(screen.getByText('Inactive (archived)')).toBeInTheDocument();
  });

  it('offers exactly the three rescue actions — no "create anyway"', () => {
    renderModal();
    expect(
      screen.getByRole('button', { name: /Add a new colour\/size of this model/ })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Open the existing product/ })
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Go back/ })).toBeInTheDocument();
    expect(screen.queryByText(/create anyway/i)).not.toBeInTheDocument();
  });

  it('Enter triggers the default add-variant action', () => {
    const { onAddVariant, onClose } = renderModal();
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onAddVariant).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('Esc triggers go-back (form left intact by the parent)', () => {
    const { onAddVariant, onClose } = renderModal();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onAddVariant).not.toHaveBeenCalled();
  });

  it('click handlers route to the right callbacks', () => {
    const { onAddVariant, onOpenExisting, onClose } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: /Open the existing product/ }));
    expect(onOpenExisting).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /Go back/ }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole('button', { name: /Add a new colour\/size of this model/ })
    );
    expect(onAddVariant).toHaveBeenCalledTimes(1);
  });

  it('busy disables the actions and suppresses the Enter shortcut', () => {
    const { onAddVariant } = renderModal({ busy: true });
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onAddVariant).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Loading/ })).toBeDisabled();
  });
});
