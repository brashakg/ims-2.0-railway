// ============================================================================
// IMS 2.0 - Hold / recall controls, shared by both new tills
// ============================================================================
// The two bill-strip buttons ("Hold bill", "Held" + count badge) and the
// recall modal, extracted from BillingSurface so the general counter gets
// hold/recall by SHARING this implementation, not by copying it. That closes
// a real hole: the store auto-parks a cart when the screen goes idle on EVERY
// surface, so a cart parked on the counter was unrecoverable from the counter.
//
// All the ownership scoping (one cashier must never see another's parked
// cart) lives in useHeldBills - this file is only the chrome. Buttons are
// 44px minimum: these run on shop iPads (they were 36px on the billing
// surface before the extraction).

import { useState } from 'react';
import { X } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { usePOSStore } from '../../stores/posStore';
import { useHeldBills } from './useHeldBills';

export function HeldBillsControls() {
  const { user } = useAuth();
  const store = usePOSStore();
  const held = useHeldBills(store, user?.id || '');
  const [recallOpen, setRecallOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => {
          if ((store.cart || []).length === 0) return;
          held.holdCurrentBill();
        }}
        disabled={(store.cart || []).length === 0}
        title="Put this bill aside and serve the next customer"
        className="inline-flex items-center gap-1.5 px-2.5 min-h-[44px] rounded-lg border border-gray-200 bg-white text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
      >
        Hold bill
      </button>
      <button
        type="button"
        onClick={() => setRecallOpen(true)}
        title="Bring back a bill you put aside"
        className="inline-flex items-center gap-1.5 px-2.5 min-h-[44px] rounded-lg border border-gray-200 bg-white text-[11px] font-medium text-gray-700 hover:bg-gray-50"
      >
        Held
        {held.heldBills.length > 0 && (
          <span className="ml-0.5 inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-amber-500 text-white text-[10px] font-semibold">
            {held.heldBills.length}
          </span>
        )}
      </button>
      {/* A direct way out of a wrong bill. Without it "start over" was
          Hold -> Held -> Discard, three taps through a modal. The native
          confirm is the guard: a mis-tap here would lose a sale. */}
      <button
        type="button"
        onClick={() => {
          if ((store.cart || []).length === 0) return;
          if (window.confirm('Discard this bill? Items and payments entered so far will be lost.')) {
            store.resetTransaction();
          }
        }}
        disabled={(store.cart || []).length === 0}
        title="Throw this bill away and start a new one"
        className="inline-flex items-center gap-1.5 px-2.5 min-h-[44px] rounded-lg border border-gray-200 bg-white text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
      >
        Discard bill
      </button>

      {recallOpen && (
        <div
          className="fixed inset-0 z-[60] bg-black/40 flex items-center justify-center p-4"
          onClick={() => setRecallOpen(false)}
        >
          <div
            className="bg-white rounded-2xl w-full max-w-lg max-h-[80dvh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-3.5 border-b border-gray-200 flex items-center gap-3">
              <h2 className="text-base font-semibold flex-1">Held bills</h2>
              <button
                onClick={() => setRecallOpen(false)}
                aria-label="Close"
                className="min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-gray-500 hover:text-gray-900"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            {held.heldBills.length === 0 ? (
              <div className="p-6 text-center text-sm text-gray-500">
                Nothing on hold. Use <span className="font-medium text-gray-700">Hold bill</span> to
                put the current sale aside without losing it.
              </div>
            ) : (
              <div className="p-3 space-y-2">
                {held.heldBills.map((b) => (
                  <div key={b.id} className="flex items-center gap-3 rounded-xl border border-gray-200 p-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">{b.customer || 'No customer'}</div>
                      <div className="text-[11px] text-gray-500">
                        {b.items} item{b.items === 1 ? '' : 's'} · {'₹'}
                        {Math.round(b.total || 0).toLocaleString('en-IN')}
                        {b.auto ? ' · parked automatically' : ''}
                      </div>
                    </div>
                    <button
                      onClick={() => { held.discardBill(b.id); }}
                      className="min-h-[44px] px-2.5 rounded-lg border border-gray-200 text-[11px] text-gray-600 hover:bg-gray-50"
                    >
                      Discard
                    </button>
                    <button
                      onClick={() => { if (held.recallBill(b.id)) setRecallOpen(false); }}
                      className="min-h-[44px] px-3 rounded-lg bg-gray-900 text-white text-[11px] font-semibold"
                    >
                      Recall
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

export default HeldBillsControls;
