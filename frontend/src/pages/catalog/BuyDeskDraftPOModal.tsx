// ============================================================================
// IMS 2.0 - Buy Desk -> bulk draft Purchase Order
// ============================================================================
// Multi-select rows on the Buy Desk, pick ONE vendor, confirm per-line qty +
// cost, and create a single DRAFT PO. Thin modal shell around the shared
// <PurchaseOrderComposer/> (procurement Phase 2C, owner ruling: one PO form
// everywhere) so this quick-draft door shows the IDENTICAL field set, labels,
// cost pre-fill ("last paid Rs X on <date>") and validation as the manual PO
// form. Every line carries the row's REAL catalogued product_id, so the PO
// catalog gate (ON) accepts it. Cost is REQUIRED up front here too: accept_grn
// reads the PO's unit price, so a zero-cost line would mint stock with no cost
// basis -- the composer enforces unit_cost > 0 on every line (matching the
// manual form). Buyers who want lines from different vendors create one draft
// per vendor. What's local to this door: it fetches the active vendor list,
// preselects the single agreed preferred vendor, and shows each line's product
// read-only (no picker / no add-remove) since the rows are chosen upstream.

import { useEffect, useMemo, useRef, useState } from 'react';
import { FileText, X as XIcon } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { vendorsApi } from '../../services/api';
import { PurchaseOrderComposer } from '../../components/purchase/PurchaseOrderComposer';
import type {
  ComposerVendorOption,
  ComposerLine,
} from '../../components/purchase/PurchaseOrderComposer';
import type { BuyDeskRow } from '../../services/api/buyDesk';

function mapVendor(v: Record<string, unknown>): ComposerVendorOption {
  const id = String(v.vendor_id ?? v._id ?? '');
  return {
    id,
    name: String(v.trade_name ?? v.legal_name ?? id),
    code: String(v.vendor_code ?? id.slice(0, 8).toUpperCase()),
  };
}

export default function BuyDeskDraftPOModal({
  rows,
  onClose,
  onCreated,
}: {
  rows: BuyDeskRow[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const { user } = useAuth();

  const [vendors, setVendors] = useState<ComposerVendorOption[]>([]);
  const [vendorsLoading, setVendorsLoading] = useState(true);
  const [preferredVendorId, setPreferredVendorId] = useState('');

  // The preselected rows become the composer's initial lines (fixed set). Qty
  // defaults to the netted buy signal when we have one, else 1. Cost starts
  // blank -- the composer's last-cost prefill fills it from PO history per the
  // chosen vendor, and the buyer can override.
  const initialLines: ComposerLine[] = useMemo(
    () =>
      rows.map((r) => ({
        productId: r.product_id,
        productName: r.name || r.sku || r.product_id,
        sku: r.sku || '',
        quantity: r.buy_signal && r.buy_signal > 0 ? r.buy_signal : 1,
        unitCost: 0,
        taxRate: 18,
        costTouched: false,
        lastPaid: null,
      })),
    [rows],
  );

  // Load active vendors once, then apply the single-preferred-vendor preselect
  // (#863): only when every selected row that has a preference agrees on ONE
  // vendor AND that vendor is in the active list. Anything else (no preference,
  // mixed vendors, inactive vendor) fails soft to a manual pick. Never overrides
  // a choice the user already made -- the composer only adopts an empty slot.
  const loadedRef = useRef(false);
  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        const resp = await vendorsApi.getVendors({ is_active: true });
        if (cancelled) return;
        const raw: Record<string, unknown>[] = resp?.vendors ?? [];
        const options = raw.map(mapVendor).filter((v) => v.id);
        setVendors(options);
        const preferred = [
          ...new Set(rows.map((r) => r.preferred_vendor_id).filter((v): v is string => !!v)),
        ];
        if (preferred.length === 1 && options.some((v) => v.id === preferred[0])) {
          setPreferredVendorId(preferred[0]);
        }
      } catch {
        if (!cancelled) toast.error('Could not load vendors');
      } finally {
        if (!cancelled) setVendorsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-8">
        <div className="flex items-center justify-between p-5 border-b border-gray-200">
          <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
            <FileText className="w-5 h-5 text-blue-600" />
            Create draft PO · {rows.length} product{rows.length === 1 ? '' : 's'}
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <XIcon className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="p-5">
          <PurchaseOrderComposer
            mode="modal"
            vendors={vendors}
            vendorsLoading={vendorsLoading}
            initialVendorId={preferredVendorId}
            initialLines={initialLines}
            vendorHint="One vendor per draft. For multiple vendors, create a draft per group."
            renderProductCell={({ line }) => (
              <div className="min-w-0 px-1 py-2">
                <div className="text-sm font-medium text-gray-900 truncate">{line.productName}</div>
                <div className="text-xs text-gray-500 truncate">{line.sku}</div>
              </div>
            )}
            submitLabel="Create draft PO"
            submittingLabel="Creating…"
            onCancel={onClose}
            onSubmit={async (payload) => {
              const storeId = user?.activeStoreId ?? 'default';
              const resp = await vendorsApi.createPurchaseOrder({
                vendor_id: payload.vendorId,
                delivery_store_id: storeId,
                expected_date: payload.expectedDate || undefined,
                notes: payload.notes || undefined,
                items: payload.items.map((it) => ({
                  product_id: it.product_id,
                  product_name: it.product_name,
                  sku: it.sku,
                  quantity: it.quantity,
                  unit_price: it.unit_price,
                })),
              });
              toast.success(
                `Draft PO ${resp.po_number ?? ''} created with ${payload.items.length} line(s)`,
              );
              onCreated();
            }}
          />
        </div>
      </div>
    </div>
  );
}
