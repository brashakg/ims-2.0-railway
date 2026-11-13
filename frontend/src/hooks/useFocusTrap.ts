// Keep keyboard focus inside an open modal (R29): opening moves focus to
// `initial` (else the first control), Tab / Shift+Tab wrap within the
// container, focus that has escaped is pulled back in, and closing the modal
// returns focus to whatever opened it. Do not also use autoFocus inside the
// modal: it would move focus before the opener is recorded.
import { useEffect, type RefObject } from 'react';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.style.display !== 'none' && !el.closest('[hidden]'),
  );
}

export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  initial?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    if (!active) return;
    const opener = document.activeElement as HTMLElement | null;
    const start = initial?.current ?? (ref.current ? focusables(ref.current)[0] : undefined);
    start?.focus();
    const onKey = (e: KeyboardEvent) => {
      const root = ref.current;
      if (e.key !== 'Tab' || !root) return;
      const items = focusables(root);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const current = document.activeElement;
      if (!root.contains(current)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && current === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && current === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      if (opener && document.contains(opener)) opener.focus();
    };
  }, [active, ref, initial]);
}
