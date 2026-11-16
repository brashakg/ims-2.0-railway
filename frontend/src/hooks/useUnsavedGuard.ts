import { useContext, useEffect } from 'react';
import { UNSAFE_NavigationContext } from 'react-router-dom';
import type { Navigator } from 'react-router-dom';

const LEAVE_PROMPT = 'You have unsaved changes on this page - leave without saving?';

// One guard state for the whole app: several sections of one page may each
// be dirty, and the staff member is asked once per exit.
let dirtyCount = 0;
// A page's OWN navigation (after a successful save, or after it already
// asked its own question) passes: see ownNavigation().
let ownDepth = 0;
// navigate(-1) already asked: its popstate passes.
let allowPop = false;
// The history.go() that puts the entry back after Stay: its popstate is ours.
let restoring = false;
// The router's history index of the address the staff member is on.
let atIdx: number | undefined;
const idx = () => (window.history.state as { idx?: number } | null)?.idx;

/** Run a navigation the page itself decided on (it already asked, or the
 *  work was just saved): the unsaved-work guard lets it through. */
export function ownNavigation<T>(fn: () => T): T {
  ownDepth += 1;
  try {
    return fn();
  } finally {
    ownDepth -= 1;
  }
}

const leaveOk = () => ownDepth > 0 || dirtyCount === 0 || window.confirm(LEAVE_PROMPT);

// ---- browser Back / Forward ------------------------------------------------
// Registered when this module loads -- App.tsx imports it before <BrowserRouter>
// mounts -- so it runs BEFORE the router's own popstate listener (a browser
// calls a target's listeners in the order they were added; Chrome does so
// even for a capture listener on window) and stopImmediatePropagation keeps
// the router from leaving; Stay puts the history entry back.
if (typeof window !== 'undefined') {
  window.addEventListener('popstate', (e: PopStateEvent) => {
    if (restoring) { restoring = false; e.stopImmediatePropagation(); return; }
    const toIdx = (e.state as { idx?: number } | null)?.idx;
    if (allowPop || dirtyCount === 0 || typeof atIdx !== 'number' || typeof toIdx !== 'number') {
      // ponytail: an entry the router did not write (no idx) cannot be put
      // back, so it passes; every router entry carries one.
      allowPop = false;
      atIdx = toIdx;
      return;
    }
    if (window.confirm(LEAVE_PROMPT)) { atIdx = toIdx; return; }
    e.stopImmediatePropagation();
    restoring = true;
    window.history.go(atIdx - toIdx);
  }, true);
}

// ---- router navigations ----------------------------------------------------
// The router's history object: its `location` is the address right now.
type HistoryNavigator = Navigator & { location?: { pathname: string; search: string } };
const wrapped = new WeakSet<object>();
function guardNavigator(nav: HistoryNavigator) {
  if (wrapped.has(nav)) return;
  wrapped.add(nav);
  const { push, replace, go } = nav;
  const here = () => {
    const l = nav.location ?? window.location;
    return l.pathname + l.search;
  };
  const stays = (to: Parameters<Navigator['push']>[0]) =>
    (typeof to === 'string' ? to.split('#')[0] : (to.pathname ?? '') + (to.search ?? '')) === here();
  nav.push = (to, state, opts) => {
    if (stays(to) || leaveOk()) { push.call(nav, to, state, opts); atIdx = idx(); }
  };
  nav.replace = (to, state, opts) => {
    if (stays(to) || leaveOk()) { replace.call(nav, to, state, opts); atIdx = idx(); }
  };
  nav.go = (delta) => {
    if (delta !== 0 && !leaveOk()) return;
    if (dirtyCount > 0) allowPop = true;
    go.call(nav, delta);
  };
}

/**
 * Unsaved-work guard (D51). The app runs on <BrowserRouter> (not a data
 * router), so react-router's useBlocker is unavailable. While `dirty`, EVERY
 * real exit asks first:
 *  - closing / reloading the tab (beforeunload);
 *  - any router navigation that changes the address -- links, menus, the
 *    command palette, navigate(), navigate(-1), a same-path query switch
 *    (?edit=A -> B) -- through the router's navigator;
 *  - browser Back / Forward (the popstate listener above).
 * A plain <a href> to another page is a full page load, so beforeunload asks.
 */
export function useUnsavedGuard(dirty: boolean) {
  const nav = useContext(UNSAFE_NavigationContext)?.navigator as HistoryNavigator | undefined;
  useEffect(() => {
    if (nav) guardNavigator(nav);
  }, [nav]);
  useEffect(() => {
    if (!dirty) return;
    dirtyCount += 1;
    atIdx = idx();
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      dirtyCount -= 1;
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, [dirty]);
}
