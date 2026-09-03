// ============================================================================
// IMS 2.0 - Family-member conflict popup
// ============================================================================
// Shown by AddCustomerModal when POST /customers is refused because the number
// already belongs to a FAMILY MEMBER on someone else's account (owner ruling
// 2026-09-04: one person, one record). Two 44px actions so the counter is
// never stuck: PROMOTE the member to their own account (their Rx and eye tests
// move with them), or OPEN the account that already holds them.

import { Loader2 } from 'lucide-react';
import type { FamilyMemberConflict } from '../../services/api/customers';

interface FamilyMemberConflictModalProps {
  conflict: FamilyMemberConflict;
  busy: boolean;
  error?: string | null;
  onPromote: () => void;
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
  const holder = conflict.account_holder_name || 'an existing customer';
  const member = conflict.patient_name || 'a family member';
  return (
    <div
      className="fixed inset-0 z-[60] bg-black/50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="family-member-conflict-title"
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-5">
        <h3 id="family-member-conflict-title" className="text-lg font-semibold text-gray-900">
          This number is already on a family account
        </h3>
        <p className="mt-2 text-sm text-gray-700">
          This number belongs to <span className="font-semibold">{member}</span>
          {conflict.relation ? ` (${conflict.relation})` : ''}, a family member on{' '}
          <span className="font-semibold">{holder}</span>&rsquo;s account.
        </p>
        <p className="mt-1 text-xs text-gray-500">
          One person, one record: a second account is not created. Move them to their own
          account (their prescriptions and eye tests move too), or continue on the existing
          account.
        </p>
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-600">
            {error}
          </p>
        )}
        <div className="mt-4 flex flex-col sm:flex-row gap-2">
          <button
            type="button"
            onClick={onPromote}
            disabled={busy}
            className="btn-primary min-h-[44px] flex-1 flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            Promote to own account
          </button>
          <button
            type="button"
            onClick={onOpenExisting}
            disabled={busy}
            className="btn-outline min-h-[44px] flex-1 disabled:opacity-50"
          >
            Open existing account
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
