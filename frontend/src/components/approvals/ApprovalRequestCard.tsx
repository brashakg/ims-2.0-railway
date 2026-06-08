// ============================================================================
// IMS 2.0 - E4 Approval request card
// ============================================================================
// One row in the approver inbox / history. Restrained, light, single-accent;
// colour is used ONLY for semantic status + an urgency cue on the live expiry
// countdown. The two actions (Approve / Reject) are surfaced as buttons on a
// pending card and bubble up to the parent, which opens the PIN modal.

import { useNow } from '../../hooks/useNow';
import { formatDateTimeIST, toDate } from '../../utils/datetime';
import type { ApprovalRequest } from '../../services/api/approvals';

// ----------------------------------------------------------------------------
// Shared presentation helpers (exported — reused by the pages)
// ----------------------------------------------------------------------------

const ACTION_LABELS: Record<string, string> = {
  discount_override: 'Discount Override',
  refund: 'Refund',
  journal_entry: 'Journal Entry',
  profile_merge: 'Profile Merge',
  petty_cash: 'Petty Cash',
  endless_aisle: 'Endless Aisle',
  rtv: 'Return to Vendor',
};

export function actionLabel(actionType: string): string {
  return (
    ACTION_LABELS[actionType] ||
    actionType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

/** Semantic status chip styling. Neutral by default; colour carries meaning. */
const STATUS_STYLES: Record<string, string> = {
  REQUESTED: 'bg-amber-50 text-amber-700 border-amber-200',
  APPROVED: 'bg-green-50 text-green-700 border-green-200',
  CONSUMED: 'bg-green-50 text-green-700 border-green-200',
  REJECTED: 'bg-red-50 text-red-700 border-red-200',
  EXPIRED: 'bg-gray-100 text-gray-600 border-gray-200',
};

export function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] || 'bg-gray-100 text-gray-600 border-gray-200';
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${cls}`}
    >
      {status}
    </span>
  );
}

/** ₹ in Indian grouping, or an em-dash when no amount drives the request. */
export function formatRupees(amount: number | null | undefined): string {
  if (amount == null) return '—';
  return `₹${amount.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

/** Human countdown to a deadline relative to `now`. Returns null once past. */
export function remainingLabel(expiresAt: string | null, now: Date): string | null {
  const d = toDate(expiresAt);
  if (!d) return null;
  const ms = d.getTime() - now.getTime();
  if (ms <= 0) return null;
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

// ----------------------------------------------------------------------------
// Card
// ----------------------------------------------------------------------------

interface Props {
  request: ApprovalRequest;
  /** Show Approve / Reject actions (pending inbox only). */
  showActions?: boolean;
  onApprove?: (request: ApprovalRequest) => void;
  onReject?: (request: ApprovalRequest) => void;
}

export function ApprovalRequestCard({
  request,
  showActions = false,
  onApprove,
  onReject,
}: Props) {
  // 1s tick only matters when a live countdown is visible (pending rows).
  const now = useNow(showActions ? 1000 : 60_000);
  const isRequested = request.status === 'REQUESTED';
  const remaining = isRequested ? remainingLabel(request.expires_at, now) : null;

  // Urgency cue: amber under 10m, red under 2m. Colour-only, semantic.
  let countdownCls = 'text-gray-500';
  const expDate = toDate(request.expires_at);
  if (isRequested && expDate) {
    const minsLeft = (expDate.getTime() - now.getTime()) / 60000;
    if (minsLeft <= 2) countdownCls = 'text-red-600 font-medium';
    else if (minsLeft <= 10) countdownCls = 'text-amber-600 font-medium';
  }

  const ctxSummary = summariseContext(request.context);

  return (
    <div className="border border-gray-200 rounded-lg bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-gray-900">
              {actionLabel(request.action_type)}
            </span>
            <StatusBadge status={request.status} />
            {request.maker_checker && (
              <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium border bg-gray-50 text-gray-600 border-gray-200">
                Maker-checker
              </span>
            )}
          </div>
          <div className="mt-1 text-xs text-gray-500">
            Requested by{' '}
            <span className="text-gray-700">{request.requested_by || '—'}</span>
            {request.store_id && (
              <>
                {' · '}
                <span className="text-gray-700">{request.store_id}</span>
              </>
            )}
            {' · '}
            {formatDateTimeIST(request.created_at)}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="font-mono text-sm text-gray-900">
            {formatRupees(request.amount)}
          </div>
          {isRequested &&
            (remaining ? (
              <div className={`text-xs mt-0.5 ${countdownCls}`}>
                {remaining} left
              </div>
            ) : (
              <div className="text-xs mt-0.5 text-red-600 font-medium">
                Expiring…
              </div>
            ))}
        </div>
      </div>

      {request.reason && (
        <p className="mt-2 text-sm text-gray-700 leading-relaxed">
          {request.reason}
        </p>
      )}
      {ctxSummary && (
        <p className="mt-1 text-xs text-gray-500 font-mono break-words">
          {ctxSummary}
        </p>
      )}
      {request.status === 'REJECTED' && request.reject_reason && (
        <p className="mt-1 text-xs text-red-600">
          Rejected: {request.reject_reason}
        </p>
      )}

      {showActions && isRequested && (
        <div className="mt-3 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => onReject?.(request)}
            className="inline-flex items-center px-3 py-1.5 text-xs font-medium rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-colors"
          >
            Reject
          </button>
          <button
            type="button"
            onClick={() => onApprove?.(request)}
            className="inline-flex items-center px-3 py-1.5 text-xs font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 transition-colors"
          >
            Approve
          </button>
        </div>
      )}
    </div>
  );
}

/** Compact one-line summary of the action-specific context object. */
function summariseContext(context?: Record<string, unknown>): string | null {
  if (!context) return null;
  const keys = Object.keys(context);
  if (keys.length === 0) return null;
  const parts: string[] = [];
  for (const k of keys.slice(0, 4)) {
    const v = context[k];
    if (v == null || typeof v === 'object') continue;
    parts.push(`${k}: ${String(v)}`);
  }
  return parts.length ? parts.join('  ·  ') : null;
}

export default ApprovalRequestCard;
