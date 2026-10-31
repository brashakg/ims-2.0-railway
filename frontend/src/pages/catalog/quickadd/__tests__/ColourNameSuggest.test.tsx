import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const suggestColourName = vi.fn();
const toastError = vi.fn();
vi.mock('../../../../services/api/products', () => ({
  productApi: { suggestColourName: (...a: unknown[]) => suggestColourName(...a) },
}));
vi.mock('../../../../context/ToastContext', () => ({
  useToast: () => ({ error: toastError, success: vi.fn(), warning: vi.fn() }),
}));

import { ColourNameSuggest } from '../ColourNameSuggest';

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

  it('cannot suggest without a photo', () => {
    render(<Harness photo={null} />);
    const btn = screen.getByRole('button', { name: /suggest/i });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', 'Add a photo first');
  });
});
