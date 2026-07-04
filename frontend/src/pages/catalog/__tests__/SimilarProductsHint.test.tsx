// ============================================================================
// IMS 2.0 - SimilarProductsHint tests (dup-detect Phase 2 strip)
// ============================================================================
// Locks the render contract: NOTHING while un-armed / loading / errored / no
// matches; a quiet sibling line with out-of-Tab-order chips; an exact-match
// warning with the Open link; variant mode suppresses siblings but keeps the
// exact warning. The debounce/abort behaviour is covered by the hook's own
// tests — here the hook is mocked so each render state is exact.

import { render, screen, fireEvent } from '@testing-library/react';
import { vi, type Mock } from 'vitest';
import { SimilarProductsHint } from '../SimilarProductsHint';
import { useSimilarProducts } from '../useSimilarProducts';
import type { SimilarProductsResponse } from '../../../services/api/products';

vi.mock('../useSimilarProducts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../useSimilarProducts')>();
  return { ...actual, useSimilarProducts: vi.fn() };
});

const mockHook = useSimilarProducts as unknown as Mock;

const SIBLINGS: SimilarProductsResponse = {
  exact_match: null,
  siblings: [
    { product_id: 'P-1', sku: 'FRRB2140BLK', name: 'Ray-Ban RB-2140', colour_code: 'BLK' },
    { product_id: 'P-2', sku: 'FRRB2140RED', name: 'Ray-Ban RB-2140', colour_code: 'RED', size: '52' },
  ],
  model_colour_count: 3,
};

const EXACT: SimilarProductsResponse = {
  exact_match: {
    product_id: 'P-9',
    sku: 'FRRB2140GRN',
    name: 'Ray-Ban RB-2140',
    colour_code: 'GRN',
  },
  siblings: [
    { product_id: 'P-1', sku: 'FRRB2140BLK', name: 'Ray-Ban RB-2140', colour_code: 'BLK' },
  ],
  model_colour_count: 2,
};

function renderHint(overrides: Partial<Parameters<typeof SimilarProductsHint>[0]> = {}) {
  const onPickSibling = vi.fn();
  const onOpenExisting = vi.fn();
  const utils = render(
    <SimilarProductsHint
      category="FR"
      brand="Ray-Ban"
      model="RB-2140"
      colour=""
      size=""
      onPickSibling={onPickSibling}
      onOpenExisting={onOpenExisting}
      {...overrides}
    />
  );
  return { ...utils, onPickSibling, onOpenExisting };
}

describe('SimilarProductsHint — render-nothing states', () => {
  it('renders nothing while un-armed', () => {
    mockHook.mockReturnValue({ data: null, armed: false });
    const { container } = renderHint();
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing while loading / errored (null data)', () => {
    mockHook.mockReturnValue({ data: null, armed: true });
    const { container } = renderHint();
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when there are no matches', () => {
    mockHook.mockReturnValue({
      data: { exact_match: null, siblings: [], model_colour_count: 0 },
      armed: true,
    });
    const { container } = renderHint();
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing in variant mode when there are only siblings', () => {
    mockHook.mockReturnValue({ data: SIBLINGS, armed: true });
    const { container } = renderHint({ variantMode: true });
    expect(container.firstChild).toBeNull();
  });
});

describe('SimilarProductsHint — siblings line', () => {
  beforeEach(() => {
    mockHook.mockReturnValue({ data: SIBLINGS, armed: true });
  });

  it('shows the model-exists line with the TRUE colour count and one chip per sibling', () => {
    renderHint();
    expect(screen.getByText(/This model exists in 3 colours:/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'BLK' })).toBeInTheDocument();
    // size folds into the chip label
    expect(screen.getByRole('button', { name: 'RED · 52' })).toBeInTheDocument();
  });

  it('keeps every chip OUT of the Tab order (tabIndex=-1)', () => {
    renderHint();
    screen.getAllByRole('button').forEach((btn) => {
      expect(btn).toHaveAttribute('tabindex', '-1');
    });
  });

  it('a chip click hands the sibling product_id to the Phase 1 variant path', () => {
    const { onPickSibling } = renderHint();
    fireEvent.click(screen.getByRole('button', { name: 'BLK' }));
    expect(onPickSibling).toHaveBeenCalledWith('P-1');
  });
});

describe('SimilarProductsHint — exact-match warning', () => {
  beforeEach(() => {
    mockHook.mockReturnValue({ data: EXACT, armed: true });
  });

  it('shows the warning with the existing SKU and an Open link', () => {
    renderHint();
    expect(screen.getByRole('alert')).toHaveTextContent(
      /This exact colour already exists — SKU\s*FRRB2140GRN/
    );
    expect(screen.getByRole('alert')).toHaveTextContent(/enter a different colour/);
  });

  it('Open it fires the popup product-open path with the SKU', () => {
    const { onOpenExisting } = renderHint();
    fireEvent.click(screen.getByRole('button', { name: 'Open it' }));
    expect(onOpenExisting).toHaveBeenCalledWith('FRRB2140GRN');
  });

  it('the Open link is out of the Tab order too', () => {
    renderHint();
    expect(screen.getByRole('button', { name: 'Open it' })).toHaveAttribute('tabindex', '-1');
  });

  it('variant mode keeps the exact warning but suppresses the sibling chips', () => {
    renderHint({ variantMode: true });
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText(/This model exists in/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'BLK' })).not.toBeInTheDocument();
  });
});
