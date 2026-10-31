import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../../../services/api/client';
import {
  CANONICAL_TO_PICKER,
  CATEGORY_FIELDS,
} from '../../../../domain/catalog/productAdd/categoryFields';

const suggestColourName = vi.fn();
const toastError = vi.fn();
vi.mock('../../../../services/api/products', () => ({
  productApi: { suggestColourName: (...a: unknown[]) => suggestColourName(...a) },
}));
vi.mock('../../../../context/ToastContext', () => ({
  useToast: () => ({ error: toastError, success: vi.fn(), warning: vi.fn() }),
}));

import { COLOUR_NAME_CANONICALS, ColourNameSuggest, hasColourNameSuggest } from '../ColourNameSuggest';

function Harness({ productId = null, photo = '/api/v1/products/image/p1' }: { productId?: string | null; photo?: string | null }) {
  const [value, setValue] = useState('');
  return (
    <>
      <ColourNameSuggest
        category="SG"
        attributes={{ brand_name: 'Ray-Ban', model_no: 'RB3025', polarization: 'Yes' }}
        value={value}
        onChange={setValue}
        productId={productId}
        photo={photo}
        inputClassName="input-field w-full"
      />
      <output data-testid="saved-value">{value}</output>
    </>
  );
}

describe('Colour Name - Suggest from photo 1', () => {
  beforeEach(() => {
    suggestColourName.mockReset();
    toastError.mockReset();
  });

  it('shows the suggestion and Accept puts it in the field (nothing before Accept)', async () => {
    suggestColourName.mockResolvedValue({ status: 'SUGGESTED', suggestion: 'Gold / Green Polarized' });
    render(<Harness productId="prod-1" />);
    fireEvent.click(screen.getByRole('button', { name: /suggest/i }));
    expect(await screen.findByTestId('qa-colour-suggestion')).toHaveTextContent('Gold / Green Polarized');
    expect(screen.getByTestId('saved-value')).toHaveTextContent('');
    expect(suggestColourName).toHaveBeenCalledWith({
      product_id: 'prod-1',
      image_url: '/api/v1/products/image/p1',
      category: 'SG',
      attributes: { brand_name: 'Ray-Ban', model_no: 'RB3025', polarization: 'Yes' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }));
    expect(screen.getByLabelText('Colour Name')).toHaveValue('Gold / Green Polarized');
    expect(screen.queryByTestId('qa-colour-suggestion')).not.toBeInTheDocument();
  });

  it('Change puts the suggestion in the field and focuses it for editing', async () => {
    suggestColourName.mockResolvedValue({ status: 'SUGGESTED', suggestion: 'Havana / Brown Gradient' });
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: /suggest/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Change' }));
    const input = screen.getByLabelText('Colour Name');
    expect(input).toHaveValue('Havana / Brown Gradient');
    expect(input).toHaveFocus();
    // Add product: no product id is sent, only the form's photo 1.
    expect(suggestColourName.mock.calls[0][0]).not.toHaveProperty('product_id');
  });

  it('a reply for product A is never shown (or accepted) on product B', async () => {
    let resolve: (v: unknown) => void = () => {};
    suggestColourName.mockReturnValue(new Promise((r) => { resolve = r; }));
    const { rerender } = render(<Harness productId="prod-A" photo="/api/v1/products/image/a" />);
    fireEvent.click(screen.getByRole('button', { name: /suggest/i }));
    rerender(<Harness productId="prod-B" photo="/api/v1/products/image/b" />);
    await act(async () => {
      resolve({ status: 'SUGGESTED', suggestion: 'Gold / Green' });
    });
    expect(screen.queryByTestId('qa-colour-suggestion')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument();
  });

  it('a shown suggestion goes away when the photo changes', async () => {
    suggestColourName.mockResolvedValue({ status: 'SUGGESTED', suggestion: 'Gold / Green' });
    const { rerender } = render(<Harness productId="prod-A" photo="/api/v1/products/image/a" />);
    fireEvent.click(screen.getByRole('button', { name: /suggest/i }));
    await screen.findByTestId('qa-colour-suggestion');
    rerender(<Harness productId="prod-A" photo="/api/v1/products/image/other" />);
    expect(screen.queryByTestId('qa-colour-suggestion')).not.toBeInTheDocument();
  });

  it('says plainly why there is no suggestion', async () => {
    suggestColourName.mockResolvedValue({
      status: 'FAILED_NO_KEY', suggestion: '',
      message: 'No Anthropic key is set. Add it in Settings -> Integrations -> Anthropic.',
    });
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: /suggest/i }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('No Anthropic key is set. Add it in Settings -> Integrations -> Anthropic.')
    );
    expect(screen.queryByTestId('qa-colour-suggestion')).not.toBeInTheDocument();
  });

  it("shows the server's 429 detail", async () => {
    const detail = 'Too many colour suggestions - at most 30 every 10 minutes. Try again shortly.';
    suggestColourName.mockRejectedValue(new ApiError('Request failed', { status: 429, detail }));
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: /suggest/i }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(detail));
  });

  it('cannot suggest without a photo', () => {
    render(<Harness photo={null} />);
    const btn = screen.getByRole('button', { name: /suggest/i });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', 'Add a photo first');
  });
});

describe('which categories get the Suggest field', () => {
  it('is derived from the registry codes: eyewear yes, contact lenses no', () => {
    for (const code of ['SG', 'FR', 'RG', 'SMTFR', 'SMTSG']) expect(hasColourNameSuggest(code)).toBe(true);
    for (const code of ['CL', 'CCL', 'LS', 'WT', 'ACC']) expect(hasColourNameSuggest(code)).toBe(false);
  });

  it('never doubles a grid field: none of these categories has colour_name in the grid', () => {
    for (const canonical of COLOUR_NAME_CANONICALS) {
      const fields = CATEGORY_FIELDS[CANONICAL_TO_PICKER[canonical]];
      expect(fields, canonical).toBeDefined();
      expect(fields.some((f) => f.name === 'colour_name'), canonical).toBe(false);
    }
  });
});
