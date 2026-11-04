// The forced sign-out (a 401 the client cannot refresh) tells the POS store
// by this event, so it runs THE one sign-out step (park a pending bill, then
// clear the draft). A file of its own: client.ts cannot import the store
// (posStore -> gstRuntime -> client is an import cycle).
export const SIGNED_OUT_EVENT = 'ims:signed-out';

// Ctrl-K recent lists (components/shell/CommandPalette.tsx) hold customer
// names and phones, one list per person + shop + role set (Codex R09). Every
// sign-out drops them all, the old browser-wide key included.
export const PALETTE_RECENT_PREFIX = 'cmdk_recent_v1';

export function dropPaletteRecents(): void {
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k?.startsWith(PALETTE_RECENT_PREFIX)) localStorage.removeItem(k);
    }
  } catch { /* storage unavailable - nothing to drop */ }
}
