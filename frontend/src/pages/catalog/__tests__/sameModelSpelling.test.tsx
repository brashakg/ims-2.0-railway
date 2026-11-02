// ============================================================================
// IMS 2.0 - The same model written differently (owner 2026-11-01)
// ============================================================================
// "RB 4345 / 0RB4345 / RB-4345 / only 4345 are the same product." The
// similar-products strip shows similar_models: RED for a same colour + size
// twin, AMBER when only the model matches; the save popup's "maybe" mode
// (409 NEAR_DUPLICATE) asks "Is this the same product?". Open it = a NEW TAB,
// never Enter; "It's a different product" needs a reason.

import { render, screen, fireEvent, within } from '@testing-library/react';
import { vi, type Mock } from 'vitest';
import { SimilarProductsHint } from '../SimilarProductsHint';
import { DuplicateProductModal } from '../DuplicateProductModal';
import { useSimilarProducts } from '../useSimilarProducts';
import type { SimilarProductsResponse, DuplicateProductInfo } from '../../../services/api/products';

vi.mock('../useSimilarProducts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../useSimilarProducts')>();
  return { ...actual, useSimilarProducts: vi.fn() };
});
const mockHook = useSimilarProducts as unknown as Mock;

const TWIN = {
  product_id: 'P-OLD',
  sku: 'SG-RAYBAN-RB4340-601/M3-50',
  brand: 'Ray-Ban',
  model: 'RB4340',
  colour_code: '601/M3',
  size: '50',
  created_by_name: 'Priya',
  created_at: '2026-09-12T10:00:00',
};
const OTHER = { ...TWIN, product_id: 'P-2', sku: 'SG-RAYBAN-RB4340-710/51-50', colour_code: '710/51' };

function answer(sameColourSize: boolean): SimilarProductsResponse {
  return {
    exact_match: null,
    siblings: [],
    model_colour_count: 0,
    similar_models: [
      {
        model: 'RB4340',
        brand: 'Ray-Ban',
        same_colour_size: sameColourSize,
        products: sameColourSize ? [TWIN, OTHER] : [OTHER],
      },
    ],
  };
}

function renderHint(over: Partial<Parameters<typeof SimilarProductsHint>[0]> = {}) {
  const onPickSibling = vi.fn();
  const onMarkDifferent = vi.fn();
  render(
    <SimilarProductsHint
      category="SG"
      attributes={{ brand_name: 'Ray-Ban', model_no: '4340', colour_code: '601/M3', lens_size: '50' }}
      onPickSibling={onPickSibling}
      onOpenExisting={vi.fn()}
      onMarkDifferent={onMarkDifferent}
      {...over}
    />,
  );
  return { onPickSibling, onMarkDifferent };
}

let openSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
});
afterEach(() => {
  vi.clearAllMocks();
  openSpy.mockRestore();
});

describe('the strip: the same model written differently', () => {
  it('a same colour + size twin is a RED panel in the owner-approved words', () => {
    mockHook.mockReturnValue({ armed: true, data: answer(true) });
    renderHint();
    const red = screen.getByTestId('same-model-red');
    expect(red).toHaveTextContent(
      'This looks like the same product: Ray-Ban RB4340, colour 601/M3, size 50 (SKU SG-RAYBAN-RB4340-601/M3-50), added by Priya on 12 Sep.',
    );
    expect(screen.queryByTestId('same-model-amber')).toBeNull();
  });

  it('a model-only match is an AMBER hint', () => {
    mockHook.mockReturnValue({ armed: true, data: answer(false) });
    renderHint();
    expect(screen.getByTestId('same-model-amber')).toHaveTextContent(
      'Is this already in the catalogue? Ray-Ban has RB4340 - it may be the same model as the 4340 you typed.',
    );
    expect(screen.queryByTestId('same-model-red')).toBeNull();
  });

  it('its buttons stay out of the Tab order like the colour chips', () => {
    mockHook.mockReturnValue({ armed: true, data: answer(true) });
    renderHint();
    for (const name of ['Open it', 'Add a new colour or size of RB4340', "It's a different product"]) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('tabindex', '-1');
    }
  });

  it('Open it opens the product in a NEW tab (the typed form stays)', () => {
    mockHook.mockReturnValue({ armed: true, data: answer(true) });
    renderHint();
    fireEvent.click(screen.getByRole('button', { name: 'Open it' }));
    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy.mock.calls[0][0]).toContain('SG-RAYBAN-RB4340-601');
    expect(openSpy.mock.calls[0][1]).toBe('_blank');
  });

  it('"Add a new colour or size of RB4340" takes the stored product and spelling', () => {
    mockHook.mockReturnValue({ armed: true, data: answer(true) });
    const { onPickSibling } = renderHint();
    fireEvent.click(screen.getByRole('button', { name: 'Add a new colour or size of RB4340' }));
    expect(onPickSibling).toHaveBeenCalledWith('P-OLD');
  });

  it('"It\'s a different product" needs a reason, then records it for the group', () => {
    mockHook.mockReturnValue({ armed: true, data: answer(true) });
    const { onMarkDifferent } = renderHint();
    fireEvent.click(screen.getByRole('button', { name: "It's a different product" }));
    const box = screen.getByTestId('different-reason');
    expect(within(box).getByText('Why is it different?')).toBeInTheDocument();
    const save = within(box).getByRole('button', { name: 'Save as a different product' });
    expect(save).toBeDisabled();
    fireEvent.click(within(box).getByRole('button', { name: 'Junior / kids version' }));
    fireEvent.change(within(box).getByLabelText('Why is it different?'), { target: { value: 'RJ line' } });
    fireEvent.click(save);
    expect(onMarkDifferent).toHaveBeenCalledWith(['P-OLD', 'P-2'], 'Junior / kids version - RJ line');
  });

  it('an answered group is hidden, and variant mode hides it like the colour chips', () => {
    mockHook.mockReturnValue({ armed: true, data: answer(true) });
    renderHint({ answeredIds: new Set(['P-OLD', 'P-2']) });
    expect(screen.queryByTestId('same-model-red')).toBeNull();
  });

  it('variant mode hides it', () => {
    mockHook.mockReturnValue({ armed: true, data: answer(true) });
    renderHint({ variantMode: true });
    expect(screen.queryByTestId('same-model-red')).toBeNull();
  });
});

const INFO: DuplicateProductInfo = { ...TWIN, name: 'Ray-Ban RB4340 Square Sunglasses', is_active: true };

function renderMaybe() {
  const onAddVariant = vi.fn();
  const onOpenExisting = vi.fn();
  const onClose = vi.fn();
  const onDifferent = vi.fn();
  render(
    <DuplicateProductModal
      info={INFO}
      maybe
      typed={{ model: '4340', colour: '601/M3', size: '50' }}
      onAddVariant={onAddVariant}
      onOpenExisting={onOpenExisting}
      onClose={onClose}
      onDifferent={onDifferent}
    />,
  );
  return { onAddVariant, onOpenExisting, onClose, onDifferent };
}

describe('the save popup: "Is this the same product?" (409 NEAR_DUPLICATE)', () => {
  it('asks in the owner-approved words', () => {
    renderMaybe();
    expect(screen.getByRole('heading', { name: 'Is this the same product?' })).toBeInTheDocument();
    expect(
      screen.getByText(
        'Ray-Ban RB4340 601/M3 size 50 is already in the catalogue. Saving 4340 601/M3 size 50 would make a second copy, and its stock and sales would be split between the two.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('added by Priya on 12 Sep')).toBeInTheDocument();
  });

  it('Enter decides nothing: no new tab, no variant, no leaving the form', () => {
    const { onAddVariant, onOpenExisting } = renderMaybe();
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onAddVariant).not.toHaveBeenCalled();
    expect(onOpenExisting).not.toHaveBeenCalled();
    expect(openSpy).not.toHaveBeenCalled();
    expect(document.activeElement?.getAttribute('role')).toBe('dialog');
  });

  it('Esc goes back', () => {
    const { onClose } = renderMaybe();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Open it opens a NEW tab and never navigates away', () => {
    const { onOpenExisting } = renderMaybe();
    fireEvent.click(screen.getByRole('button', { name: 'Open it' }));
    expect(openSpy.mock.calls[0][1]).toBe('_blank');
    expect(onOpenExisting).not.toHaveBeenCalled();
  });

  it('"Add a new colour or size of RB4340" is the variant path', () => {
    const { onAddVariant } = renderMaybe();
    fireEvent.click(screen.getByRole('button', { name: 'Add a new colour or size of RB4340' }));
    expect(onAddVariant).toHaveBeenCalledTimes(1);
  });

  it('"It\'s a different product" needs a reason, then saves again with it', () => {
    const { onDifferent } = renderMaybe();
    fireEvent.click(screen.getByRole('button', { name: "It's a different product" }));
    const save = screen.getByRole('button', { name: 'Save as a different product' });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Why is it different?'), { target: { value: 'Different lens' } });
    fireEvent.click(save);
    expect(onDifferent).toHaveBeenCalledWith('Different lens');
  });

  it('the exact-duplicate popup is unchanged: Enter is still "add a variant"', () => {
    const onAddVariant = vi.fn();
    render(<DuplicateProductModal info={INFO} onAddVariant={onAddVariant} onOpenExisting={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole('heading', { name: 'This product already exists' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: "It's a different product" })).toBeNull();
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onAddVariant).toHaveBeenCalledTimes(1);
  });
});
