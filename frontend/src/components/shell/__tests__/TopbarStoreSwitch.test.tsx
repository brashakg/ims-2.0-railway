// R41 (Codex audit 2026-11-04): the topbar's "Switch store" confirmation must
// stay open until the server confirms the switch. A refused switch shows its
// error in the dialog and offers Try again; nothing on screen changes shop.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const setActiveStore = vi.fn(async (_id: string) => true);
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'u1', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', storeIds: ['S1', 'S2'], activeStoreId: 'S1' },
    setActiveRole: vi.fn(),
    setActiveStore,
    hasRole: () => false,
  }),
}));
vi.mock('../../../services/api', () => ({
  storeApi: { getStores: vi.fn(async () => ({ stores: [
    { store_id: 'S1', store_name: 'Shop One' }, { store_id: 'S2', store_name: 'Shop Two' },
  ] })) },
}));
vi.mock('../NotificationBell', () => ({ NotificationBell: () => null }));
vi.mock('../CommandPalette', () => ({ CommandPalette: () => null }));

import { Topbar } from '../Topbar';

const openSwitchTo = async (name: RegExp) => {
  render(<MemoryRouter><Topbar /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { expanded: false, name: /Shop One|S1/ }));
  fireEvent.click(await screen.findByRole('option', { name }));
  return screen.getByRole('dialog', { name: 'Confirm store switch' });
};

beforeEach(() => setActiveStore.mockClear());

describe('Topbar store switch', () => {
  it('a refused switch keeps the dialog with an error and Try again', async () => {
    setActiveStore.mockResolvedValueOnce(false);
    const dialog = await openSwitchTo(/Shop Two/);
    fireEvent.click(screen.getByRole('button', { name: 'Switch store' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not switch to .*Shop Two/);
    expect(dialog).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(setActiveStore).toHaveBeenCalledTimes(2);
  });

  it('a confirmed switch closes the dialog', async () => {
    await openSwitchTo(/Shop Two/);
    fireEvent.click(screen.getByRole('button', { name: 'Switch store' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(setActiveStore).toHaveBeenCalledWith('S2');
  });
});
