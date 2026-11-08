// ============================================================================
// IMS 2.0 - LoginPage: opening it signs no one out (owner ruling 2026-11-07,
// PR #1372). A valid sign-in -- this tab's, or one another tab just made --
// lasts until an explicit sign-out or a successful sign-in as someone else.
// ============================================================================

import { beforeAll, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

beforeAll(() => {
  const store = new Map<string, string>();
  const mock: Storage = {
    get length() { return store.size; },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => store.delete(k),
    setItem: (k: string, v: string) => store.set(k, String(v)),
  };
  Object.defineProperty(window, 'localStorage', { configurable: true, value: mock });
});

vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));

import AuthContext from '../../../context/AuthContext';
import { LoginPage } from '../LoginPage';

describe('opening the login page', () => {
  it('leaves a valid sign-in stored (another tab opening /login)', () => {
    const signedIn = {
      ims_token: 'h.eyJ1c2VyX2lkIjoidTEiLCJzaWQiOiJjMSJ9.s',
      ims_refresh_token: 'r1',
      ims_user: JSON.stringify({ id: 'u1', activeStoreId: 'S1' }),
      ims_active_module: 'pos',
    };
    Object.entries(signedIn).forEach(([k, v]) => localStorage.setItem(k, v));
    const value = { login: vi.fn(), isLoading: false } as unknown as React.ContextType<typeof AuthContext>;
    const view = render(
      <AuthContext.Provider value={value}><MemoryRouter initialEntries={['/login']}><LoginPage /></MemoryRouter></AuthContext.Provider>,
    );
    view.unmount();
    Object.entries(signedIn).forEach(([k, v]) => expect(localStorage.getItem(k)).toBe(v));
  });
});
