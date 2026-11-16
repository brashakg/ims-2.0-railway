import { useContext, useEffect } from 'react';
import { UNSAFE_NavigationContext } from 'react-router-dom';
import type { Navigator } from 'react-router-dom';

const LEAVE_PROMPT = 'You have unsaved changes on this page - leave without saving?';

// A page's OWN navigation (after a successful save, or after it already
// asked its own question) passes the guard: see ownNavigation().
let ownDepth = 0;

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

// The router's history object: its `location` is the address right now.
type GuardedNavigator = Navigator & { location?: { pathname: string; search: string } };

/**
 * Unsaved-work guard (D51). The app runs on <BrowserRouter> (not a data
 * router), so react-router's useBlocker is unavailable. While `dirty`, EVERY
 * real exit asks first:
 *  - closing / reloading the tab (beforeunload);
 *  - any router navigation that changes the address -- links, menus, the
 *    command palette, navigate(), a same-path query switch (?edit=A -> B) --
 *    by wrapping the router's navigator push / replace / go;
 *  - browser Back / Forward: a capture-phase popstate listener runs before the
 *    router's own, and Stay puts the history entry back.
 * A plain <a href> to another page is a full page load, so beforeunload asks.
 */
export function useUnsavedGuard(dirty: boolean) {
  const nav = useContext(UNSAFE_NavigationContext)?.navigator as GuardedNavigator | undefined;

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', onBeforeUnload);

    // ---- router navigations --------------------------------------------
    const here = () => {
      const l = nav?.location ?? window.location;
      return l.pathname + l.search;
    };
    const target = (to: Parameters<Navigator['push']>[0]) => {
      if (typeof to === 'string') return to.split('#')[0];
      return (to.pathname ?? '') + (to.search ?? '');
    };
    let allowPop = false;
    const idx = () => (window.history.state as { idx?: number } | null)?.idx;
    let atIdx = idx();
    const orig = nav ? { push: nav.push, replace: nav.replace, go: nav.go } : null;
    if (nav && orig) {
      const ask = (to: Parameters<Navigator['push']>[0]) =>
        ownDepth > 0 || target(to) === here() || window.confirm(LEAVE_PROMPT);
      nav.push = (to, state, opts) => {
        if (ask(to)) { orig.push.call(nav, to, state, opts); atIdx = idx(); }
      };
      nav.replace = (to, state, opts) => {
        if (ask(to)) { orig.replace.call(nav, to, state, opts); atIdx = idx(); }
      };
      nav.go = (delta) => {
        if (delta !== 0 && ownDepth === 0 && !window.confirm(LEAVE_PROMPT)) return;
        allowPop = true;
        orig.go.call(nav, delta);
      };
    }

    // ---- browser Back / Forward ----------------------------------------
    // Capture on window runs before the router's (bubble) popstate listener;
    // stopImmediatePropagation keeps the router from leaving.
    let restoring = false;
    const onPopState = (e: PopStateEvent) => {
      if (restoring) { restoring = false; e.stopImmediatePropagation(); return; }
      const toIdx = (e.state as { idx?: number } | null)?.idx;
      if (allowPop || typeof atIdx !== 'number' || typeof toIdx !== 'number') {
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
    };
    window.addEventListener('popstate', onPopState, true);

    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      window.removeEventListener('popstate', onPopState, true);
      if (nav && orig) Object.assign(nav, orig);
    };
  }, [dirty, nav]);
}
