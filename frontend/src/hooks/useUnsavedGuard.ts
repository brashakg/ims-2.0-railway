import { useEffect } from 'react';

/**
 * Unsaved-edit guard. The app runs on <BrowserRouter> (not a data router), so
 * react-router's useBlocker is unavailable; this follows the codebase's own
 * window.confirm pattern. While `dirty`: closing/reloading the tab prompts via
 * beforeunload, and a click on any in-app link (the settings rail included)
 * asks before leaving.
 */
export function useUnsavedGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    const onClick = (e: MouseEvent) => {
      // Ctrl/Cmd/Shift/Alt or a non-primary button opens a new tab/window (or
      // downloads) - this page is not left, so no prompt.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      const a = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!a || a.target === '_blank' || a.hasAttribute('download') || a.getAttribute('href')?.startsWith('#')) return;
      // mailto:/tel: and a link back to this very page do not leave it.
      if (a.protocol !== 'http:' && a.protocol !== 'https:') return;
      if (a.pathname === window.location.pathname) return;
      if (!window.confirm('You have unsaved changes on this page - leave without saving?')) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onClick, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      document.removeEventListener('click', onClick, true);
    };
  }, [dirty]);
}
