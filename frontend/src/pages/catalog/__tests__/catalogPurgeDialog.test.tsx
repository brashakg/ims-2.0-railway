// ============================================================================
// Delete permanently (owner 2026-10-24): an ARCHIVED product, SUPERADMIN only
// ============================================================================
// The dialog asks the server first (dry run): it names the SKUs that go and
// says it cannot be undone; the ones that stay archived show the server's
// reason. Only the ready ones are sent when the button is pressed. The bar's
// button is for SUPERADMIN in the Archived view only.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api/catalog', () => ({
  catalogBulkApi: { purge: vi.fn() },
}));

import { catalogBulkApi } from '../../../services/api/catalog';
import { PurgeDialog, SelectionBar } from '../CatalogBulkActions';

const purge = catalogBulkApi.purge as unknown as ReturnType<typeof vi.fn>;
const HISTORY =
  'It has sales/stock/purchase history (stock units), so it stays archived to keep the records complete.';

describe('PurgeDialog', () => {
  it('names the SKUs, shows each refusal, and deletes only the ready ones', async () => {
    purge.mockImplementation(async (ids: string[], apply: boolean) => ({
      applied: apply,
      results: apply
        ? ids.map((id) => ({ product_id: id, sku: 'SKU-P1', status: 'done' }))
        : [
            { product_id: 'P1', sku: 'SKU-P1', status: 'ready', website: true },
            { product_id: 'P3', sku: 'SKU-P3', status: 'ready', website: false },
            { product_id: 'P2', sku: 'SKU-P2', status: 'refused', code: 409, reason: HISTORY },
          ],
    }));
    const onDone = vi.fn();
    render(<PurgeDialog ids={['P1', 'P2']} onClose={() => {}} onDone={onDone} />);
    expect(await screen.findByText(/cannot be undone from the screen/)).toBeTruthy();
    // Website removals are listed separately, in the owner's words.
    expect(screen.getByTestId('purge-website').textContent).toContain('REMOVED FROM THE WEBSITE');
    expect(screen.getByTestId('purge-website').textContent).toContain('SKU-P1');
    expect(screen.getByTestId('purge-website').textContent).not.toContain('SKU-P3');
    expect(screen.getByTestId('purge-refused').textContent).toContain(HISTORY);
    fireEvent.click(screen.getByRole('button', { name: /Delete 2 items permanently/ }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('2 items deleted permanently', true));
    expect(purge).toHaveBeenLastCalledWith(['P1', 'P3'], true);
  });
});

describe('SelectionBar', () => {
  const bar = (canPurge: boolean, archivedView: boolean) =>
    render(
      <SelectionBar
        count={1}
        cap={200}
        canFlag={false}
        canArchive
        canPurge={canPurge}
        archivedView={archivedView}
        flagType="needs_revision"
        onFlagType={() => {}}
        onFlag={() => {}}
        onArchive={() => {}}
        onRestore={() => {}}
        onPurge={() => {}}
        onClear={() => {}}
        busy={false}
      />
    );

  it('shows Delete permanently to SUPERADMIN in the Archived view only', () => {
    const { unmount } = bar(true, true);
    expect(screen.getByRole('button', { name: /Delete permanently/ })).toBeTruthy();
    unmount();
    bar(false, true);
    expect(screen.queryByRole('button', { name: /Delete permanently/ })).toBeNull();
  });

  it('is not offered outside the Archived view', () => {
    bar(true, false);
    expect(screen.queryByRole('button', { name: /Delete permanently/ })).toBeNull();
  });
});
