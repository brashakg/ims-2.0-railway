import type { UserRole } from '../../types';

/**
 * Who may raise a purchase order: the server's PO create gate
 * (vendors/_shared._VENDOR_ROLES on POST /vendors/purchase-orders, plus
 * SUPERADMIN, who always passes) -- pinned to it by
 * test_add_product_owner_rulings.py. Also the Purchase section pages' gate.
 * CATALOG_MANAGER is NOT in it (owner 2026-10-08).
 */
export const PURCHASE_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'];
