// ============================================================================
// IMS 2.0 - The till's locked bill (money review 18)
// ============================================================================
// A payment that debits the customer (store credit, a gift voucher) could not
// be confirmed: the bill is LOCKED to its order and key, so it can only be
// pressed again (which finishes that payment once) or checked on the Orders
// screen. A changed bill would be another sale and take the credit again.
// Only a manager may abandon the attempt; the server keeps the money trace
// (its own P1 task for a debit that never reached a bill).

import { Lock } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { usePOSStore } from '../../stores/posStore';
import { TILL_BILL_LOCKED } from './posBillKey';

const MANAGERS = ['STORE_MANAGER', 'AREA_MANAGER', 'ADMIN', 'SUPERADMIN'];

export function BillLockNotice() {
  const lock = usePOSStore((s) => s.bill_lock);
  const { user } = useAuth();
  if (!lock) return null;
  const isManager = (user?.roles || []).some((r: string) => MANAGERS.includes(r));
  const abandon = () => {
    if (window.confirm('Abandon this attempt? The payment that could not be confirmed stays on record for '
      + 'Accounts (a task is raised if it was taken). The bill will be cleared.')) {
      usePOSStore.getState().abandonLockedBill();
    }
  };
  return (
    <div role="status" className="mx-3 mt-2 bg-amber-50 border border-amber-300 rounded-lg p-2.5 flex items-center gap-2 text-sm text-amber-800">
      <Lock className="w-4 h-4 flex-shrink-0" />
      <span className="flex-1">{TILL_BILL_LOCKED}{lock.orderId ? ` (order ${lock.orderId})` : ''}</span>
      {isManager && (
        <button type="button" onClick={abandon}
          className="px-2 py-1 text-xs rounded-md border border-amber-400 hover:bg-amber-100">
          Abandon this attempt (manager)
        </button>
      )}
    </div>
  );
}
