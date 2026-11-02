// ============================================================================
// IMS 2.0 - Collection browse never shows an unreadable view as "no products"
// ============================================================================
// Review round 5: a page whose membership could not be read (total null, with
// or without the unreadable flag) must say so -- not "No products in this
// collection." and not the "Empty collection." card. A real empty page (total
// 0) still says it is empty.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../../services/api/collectionBrowse', () => ({
  collectionBrowseApi: { list: vi.fn(), products: vi.fn(), refresh: vi.fn() },
}));
vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: { roles: [] } }) }));

import CollectionBrowsePage from '../CollectionBrowsePage';
import { collectionBrowseApi } from '../../../services/api/collectionBrowse';

const page = (extra: object) => ({ handle: 'h', products: [], skip: 0, limit: 24, ...extra });

describe('collection browse over an unreadable view', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (collectionBrowseApi.list as any).mockResolvedValue([{ handle: 'h', title: 'H', products_count: 3 }]);
  });

  it.each([
    ['flagged unreadable', { total: null, unreadable: true }],
    ['total null without the flag', { total: null }],
  ])('%s says could-not-be-read, never empty', async (_n, extra) => {
    (collectionBrowseApi.products as any).mockResolvedValue(page(extra));
    render(<CollectionBrowsePage />);
    expect((await screen.findAllByText(/could not be read just now/)).length).toBeGreaterThan(0);
    expect(screen.queryByText('No products in this collection.')).toBeNull();
    expect(screen.queryByText('Empty collection.')).toBeNull();
  });

  it('a readable empty page still says it is empty', async () => {
    (collectionBrowseApi.products as any).mockResolvedValue(page({ total: 0 }));
    render(<CollectionBrowsePage />);
    expect(await screen.findByText('No products in this collection.')).toBeTruthy();
    expect(screen.queryByText(/could not be read just now/)).toBeNull();
  });
});
