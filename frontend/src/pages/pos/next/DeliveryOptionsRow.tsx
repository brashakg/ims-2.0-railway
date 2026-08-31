// ============================================================================
// IMS 2.0 - POS delivery options row (Wave 4, owner spec 7)
// ============================================================================
// One compact row: collection date, 2-hour window, priority, quick note.
//
// MOUNT (BillingSurface left column, under the product results strip):
//
//   <DeliveryOptionsRow />
//
// Zero props on purpose - every value is an EXISTING posStore field
// (delivery_date / delivery_time_slot / delivery_priority / cart_note) that
// submitOrder.ts already carries to the backend. No new store state, no local
// copy of any of it: the classic surface and this one write the same four
// keys, so a draft resumed on either surface shows the same schedule.

import { usePOSStore } from '../../../stores/posStore';
import { istDayString } from '../../../utils/datetime';

/** The store's booking windows. Exported so the classic surface's copy can be
    pointed here when it retires - one vocabulary, or workshop queues sort two
    different sets of slot strings. */
export const DELIVERY_TIME_SLOTS = [
  '10:00-12:00',
  '12:00-14:00',
  '14:00-16:00',
  '16:00-18:00',
  '18:00-20:00',
];

const CONTROL =
  'min-h-[44px] px-2 rounded-lg border border-gray-200 bg-white text-sm text-gray-900';

export function DeliveryOptionsRow() {
  const store = usePOSStore();
  // Stores are Indian; a browser-local "today" can be yesterday in IST after
  // 05:30 UTC, which would let staff book a collection date in the past.
  const today = istDayString(new Date()) || undefined;
  const priority = store.delivery_priority || 'NORMAL';

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2">
      <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500 shrink-0">
        Delivery
      </span>

      <input
        type="date"
        aria-label="Delivery or collection date"
        title="Delivery or collection date"
        min={today}
        value={store.delivery_date || ''}
        onChange={(e) => store.setDeliveryDate(e.target.value || null)}
        className={`${CONTROL} w-[150px]`}
      />

      <select
        aria-label="Delivery time slot"
        title="Delivery time slot"
        value={store.delivery_time_slot || ''}
        onChange={(e) => store.setDeliveryTimeSlot(e.target.value || null)}
        className={`${CONTROL} w-[132px]`}
      >
        <option value="">Any time</option>
        {DELIVERY_TIME_SLOTS.map((slot) => (
          <option key={slot} value={slot}>
            {slot.replace('-', ' – ')}
          </option>
        ))}
      </select>

      <select
        aria-label="Delivery priority"
        title="Delivery priority"
        value={priority}
        onChange={(e) =>
          store.setDeliveryPriority(e.target.value as 'NORMAL' | 'EXPRESS' | 'URGENT')
        }
        className={`min-h-[44px] px-2 rounded-lg border text-sm font-medium w-[132px] ${
          priority === 'URGENT'
            ? 'border-red-300 bg-red-50 text-red-700'
            : priority === 'EXPRESS'
              ? 'border-orange-300 bg-orange-50 text-orange-700'
              : 'border-gray-200 bg-white text-gray-900'
        }`}
      >
        <option value="NORMAL">Normal</option>
        <option value="EXPRESS">Express</option>
        <option value="URGENT">Urgent (same day)</option>
      </select>

      {/* cart_note is the BILL-level note the order already carries; there is
          no separate delivery-note field and adding one is a backend change. */}
      <input
        type="text"
        aria-label="Note for this bill"
        title="Note for this bill"
        value={store.cart_note || ''}
        onChange={(e) => store.setCartNote(e.target.value)}
        placeholder="Quick note (call before delivery, gift wrap…)"
        className={`${CONTROL} flex-1 min-w-[160px]`}
      />
    </div>
  );
}

export default DeliveryOptionsRow;
