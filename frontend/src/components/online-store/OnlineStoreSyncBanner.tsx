// ============================================================================
// IMS 2.0 - Online Store - shared Shopify-sync (publish) banner  (BVI Phase 5)
// ============================================================================
// A prominent, unmistakable banner that states the CURRENT Shopify push posture
// so nobody ever thinks a dry-run went live. It reads GET /online-store/push/status
// (pushApi.getStatus, fail-soft) and renders one of two states:
//
//   DARK (default / not live)  -> a neutral amber banner: "Shopify writes are OFF
//                                 — preview / dry-run only". Lists exactly WHY
//                                 (the three gate components) so the owner can see
//                                 what to flip at the Phase-6 cutover.
//   LIVE (all three gates on)  -> a connected green banner: pushes write to the
//                                 live storefront.
//
// Reused on every Online Store screen that exposes a Publish control (Collections,
// Mega-menu, Design queue) AND on the module shell. It re-fetches on mount and
// exposes an imperative `refresh()` via ref so a page can refresh the counts /
// posture right after a publish. Light theme only.

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useState,
} from 'react';
import { Loader2, ShieldAlert, Zap, RefreshCw } from 'lucide-react';
import { pushApi, type PushStatus, type PushResult } from '../../services/api/onlineStore';

export interface OnlineStoreSyncBannerHandle {
  /** Re-fetch the push posture (call after a publish to refresh the counts). */
  refresh: () => void;
}

interface Props {
  /** Tighten the vertical rhythm when embedded above a dense toolbar. */
  className?: string;
  /** Bumping this value triggers a re-fetch (handy alternative to the ref). */
  refreshKey?: number;
}

/** A short, human gate reason for the DARK state. */
function darkReasons(status: PushStatus | null): string[] {
  const m = status?.mode;
  if (!m) return [];
  const out: string[] = [];
  if (m.writes_enabled === false) out.push('IMS_SHOPIFY_WRITES is off');
  if (m.dispatch_mode && m.dispatch_mode !== 'live') out.push(`dispatch mode is "${m.dispatch_mode}" (needs "live")`);
  if (m.creds_present === false) out.push('Shopify credentials are not set');
  return out;
}

const OnlineStoreSyncBanner = forwardRef<OnlineStoreSyncBannerHandle, Props>(
  function OnlineStoreSyncBanner({ className = '', refreshKey }, ref) {
    const [status, setStatus] = useState<PushStatus | null>(null);
    const [loading, setLoading] = useState(true);

    const load = useCallback(async () => {
      setLoading(true);
      try {
        const s = await pushApi.getStatus();
        setStatus(s);
      } finally {
        setLoading(false);
      }
    }, []);

    useEffect(() => {
      let alive = true;
      pushApi
        .getStatus()
        .then((s) => {
          if (alive) setStatus(s);
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
      return () => {
        alive = false;
      };
    }, [refreshKey]);

    useImperativeHandle(ref, () => ({ refresh: load }), [load]);

    if (loading && !status) {
      return (
        <div
          className={
            'rounded-xl border border-gray-200 bg-white px-4 py-3 flex items-center gap-2 text-sm text-gray-500 ' +
            className
          }
        >
          <Loader2 className="w-4 h-4 animate-spin" /> Checking Shopify sync status…
        </div>
      );
    }

    const live = !!status?.mode?.is_live;

    if (live) {
      return (
        <div
          className={
            'rounded-xl border border-green-200 bg-green-50 px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1 ' +
            className
          }
          role="status"
        >
          <span className="inline-flex items-center gap-1.5 rounded-full bg-green-100 text-green-800 border border-green-200 px-2.5 py-1 text-xs font-semibold">
            <Zap className="w-3.5 h-3.5" /> Shopify writes LIVE
          </span>
          <span className="text-sm text-green-900">
            Publishing here writes to the live storefront.
          </span>
          {status?.mode?.api_version && (
            <span className="text-xs text-green-700/80">API {status.mode.api_version}</span>
          )}
        </div>
      );
    }

    // DARK (default) — make it impossible to mistake a dry-run for a live push.
    const reasons = darkReasons(status);
    return (
      <div
        className={
          'rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 ' + className
        }
        role="status"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 text-amber-900 border border-amber-300 px-2.5 py-1 text-xs font-semibold">
            <ShieldAlert className="w-3.5 h-3.5" /> Shopify writes OFF — preview / dry-run only
          </span>
          <span className="text-sm text-amber-900">
            Publishing here returns a dry-run plan. Nothing reaches the live storefront yet.
          </span>
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="ml-auto inline-flex items-center gap-1 text-xs text-amber-800 hover:text-amber-900 disabled:opacity-50"
            title="Re-check status"
          >
            <RefreshCw className={'w-3.5 h-3.5 ' + (loading ? 'animate-spin' : '')} /> Re-check
          </button>
        </div>
        {reasons.length > 0 && (
          <p className="mt-1.5 text-xs text-amber-800/90">
            Off because: {reasons.join('; ')}. The owner arms live pushes at the storefront cutover.
          </p>
        )}
      </div>
    );
  },
);

export default OnlineStoreSyncBanner;

// ----------------------------------------------------------------------------
// SyncChip — a tiny per-entity "synced / pending" status pill reused on the row
// / card next to a Publish control. Driven by whether the entity already carries
// a Shopify id (synced) and/or is locally modified (pending).
// ----------------------------------------------------------------------------
export function SyncChip({
  synced,
  pending,
  className = '',
}: {
  /** The entity already has a Shopify id (was pushed at least once). */
  synced: boolean;
  /** The entity has local edits not yet pushed (dirty). */
  pending?: boolean;
  className?: string;
}) {
  if (synced && !pending) {
    return (
      <span
        className={
          'inline-flex items-center rounded-full bg-green-100 text-green-800 border border-green-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ' +
          className
        }
        title="Mapped to a Shopify id"
      >
        Synced
      </span>
    );
  }
  if (synced && pending) {
    return (
      <span
        className={
          'inline-flex items-center rounded-full bg-amber-100 text-amber-800 border border-amber-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ' +
          className
        }
        title="Pushed before, but edited since — re-publish to update Shopify"
      >
        Pending changes
      </span>
    );
  }
  return (
    <span
      className={
        'inline-flex items-center rounded-full bg-gray-100 text-gray-600 border border-gray-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ' +
        className
      }
      title="Not yet pushed to Shopify"
    >
      Not pushed
    </span>
  );
}

// ----------------------------------------------------------------------------
// formatPushResult — a consistent toast message for a publish outcome, making
// the SIMULATED-vs-LIVE distinction explicit (so a dry-run is never mistaken for
// a live write) and surfacing the shopify_id when present.
// ----------------------------------------------------------------------------
export function formatPushResult(label: string, r: PushResult): string {
  const where = r.mode === 'LIVE' ? 'LIVE' : 'dry-run (SIMULATED)';
  if (!r.ok) {
    return `${label}: ${where} — ${r.error || r.reason || 'not pushed'}`;
  }
  const idPart = r.shopify_id ? ` · ${r.shopify_id}` : '';
  const actionPart = r.action ? ` (${r.action})` : '';
  return `${label}: ${where}${actionPart}${idPart}`;
}
