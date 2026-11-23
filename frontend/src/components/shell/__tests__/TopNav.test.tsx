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
    expect(screen.queryByRole('menuitem', { name: /^Billing$/i })).not.toBeInTheDocument();
  });

  it('(b) clicking a group trigger shows that group\'s items — the tap path', async () => {
    const user = userEvent.setup();
    renderTopNav();

    expect(screen.queryByRole('menuitem', { name: /^Billing$/i })).not.toBeInTheDocument();

    // CLICK (not hover) — this is the exact interaction that was dead on prod.
    await user.click(trigger('Sales floor'));

    const menu = screen.getByRole('menu', { name: /Sales floor/i });
    expect(menu).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /^Billing$/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /Orders/i })).toBeInTheDocument();
    expect(trigger('Sales floor')).toHaveAttribute('aria-expanded', 'true');
  });

  it('(b-bare) a raw fireEvent.click opens the dropdown (proves no hover needed)', () => {
    renderTopNav();
    expect(screen.queryByRole('menuitem', { name: /^Billing$/i })).not.toBeInTheDocument();
    fireEvent.click(trigger('Sales floor'));
    expect(screen.getByRole('menuitem', { name: /^Billing$/i })).toBeInTheDocument();
  });

  it('(c) clicking the same trigger again hides the items', async () => {
    const user = userEvent.setup();
    renderTopNav();

    await user.click(trigger('Sales floor'));
    expect(screen.getByRole('menuitem', { name: /^Billing$/i })).toBeInTheDocument();

    await user.click(trigger('Sales floor'));
    expect(screen.queryByRole('menuitem', { name: /^Billing$/i })).not.toBeInTheDocument();
    expect(trigger('Sales floor')).toHaveAttribute('aria-expanded', 'false');
  });

  it('(d) clicking a different group switches the open dropdown', async () => {
    const user = userEvent.setup();
    renderTopNav();

    await user.click(trigger('Sales floor'));
    expect(screen.getByRole('menuitem', { name: /^Billing$/i })).toBeInTheDocument();

    // Switch to "Stock & supply" — first group's items vanish, second's appear.
    await user.click(trigger('Stock & supply'));
    expect(screen.queryByRole('menuitem', { name: /^Billing$/i })).not.toBeInTheDocument();
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

  it('(f) scrolling INSIDE an open dropdown keeps it open (long-menu scroll bug)', async () => {
    const user = userEvent.setup();
    renderTopNav();
    await user.click(trigger('Stock & supply'));
    const menu = screen.getByRole('menu', { name: /Stock & supply/i });
    fireEvent.wheel(menu, { deltaY: 120 });
    fireEvent.scroll(menu);
    expect(screen.getByRole('menu', { name: /Stock & supply/i })).toBeInTheDocument();
    // A page-body scroll (below the fixed bar) does not move the anchor either.
    const body = document.createElement('div');
    document.body.appendChild(body);
    fireEvent.scroll(body);
    expect(screen.getByRole('menu', { name: /Stock & supply/i })).toBeInTheDocument();
    body.remove();
  });

  it('(g) scrolling the menu bar itself (anchor moves) closes the dropdown', async () => {
    const user = userEvent.setup();
    const { container } = renderTopNav();
    await user.click(trigger('Stock & supply'));
    fireEvent.scroll(container.querySelector('.top-nav-menu')!);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('(h) Esc closes the open dropdown', async () => {
    const user = userEvent.setup();
    renderTopNav();
    await user.click(trigger('Stock & supply'));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});

// R11 (audit): ArrowDown on a focused group trigger threw "Cannot read
// properties of null (reading 'parentElement')" because the frame callback
// read React's event.currentTarget after the handler returned; focus stayed
// on the trigger.
describe('TopNav keyboard (R11)', () => {
  const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r(null)));

  it.each(['{ArrowDown}', '{Enter}', ' '])('%s opens the group and focuses its first entry without an error', async (key) => {
    const user = userEvent.setup();
    const errors: unknown[] = [];
    const onErr = (e: ErrorEvent) => { errors.push(e.error ?? e.message); e.preventDefault(); };
    window.addEventListener('error', onErr);
    try {
      renderTopNav();
      trigger('Operations').focus();
      await user.keyboard(key);
      await nextFrame();
      const items = screen.getAllByRole('menuitem');
      expect(document.activeElement).toBe(items[0]);
      expect(errors).toEqual([]);
    } finally {
      window.removeEventListener('error', onErr);
    }
  });

  it('arrows move through the entries (wrapping), Esc closes and returns focus to the trigger', async () => {
    const user = userEvent.setup();
    renderTopNav();
    trigger('Operations').focus();
    await user.keyboard('{ArrowDown}');
    await nextFrame();
    const items = screen.getAllByRole('menuitem');
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(items[1]);
    await user.keyboard('{ArrowUp}{ArrowUp}');
    expect(document.activeElement).toBe(items[items.length - 1]);
    await user.keyboard('{Home}');
    expect(document.activeElement).toBe(items[0]);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(document.activeElement).toBe(trigger('Operations'));
  });
});

// R38 (audit): at 1024px the bar hid groups past its right edge with the
// scrollbar hidden and nothing saying so. jsdom has no layout, so the bar's
// sizes are given here: 1000px of groups in a 600px-wide bar.
describe('TopNav overflow arrows (R38)', () => {
  const proto = HTMLElement.prototype;
  const saved = {
    sw: Object.getOwnPropertyDescriptor(proto, 'scrollWidth'),
    cw: Object.getOwnPropertyDescriptor(proto, 'clientWidth'),
  };
  function sized(scrollWidth: number, clientWidth: number) {
    Object.defineProperty(proto, 'scrollWidth', {
      configurable: true,
      get() { return (this as HTMLElement).classList?.contains('top-nav-menu') ? scrollWidth : 0; },
    });
    Object.defineProperty(proto, 'clientWidth', {
      configurable: true,
      get() { return (this as HTMLElement).classList?.contains('top-nav-menu') ? clientWidth : 0; },
    });
  }
  function restore() {
    if (saved.sw) Object.defineProperty(proto, 'scrollWidth', saved.sw);
    if (saved.cw) Object.defineProperty(proto, 'clientWidth', saved.cw);
  }

  it('a bar wider than its space shows a "more" arrow that scrolls to the hidden groups', () => {
    sized(1000, 600);
    try {
      const { container } = renderTopNav();
      const bar = container.querySelector<HTMLElement>('.top-nav-menu')!;
      expect(screen.queryByRole('button', { name: 'Show earlier menus' })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Show more menus' }));
      expect(bar.scrollLeft).toBeGreaterThan(0);
      fireEvent.scroll(bar);
      expect(screen.getByRole('button', { name: 'Show earlier menus' })).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Show earlier menus' }));
      expect(bar.scrollLeft).toBe(0);
    } finally {
      restore();
    }
  });

  it('a bar that fits shows no arrows', () => {
    sized(600, 600);
    try {
      renderTopNav();
      expect(screen.queryByRole('button', { name: /Show (more|earlier) menus/ })).not.toBeInTheDocument();
    } finally {
      restore();
    }
  });
});
