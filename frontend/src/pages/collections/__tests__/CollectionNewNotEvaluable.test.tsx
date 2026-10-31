// ============================================================================
// IMS 2.0 - New Collection builder: rules the website cannot read
// ============================================================================
// The live preview reports `not_evaluable` when the website (and IMS's one
// membership rule) cannot read the chips' rules -- the collection would show
// 0 products there. The page must SHOW the reason and keep Create off.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../../services/api/products', () => ({
  productApi: {
    getCategoryRegistry: vi.fn().mockResolvedValue({
      categories: [{ code: 'SUNGLASS', name: 'Sunglasses', fields: [] }],
    }),
    getBrandOptions: vi.fn().mockResolvedValue({ brands: [] }),
  },
}));

vi.mock('../../../services/api/collectionsInsights', () => ({
  collectionsInsightsApi: { preview: vi.fn(), createSmart: vi.fn() },
}));

vi.mock('../../../hooks/useDebounce', () => ({ useDebounce: (v: unknown) => v }));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
  useNavigate: () => vi.fn(),
}));

import CollectionNewPage from '../CollectionNewPage';
import { collectionsInsightsApi } from '../../../services/api/collectionsInsights';

const PREVIEW = { match_count: 0, units_on_hand: 0, sample: [] };

beforeEach(() => {
  vi.mocked(collectionsInsightsApi.preview).mockReset();
});

describe('New Collection: rules the website cannot read', () => {
  it('shows the reason and keeps Create off', async () => {
    vi.mocked(collectionsInsightsApi.preview).mockResolvedValue({
      ...PREVIEW,
      not_evaluable: 'rules IMS cannot evaluate as the website does: frame_size EQUALS 52',
    });
    render(<CollectionNewPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sunglasses' }));
    const warn = await screen.findByTestId('not-evaluable-warning');
    expect(warn.textContent).toMatch(/frame_size EQUALS 52/);
    expect((screen.getByRole('button', { name: /Create collection/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('a readable rule set leaves Create on', async () => {
    vi.mocked(collectionsInsightsApi.preview).mockResolvedValue({ ...PREVIEW, not_evaluable: null });
    render(<CollectionNewPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sunglasses' }));
    await screen.findByText('Products');
    expect(screen.queryByTestId('not-evaluable-warning')).toBeNull();
    expect((screen.getByRole('button', { name: /Create collection/ }) as HTMLButtonElement).disabled).toBe(false);
  });
});
