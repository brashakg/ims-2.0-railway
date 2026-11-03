// A pending key found on till load with no bill here (money review
// follow-up): one warning; dismissing it is a confirm, like Discard.
// It sits in the page flow above the bill strip, never fixed: a fixed box at
// the bottom covered Complete sale on tablets and phones, and the only way
// past it was Dismiss, which clears the keys before anyone checks Orders.

import { usePOSStore } from '../../stores/posStore';

export function OrphanBillWarning() {
  const orphans = usePOSStore((s) => s.orphan_bill_ids);
  const dismiss = usePOSStore((s) => s.dismissOrphanBillKeys);
  if (!(orphans || []).length) return null;
  return (
    <div role="alert" className="mx-3.5 mt-2 shrink-0 flex items-center gap-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      <span className="flex-1">A sale may already be paid but is no longer on this till - check Orders</span>
      <button
        type="button"
        onClick={() => {
          if (window.confirm('Have you checked Orders? Dismissing clears this warning for good.')) {
            dismiss();
          }
        }}
        className="min-h-[44px] px-3 rounded-lg border border-red-200 bg-white text-[11px] font-semibold text-red-700 hover:bg-red-100"
      >
        Dismiss
      </button>
    </div>
  );
}

export default OrphanBillWarning;
