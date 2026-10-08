import type { UserRole } from '../../types';

/** Who reads vendor returns and their GST debit notes: the purchase roles plus
 *  WORKSHOP_STAFF, who logs the defective pair and is shown the item, quantity
 *  and reason only. The ACCOUNTANT owns the debit notes (owner ruling
 *  2026-10-07, R2). It IS the backend's services/cost_mask RETURN_READERS
 *  (+ SUPERADMIN); backend/tests/test_supplier_money_masked_for_managers.py holds
 *  the two equal. The route, the Purchase tab and the Ctrl-K jump read it. */
export const RETURN_READERS: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'ACCOUNTANT',
  'WORKSHOP_STAFF',
];
