// ============================================================================
// TopNav — group dropdowns open/close by CLICK/TAP
// ============================================================================
// Regression guard for the prod bug where the top-bar group menus opened on
// mouse HOVER (onPointerEnter) while the trigger's onClick TOGGLED them: on a
// mouse the hover opened it and the same click immediately closed it, and on
// TOUCH (iPad, no hover) the open/close was unreliable -> tapping a group opened
// nothing. The fix removes hover-open so onClick -> toggleGroup is the single
// trigger. These tests drive the TAP path with fireEvent/userEvent CLICKS (never
// hover), so they fail if anyone re-introduces a hover/toggle conflict.
//
// useAuth is mocked to a SUPERADMIN user with hasModuleAccess() === true so every
// nav group is visible; the real navConfig + filterVisibleGroups drive rendering.

import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

// Stable references so TopNav's effects don't churn across renders.
const MOCK_USER = {
  id: 'u1',
  name: 'Super Admin',
  roles: ['SUPERADMIN'],
  activeRole: 'SUPERADMIN',
  activeStoreId: 'BV-BOK-01',
  storeIds: ['BV-BOK-01'],
};
const logout = vi.fn();
const MOCK_AUTH = {
  user: MOCK_USER,
  hasModuleAccess: () => true,
  logout,
};
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => MOCK_AUTH,
}));

import { TopNav } from '../TopNav';

function renderTopNav() {
  return render(
    <MemoryRouter>
      <TopNav brand="bv" />
    </MemoryRouter>,
  );
}

// Group triggers are <button aria-haspopup="true"> with the group title as text.
function trigger(title: string) {
  return screen.getByRole('button', { name: new RegExp(title, 'i') });
}

describe('TopNav brand link + Hub item', () => {
  it('brand logo is a Home link to /dashboard', () => {
    renderTopNav();
    const brand = screen.getByRole('link', { name: /go to Hub/i });
    expect(brand).toHaveAttribute('href', '/dashboard');
    // It carries the brand class (the logo block), not a stray nav item.
    expect(brand).toHaveClass('top-nav-brand');
  });

  it('drops the "Hub" item from the top menu but keeps Notifications', () => {
    renderTopNav();
    // No top-menu link/button labelled exactly "Hub" (the logo provides Home).
    expect(screen.queryByRole('link', { name: /^Hub$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Hub$/i })).not.toBeInTheDocument();
    // Notifications direct link is still present.
    expect(screen.getByRole('link', { name: /Notifications/i })).toBeInTheDocument();
  });
});

describe('TopNav group dropdowns (tap/click)', () => {
  it('(a) renders no dropdown items until a group is clicked', () => {
    renderTopNav();
    // The triggers themselves are present...
    expect(trigger('Sales floor')).toBeInTheDocument();
    // ...but no dropdown menu / menuitems are open initially.
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /POS/i })).not.toBeInTheDocument();
  });

  it('(b) clicking a group trigger shows that group\'s items — the tap path', async () => {
    const user = userEvent.setup();
    renderTopNav();

    expect(screen.queryByRole('menuitem', { name: /POS/i })).not.toBeInTheDocument();

    // CLICK (not hover) — this is the exact interaction that was dead on prod.
    await user.click(trigger('Sales floor'));

    const menu = screen.getByRole('menu', { name: /Sales floor/i });
    expect(menu).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /POS/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /Orders/i })).toBeInTheDocument();
    expect(trigger('Sales floor')).toHaveAttribute('aria-expanded', 'true');
  });

  it('(b-bare) a raw fireEvent.click opens the dropdown (proves no hover needed)', () => {
    renderTopNav();
    expect(screen.queryByRole('menuitem', { name: /POS/i })).not.toBeInTheDocument();
    fireEvent.click(trigger('Sales floor'));
    expect(screen.getByRole('menuitem', { name: /POS/i })).toBeInTheDocument();
  });

  it('(c) clicking the same trigger again hides the items', async () => {
    const user = userEvent.setup();
    renderTopNav();

    await user.click(trigger('Sales floor'));
    expect(screen.getByRole('menuitem', { name: /POS/i })).toBeInTheDocument();

    await user.click(trigger('Sales floor'));
    expect(screen.queryByRole('menuitem', { name: /POS/i })).not.toBeInTheDocument();
    expect(trigger('Sales floor')).toHaveAttribute('aria-expanded', 'false');
  });

  it('(d) clicking a different group switches the open dropdown', async () => {
    const user = userEvent.setup();
    renderTopNav();

    await user.click(trigger('Sales floor'));
    expect(screen.getByRole('menuitem', { name: /POS/i })).toBeInTheDocument();

    // Switch to "Stock & supply" — first group's items vanish, second's appear.
    await user.click(trigger('Stock & supply'));
    expect(screen.queryByRole('menuitem', { name: /POS/i })).not.toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /Inventory/i })).toBeInTheDocument();
  });

  it('(e) clicking a menu item closes the menu', async () => {
    const user = userEvent.setup();
    renderTopNav();

    await user.click(trigger('Sales floor'));
    const item = screen.getByRole('menuitem', { name: /Orders/i });
    expect(item).toBeInTheDocument();

    await user.click(item);
    expect(screen.queryByRole('menuitem', { name: /Orders/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
