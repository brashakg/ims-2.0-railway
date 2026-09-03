// ============================================================================
// IMS 2.0 - One-person-one-record popup (both directions)
// ============================================================================
// Owner ruling 2026-09-04: a person is never in two places. ONE component, two
// shapes, told apart by the 409 body's `code`:
//   * MOBILE_BELONGS_TO_FAMILY_MEMBER -- a create was refused because the number
//     already belongs to a FAMILY MEMBER on someone else's account. Actions:
//     PROMOTE the member to their own account (their Rx and eye tests move with
//     them), or OPEN the account that already holds them.
//   * MOBILE_IS_OWN_ACCOUNT -- a family member was refused because the number
//     is already a top-level customer's OWN account. Action: OPEN that account
//     (bill or examine them there; no copy is made -- a "link" row cannot be
//     truthful while orders/Rx are keyed by the account they were captured on).
// Every action is a 44px target so the counter is never stuck.

import { Loader2 } from 'lucide-react';
import {
  FAMILY_MEMBER_CONFLICT_CODE,
  type CustomerConflict,
} from '../../services/api/customers';

interface FamilyMemberConflictModalProps {
  conflict: CustomerConflict;
  busy: boolean;
  error?: string | null;
  /** Forward shape only; ignored for the own-account shape. */
  onPromote?: () => void;
  onOpenExisting: () => void;
  onCancel: () => void;
}

export function FamilyMemberConflictModal({
  conflict,
  busy,
  error,
  onPromote,
  onOpenExisting,
  onCancel,
}: FamilyMemberConflictModalProps) {
  const forward = conflict.code === FAMILY_MEMBER_CONFLICT_CODE;
  const title = forward
    ? 'This number is already on a family account'
    : `This number is already ${conflict.customer_name || 'an existing customer'}'s own account`;
  return (
    <div
      className="fixed inset-0 z-[60] bg-black/50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="family-member-conflict-title"
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-5">
        <h3 id="family-member-conflict-title" className="text-lg font-semibold text-gray-900">
          {title}
        </h3>
        {forward ? (
          <>
            <p className="mt-2 text-sm text-gray-700">
              This number belongs to{' '}
              <span className="font-semibold">{conflict.patient_name || 'a family member'}</span>
              {conflict.relation ? ` (${conflict.relation})` : ''}, a family member on{' '}
              <span className="font-semibold">
                {conflict.account_holder_name || 'an existing customer'}
              </span>
              &rsquo;s account.
            </p>
            <p className="mt-1 text-xs text-gray-500">
              One person, one record: a second account is not created. Move them to their own
              account (their prescriptions and eye tests move too), or continue on the existing
              account.
            </p>
          </>
        ) : (
          <>
            <p className="mt-2 text-sm text-gray-700">
              <span className="font-semibold">{conflict.patient_name || 'This family member'}</span>{' '}
              was not added:{' '}
              <span className="font-semibold">{conflict.customer_name || 'an existing customer'}</span>{' '}
              already has their own account with this number.
            </p>
            <p className="mt-1 text-xs text-gray-500">
              One person, one record: bill or examine them on their own account instead of adding
              a copy here.
            </p>
          </>
        )}
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-600">
            {error}
          </p>
        )}
        <div className="mt-4 flex flex-col sm:flex-row gap-2">
          {forward && (
            <button
              type="button"
              onClick={onPromote}
              disabled={busy || !onPromote}
              className="btn-primary min-h-[44px] flex-1 flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {busy && <Loader2 className="w-4 h-4 animate-spin" />}
              Promote to own account
            </button>
          )}
          <button
            type="button"
            onClick={onOpenExisting}
            disabled={busy}
            className={`${forward ? 'btn-outline' : 'btn-primary'} min-h-[44px] flex-1 flex items-center justify-center gap-2 disabled:opacity-50`}
          >
            {!forward && busy && <Loader2 className="w-4 h-4 animate-spin" />}
            {forward ? 'Open existing account' : 'Open their account'}
          </button>
        </div>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="mt-2 w-full min-h-[44px] text-sm text-gray-600 hover:text-gray-800 disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

export default FamilyMemberConflictModal;
