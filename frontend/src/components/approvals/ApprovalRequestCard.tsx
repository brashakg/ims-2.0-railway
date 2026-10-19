// ============================================================================
// IMS 2.0 - E4 Approval request card
// ============================================================================
// One row in the approver inbox / history. Restrained, light, single-accent;
// colour is used ONLY for semantic status + an urgency cue on the live expiry
// countdown. The two actions (Approve / Reject) are surfaced as buttons on a
// pending card and bubble up to the parent, which opens the PIN modal.

import { useNow } from '../../hooks/useNow';
import { useAuth } from '../../context/AuthContext';
import { COST_ROLES } from '../common/CostCell';
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
  RETURN_SERIAL_OVERRIDE: 'Return Serial Override',
  REFUND_APPROVAL_MATRIX: 'Refund Approval',
  leave_approval: 'Leave Approval',
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

/** Action types whose amount is supplier money (owner ruling 2026-10-01): an
 *  'rtv' request's amount is the credit a supplier gave us on a vendor RMA. */
const SUPPLIER_MONEY_ACTIONS = new Set(['rtv']);

/** The amount this reader may see on a request: a supplier-money amount is
 *  COST_ROLES' alone, so anyone else gets null (formatRupees shows an
 *  em-dash). The server already drops it for them; this keeps a stale or wider
 *  answer off the screen too. Every other action type keeps its amount. */
export function shownAmount(
  request: Pick<ApprovalRequest, 'action_type' | 'amount'> & Pick<Partial<ApprovalRequest>, 'context'>,
  canSeePayables: boolean,
): number | null | undefined {
  if (!canSeePayables && SUPPLIER_MONEY_ACTIONS.has(request.action_type)) return null;
  // A discount approval's money is the rupees it takes off the bill -- the
  // server's own figure (services/discount_approval), not a routing amount.
  if (request.action_type === 'discount_override') {
    const off = Number((request.context as DiscountContext | undefined)?.discount_rupees);
    return Number.isFinite(off) ? off : request.amount;
  }
  return request.amount;
}

/** The bill a discount_override approval covers, exactly as the SERVER priced
 *  it when it raised the request. The cashier wrote none of it. */
interface DiscountContext {
  lines?: {
    product_id?: string;
    product_name?: string | null;
    quantity?: number;
    mrp?: number | null;
    unit_price?: number;
    discount_percent?: number;
  }[];
  cart_discount_percent?: number;
  discount_percent?: number;
  discount_rupees?: number;
  bill_before?: number;
  bill_after?: number;
  zero_total?: boolean;
  customer_id?: string | null;
  customer_name?: string | null;
}

/** What a manager approves for a discount: shop, cashier, every line (name,
 *  qty, MRP, price, line %), the bill %, rupees off, the bill before/after,
 *  and the cashier's note -- labelled as theirs. */
function DiscountBill({ request }: { request: ApprovalRequest }) {
  const c = (request.context || {}) as DiscountContext;
  const pct = (v?: number) => `${Number(v || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}%`;
  return (
    <div className="mt-2 space-y-1.5 text-xs text-gray-700">
      <div>
        Shop <span className="font-medium text-gray-900">{request.store_name || request.store_id || '—'}</span>
        {' · '}Cashier{' '}
        <span className="font-medium text-gray-900">
          {request.requested_by_name || request.requested_by || '—'}
        </span>
        {' · '}Customer{' '}
        <span className="font-medium text-gray-900">
          {c.customer_name || c.customer_id || '—'}
        </span>
      </div>
      <table className="w-full text-left">
        <thead className="text-gray-500">
          <tr>
            <th className="font-normal py-0.5">Item</th>
            <th className="font-normal py-0.5 text-right">Qty</th>
            <th className="font-normal py-0.5 text-right">MRP</th>
            <th className="font-normal py-0.5 text-right">Price</th>
            <th className="font-normal py-0.5 text-right">Line off</th>
          </tr>
        </thead>
        <tbody>
          {(c.lines || []).map((ln, i) => (
            <tr key={`${ln.product_id}-${i}`} className="border-t border-gray-100">
              <td className="py-0.5 pr-2 text-gray-900">{ln.product_name || ln.product_id}</td>
              <td className="py-0.5 text-right">{ln.quantity}</td>
              <td className="py-0.5 text-right">{ln.mrp != null ? formatRupees(ln.mrp) : '—'}</td>
              <td className="py-0.5 text-right">{formatRupees(ln.unit_price)}</td>
              <td className="py-0.5 text-right">{pct(ln.discount_percent)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div>
        Bill discount <span className="font-medium text-gray-900">{pct(c.cart_discount_percent)}</span>
        {' · '}Total off <span className="font-medium text-gray-900">{formatRupees(c.discount_rupees)}</span>
        {' '}({pct(c.discount_percent)} at most on a line)
      </div>
      <div>
        Bill <span className="font-medium text-gray-900">{formatRupees(c.bill_before)}</span>
        {' → '}
        <span className="font-medium text-gray-900">{formatRupees(c.bill_after)}</span>
        {c.zero_total && <span className="ml-1 font-medium text-red-700">(Rs 0 bill)</span>}
      </div>
      {request.reason && (
        <p className="text-gray-700">
          <span className="text-gray-500">Cashier&apos;s note:</span> {request.reason}
        </p>
      )}
    </div>
  );
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

/** An inbox row: the server stamps ``can_approve`` -- whether approve / reject
 *  would pass this reader's role, maker-checker and shop gates (the engine's
 *  own checks). A Return-to-Vendor request is the accounts roles' alone (owner
 *  ruling 2026-10-12), and an accountant reads the other types without acting
 *  on them, so the buttons show only where they work. Absent = not stated
 *  (an older answer): the buttons show, as before. */
export type InboxRequest = ApprovalRequest & { can_approve?: boolean };

interface Props {
  request: InboxRequest;
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
  const { hasRole } = useAuth();
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

  const isDiscount = request.action_type === 'discount_override';
  const ctxSummary = isDiscount ? null : summariseContext(request.context);

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
            <span className="text-gray-700">
              {request.requested_by_name || request.requested_by || '—'}
            </span>
            {request.store_id && (
              <>
                {' · '}
                <span className="text-gray-700">{request.store_name || request.store_id}</span>
              </>
            )}
            {' · '}
            {formatDateTimeIST(request.created_at)}
          </div>
          {(request.status === 'APPROVED' || request.status === 'CONSUMED') &&
            (request.reviewed_by_name || request.reviewed_by) && (
              <div className="mt-0.5 text-xs text-green-700">
                Approved by{' '}
                <span className="font-medium">
                  {request.reviewed_by_name || request.reviewed_by}
                </span>
                {request.reviewed_at && <> · {formatDateTimeIST(request.reviewed_at)}</>}
              </div>
            )}
        </div>
        <div className="text-right shrink-0">
          <div className="font-mono text-sm text-gray-900">
            {formatRupees(shownAmount(request, hasRole(COST_ROLES)))}
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

      {isDiscount && <DiscountBill request={request} />}
      {!isDiscount && request.reason && (
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

      {showActions && isRequested && request.can_approve !== false && (
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
