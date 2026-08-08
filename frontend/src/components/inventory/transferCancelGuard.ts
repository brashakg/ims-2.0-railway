// ============================================================================
// IMS 2.0 - Fresh-state guard for transfer cancellation
// ============================================================================
// Closes the stale-tab race on Cancel (PR #959 round 2): the Cancel button's
// visibility is computed from client state loaded at mount, so a tab opened
// while a transfer was still pre-ship (e.g. 'packed') can outlive a ship +
// receive performed in another session. Cancelling then would flip a RECEIVED
// transfer to CANCELLED with no stock reversal and permanently skip the
// inter-entity/inter-state GST deemed-supply mirror invoice (booked only on
// /complete).
//
// Contract: ALWAYS re-fetch the transfer from the server and re-run the
// canCancel gate on the FRESH document BEFORE posting the cancel. If the fresh
// status is no longer cancellable — or the fresh state cannot be verified at
// all — the cancel POST is never sent. Fail-closed by design: no verification,
// no POST.
//
// Dependency-injected (getTransfer / cancelTransfer passed in) so the guard is
// unit-testable without mounting the component.

import { canCancel, type TransferActor } from './transferPermissions';

export type FreshCancelOutcome =
  | { ok: true; response: unknown }
  | {
      ok: false;
      reason: 'stale_not_cancellable';
      /** The server-side status that blocked the cancel. */
      freshStatus: string;
      /** The fresh server doc, so the caller can re-render from truth. */
      freshTransfer: Record<string, unknown> | null;
    }
  | { ok: false; reason: 'verify_failed' };

export async function cancelWithFreshCheck(opts: {
  actor: TransferActor;
  transferId: string;
  reason: string;
  /** GET the single transfer fresh; may return {transfer: {...}} or the doc. */
  getTransfer: (id: string) => Promise<any>;
  /** The actual cancel POST — only invoked after the fresh gate passes. */
  cancelTransfer: (id: string, reason: string) => Promise<any>;
}): Promise<FreshCancelOutcome> {
  let fresh: any;
  try {
    fresh = await opts.getTransfer(opts.transferId);
  } catch {
    // Cannot verify the current server state -> never POST the cancel.
    return { ok: false, reason: 'verify_failed' };
  }

  // The backend returns {transfer: {...}}; tolerate a bare doc too.
  const doc = fresh?.transfer ?? fresh;
  if (!doc || typeof doc !== 'object' || !doc.status) {
    return { ok: false, reason: 'verify_failed' };
  }

  if (!canCancel(opts.actor, doc)) {
    return {
      ok: false,
      reason: 'stale_not_cancellable',
      freshStatus: String(doc.status ?? ''),
      freshTransfer: doc,
    };
  }

  // Fresh gate passed — the transfer is still pre-ship on the server. (The
  // backend now enforces the same allowlist, so even a race between this GET
  // and the POST is rejected server-side.)
  const response = await opts.cancelTransfer(opts.transferId, opts.reason);
  return { ok: true, response };
}
