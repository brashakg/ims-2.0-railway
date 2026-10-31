// ============================================================================
// Delete permanently (owner 2026-10-24): an ARCHIVED product, SUPERADMIN only
// ============================================================================
// The dialog asks the server first (dry run): it names the SKUs that go, says
// it cannot be undone, lists those also REMOVED FROM THE WEBSITE separately,
// and shows each refusal. After the delete it STAYS OPEN with every row's
// result (review of #1273: a second refusal or a part-delete was lost behind
// a one-line toast). A timed-out delete keeps running on the server: the
// dialog says so and never offers the button again.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api/catalog', () => ({
  catalogBulkApi: { purge: vi.fn() },
}));

import { catalogBulkApi, type BulkRowResult } from '../../../services/api/catalog';
import { PURGE_TIMEOUT_MESSAGE } from '../../../services/api/client';
import { CHECK_TIMEOUT_MESSAGE, PurgeDialog, SelectionBar } from '../CatalogBulkActions';

const purge = catalogBulkApi.purge as unknown as ReturnType<typeof vi.fn>;
const HISTORY =
  'It has sales/stock/purchase history (stock units), so it stays archived to keep the records complete.';
const SHOPIFY_NO =
  'The website (Shopify) did not remove it, so it stays archived: the listing holds other colours';
const PART =
  'Deleting stopped part-way. Nothing is lost: the backup holds every record. Press Delete permanently again to finish.';

/** The server's answer, per requested id (never a product it was not asked for). */
function server(dry: Record<string, Partial<BulkRowResult>>, applied: Record<string, Partial<BulkRowResult>> = {}) {
  purge.mockImplementation(async (ids: string[], apply: boolean) => ({
    applied: apply,
    results: ids.map((id) => ({
      product_id: id,
      sku: `SKU-${id}`,
      status: apply ? 'done' : 'ready',
      ...(apply ? applied[id] : dry[id]),
    })),
  }));
}

beforeEach(() => {
  purge.mockReset();
});

describe('PurgeDialog', () => {
  it('names the SKUs, lists website removals separately and each refusal', async () => {
    server({ P1: { website: true }, P2: { status: 'refused', code: 409, reason: HISTORY }, P3: {} });
    render(<PurgeDialog ids={['P1', 'P2', 'P3']} onClose={() => {}} onDone={() => {}} />);
    expect(await screen.findByText(/cannot be undone from the screen/)).toBeTruthy();
    const web = screen.getByTestId('purge-website').textContent || '';
    expect(web).toContain('REMOVED FROM THE WEBSITE');
    expect(web).toContain('SKU-P1');
    expect(web).not.toContain('SKU-P3');
    expect(screen.getByTestId('purge-refused').textContent).toContain(HISTORY);
    expect(purge).toHaveBeenCalledWith(['P1', 'P2', 'P3'], false);
  });

  it('after the delete it stays open with EVERY row, and Close reloads the list', async () => {
    server({ P1: {}, P2: {}, P3: {} }, {
      P2: { status: 'refused', code: 502, reason: SHOPIFY_NO },
      P3: { status: 'refused', code: 503, reason: PART },
    });
    const onDone = vi.fn();
    render(<PurgeDialog ids={['P1', 'P2', 'P3']} onClose={() => {}} onDone={onDone} />);
    fireEvent.click(await screen.findByRole('button', { name: /Delete 3 items permanently/ }));
    const out = await screen.findByTestId('purge-results');
    expect(purge).toHaveBeenLastCalledWith(['P1', 'P2', 'P3'], true);
    expect(out.textContent).toContain('SKU-P1');
    expect(out.textContent).toContain(SHOPIFY_NO);
    expect(out.textContent).toContain(PART); // the second refusal is on screen too
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /permanently/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onDone).toHaveBeenCalledWith('1 item deleted permanently - 2 items not deleted (see the list)', false);
  });

  it('nothing can go: no delete button to press', async () => {
    server({ P2: { status: 'refused', code: 409, reason: HISTORY } });
    render(<PurgeDialog ids={['P2']} onClose={() => {}} onDone={() => {}} />);
    expect(await screen.findByText('None of them can be deleted permanently.')).toBeTruthy();
    expect((screen.getByRole('button', { name: /Delete 0 items permanently/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('a dry run the server refuses shows its reason, nothing to press', async () => {
    purge.mockRejectedValue(new Error('Could not read the catalogue right now. Try again in a minute.'));
    render(<PurgeDialog ids={['P1']} onClose={() => {}} onDone={() => {}} />);
    expect((await screen.findByRole('alert')).textContent).toContain('Could not read the catalogue');
    expect((screen.getByRole('button', { name: /permanently/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('a timed-out delete says it is still running and is never offered again', async () => {
    server({ P1: {} });
    const onDone = vi.fn();
    render(<PurgeDialog ids={['P1']} onClose={() => {}} onDone={onDone} />);
    const button = await screen.findByRole('button', { name: /Delete 1 item permanently/ });
    purge.mockRejectedValueOnce(new Error(PURGE_TIMEOUT_MESSAGE));
    fireEvent.click(button);
    expect((await screen.findByRole('alert')).textContent).toBe(PURGE_TIMEOUT_MESSAGE);
    expect(screen.queryByRole('button', { name: /permanently/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(PURGE_TIMEOUT_MESSAGE, false));
  });
});

describe('PurgeDialog while the delete runs (review 3)', () => {
  it('Escape and the backdrop do nothing mid-delete; the results still arrive', async () => {
    server({ P1: {} });
    let finish: (v: unknown) => void = () => {};
    const onClose = vi.fn();
    const onDone = vi.fn();
    render(<PurgeDialog ids={['P1']} onClose={onClose} onDone={onDone} />);
    const button = await screen.findByRole('button', { name: /Delete 1 item permanently/ });
    purge.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    fireEvent.click(button);
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByRole('dialog').parentElement as HTMLElement); // the backdrop
    expect(onClose).not.toHaveBeenCalled();
    finish({ applied: true, results: [{ product_id: 'P1', sku: 'SKU-P1', status: 'done' }] });
    expect((await screen.findByTestId('purge-results')).textContent).toContain('SKU-P1');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onDone).toHaveBeenCalledWith('1 item deleted permanently', true);
  });

  it('a slow CHECK says so, never that a delete is still running', async () => {
    purge.mockRejectedValue(new Error(PURGE_TIMEOUT_MESSAGE));
    render(<PurgeDialog ids={['P1']} onClose={() => {}} onDone={() => {}} />);
    expect((await screen.findByRole('alert')).textContent).toBe(CHECK_TIMEOUT_MESSAGE);
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
