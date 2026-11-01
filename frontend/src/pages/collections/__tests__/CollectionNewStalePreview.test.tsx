// ============================================================================
// IMS 2.0 - New Collection builder: Create only on the preview of THESE rules
// ============================================================================
// The preview request is debounced. For that window after a chip changes, the
// last preview (of the OLD rules) must not keep Create on: a click there would
// post rules the website may not be able to read. Real useDebounce here.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../../services/api/products', () => ({
  productApi: {
    getCategoryRegistry: vi.fn().mockResolvedValue({
      categories: [
        { code: 'SUNGLASS', name: 'Sunglasses', fields: [] },
        { code: 'FRAME', name: 'Frames', fields: [] },
      ],
    }),
    getBrandOptions: vi.fn().mockResolvedValue({ brands: [] }),
  },
}));

vi.mock('../../../services/api/collectionsInsights', () => ({
  collectionsInsightsApi: { preview: vi.fn(), createSmart: vi.fn() },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
  useNavigate: () => vi.fn(),
}));

import CollectionNewPage from '../CollectionNewPage';
import { collectionsInsightsApi } from '../../../services/api/collectionsInsights';

beforeEach(() => {
  vi.mocked(collectionsInsightsApi.preview).mockReset();
  vi.mocked(collectionsInsightsApi.createSmart).mockReset();
  vi.mocked(collectionsInsightsApi.preview).mockImplementation(async (rules: any[]) => ({
    match_count: 1, units_on_hand: 0, sample: [],
    not_evaluable: JSON.stringify(rules).includes('FRAME') ? 'frames rule the website cannot read' : null,
  }) as any);
});

describe('New Collection: Create waits for the preview of the rules being sent', () => {
  it('a chip change turns Create off at once, before the debounced preview runs', async () => {
    render(<CollectionNewPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sunglasses' }));
    const create = () => screen.getByRole('button', { name: /Create collection/ }) as HTMLButtonElement;
    await waitFor(() => expect(create().disabled).toBe(false), { timeout: 3000 });
    await userEvent.click(screen.getByRole('button', { name: 'Frames' }));
    expect(create().disabled).toBe(true);
    await userEvent.click(create());
    expect(collectionsInsightsApi.createSmart).not.toHaveBeenCalled();
  });
});
