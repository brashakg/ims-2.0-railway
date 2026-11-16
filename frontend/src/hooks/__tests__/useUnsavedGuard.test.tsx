// D51 / CAT-04: the shared unsaved-work guard covers every real exit on the
// app's real router (<BrowserRouter>): router navigation (menus, the command
// palette, navigate()), a same-path query switch, browser Back / Forward and
// the page's own navigation after it already asked (ownNavigation).
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserRouter, Link, useLocation, useNavigate } from 'react-router-dom';
import { ownNavigation, useUnsavedGuard } from '../useUnsavedGuard';

function Page() {
  const [dirty, setDirty] = useState(false);
  useUnsavedGuard(dirty);
  const l = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="where">{l.pathname + l.search}</output>
      <button type="button" onClick={() => setDirty(true)}>type</button>
      <button type="button" onClick={() => navigate('/catalog')}>palette catalog</button>
      <button type="button" onClick={() => navigate('/catalog/add?edit=P-B')}>other product</button>
      <button type="button" onClick={() => navigate('/catalog/add?edit=P-A')}>product A</button>
      <button type="button" onClick={() => navigate(-1)}>back button</button>
      <button type="button" onClick={() => ownNavigation(() => navigate('/catalog'))}>saved</button>
      <Link to="/stock">menu stock</Link>
    </>
  );
}
const where = () => screen.getByTestId('where').textContent;
const tick = () => act(() => new Promise((r) => setTimeout(r, 30)));

let confirm: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  window.history.replaceState(null, '', '/start');
  confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
});
afterEach(() => confirm.mockRestore());

const openDirty = async () => {
  render(<BrowserRouter><Page /></BrowserRouter>);
  // A history entry behind the editor, as when it was opened from the list.
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button', { name: 'other product' }));
  confirm.mockClear();
  confirm.mockReturnValue(false);
  fireEvent.click(screen.getByRole('button', { name: 'type' }));
  await tick();
  expect(where()).toBe('/catalog/add?edit=P-B');
};

describe('useUnsavedGuard (D51)', () => {
  it('nothing typed: navigation never asks', async () => {
    render(<BrowserRouter><Page /></BrowserRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'palette catalog' }));
    expect(confirm).not.toHaveBeenCalled();
    expect(where()).toBe('/catalog');
  });

  it('command palette / programmatic navigate asks; Stay keeps the page', async () => {
    await openDirty();
    fireEvent.click(screen.getByRole('button', { name: 'palette catalog' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-B');
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'palette catalog' }));
    expect(where()).toBe('/catalog');
  });

  it('a router link asks exactly once', async () => {
    await openDirty();
    fireEvent.click(screen.getByRole('link', { name: 'menu stock' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-B');
  });

  it('a same-path switch to another ?edit= asks; Stay keeps the address', async () => {
    await openDirty();
    fireEvent.click(screen.getByRole('button', { name: 'product A' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-B');
  });

  it('browser Back asks; Stay puts the editor back, Leave goes back', async () => {
    await openDirty();
    act(() => { window.history.back(); });
    await tick();
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-B');
    await tick();
    expect(window.location.pathname + window.location.search).toBe('/catalog/add?edit=P-B');
    // Still guarded after staying, and Leave really leaves.
    confirm.mockReturnValue(true);
    act(() => { window.history.back(); });
    await tick();
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(where()).toBe('/start');
  });

  it('an in-app Back button (navigate(-1)) asks once', async () => {
    await openDirty();
    fireEvent.click(screen.getByRole('button', { name: 'back button' }));
    await tick();
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-B');
  });

  it("the page's own move after a save passes without a second question", async () => {
    await openDirty();
    fireEvent.click(screen.getByRole('button', { name: 'saved' }));
    expect(confirm).not.toHaveBeenCalled();
    expect(where()).toBe('/catalog');
  });
});
