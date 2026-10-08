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

  it('Open it fires the popup product-open path with the existing product', () => {
    const { onOpenExisting } = renderHint();
    fireEvent.click(screen.getByRole('button', { name: 'Open it' }));
    expect(onOpenExisting).toHaveBeenCalledWith(EXACT.exact_match);
  });

  // Audit C3 (R1-85): the exact item a manager ordered before it was
  // catalogued is finished, never added again -- the hint says so and hands
  // the caller the draft (existingProductPath opens it in the editor).
  it('an ordered draft as the exact match leads to finishing it', () => {
    const draft = { ...EXACT.exact_match!, provisional: true };
    mockHook.mockReturnValue({ data: { ...EXACT, exact_match: draft }, armed: true });
    const { onOpenExisting } = renderHint();
    expect(screen.getByRole('alert')).toHaveTextContent(/ordered before it was catalogued/);
    expect(screen.getByRole('alert')).not.toHaveTextContent(/already exists/);
    fireEvent.click(screen.getByRole('button', { name: 'Finish it' }));
    expect(onOpenExisting).toHaveBeenCalledWith(draft);
  });

  // Panel round 7 (one rule, Add product): Save brings a discarded draft back
  // and its popup opens it -- the hint never sends it to the stock list.
  it('a discarded draft as the exact match says Save brings it back, with no link', () => {
    const discarded = { ...EXACT.exact_match!, discarded_draft: true, category: 'FRAME' };
    mockHook.mockReturnValue({ data: { ...EXACT, exact_match: discarded }, armed: true });
    const { onOpenExisting } = renderHint();
    expect(screen.getByRole('alert')).toHaveTextContent(
      /draft discarded earlier — SKU\s*FRRB2140GRN\. Saving it as\s*frame\s*brings it back/
    );
    expect(screen.queryByRole('button', { name: 'Open it' })).toBeNull();
    expect(onOpenExisting).not.toHaveBeenCalled();
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

// Panel round 7: a sizeless frame catalogued by eye size gets Save's own
// EYE_SIZE_NEEDED answer before Save, never "This exact colour already exists".
describe('SimilarProductsHint — eye size needed', () => {
  it('names the eye sizes Save would ask for, and no exact-colour warning', () => {
    mockHook.mockReturnValue({
      data: { exact_match: null, eye_size_needed: ['52', '54'], siblings: [], model_colour_count: 1 },
      armed: true,
    });
    renderHint();
    expect(screen.getByRole('alert')).toHaveTextContent(
      /in the catalogue by eye size \(52, 54\)\. Type the eye size/
    );
    expect(screen.queryByText(/already exists/)).toBeNull();
  });
});
