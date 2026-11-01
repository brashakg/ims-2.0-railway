// ============================================================================
// IMS 2.0 - Reorder settings modal (opened from each row of the Reorder list)
// ============================================================================
// No formula of its own (stock redesign S5 review): the level is typed in by a
// person, the buy number is the server's (reorder_policy.buy_qty), and the
// 30-day sales are shown exactly as the server counted them. A blank field is
// "not set"; only what the person changed is saved.

import { useId, useState } from 'react';
import { X, TrendingDown, Save, Loader2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { typedLevel, levelText, isLevelInputValid, isBadInput, LEVEL_INPUT_ERROR } from '../../utils/reorderLevel';

interface ReorderPointModalProps {
  isOpen: boolean;
  onClose: () => void;
  product: {
    id: string;
    sku: string;
    name: string;
    brand: string;
    currentStock: number;
    /** This shop's level; null/undefined = not set. */
    reorderPoint?: number | null;
    /** The server's band for the saved level (reorder_policy.stock_status). */
    stockStatus?: string;
    // From the product master: null/undefined = never configured;
    // <= 0 (the -1 sentinel) = auto-reorder explicitly DISABLED.
    reorderQuantity?: number | null;
    /** null/undefined/0 = not set (no cap). */
    maxStock?: number | null;
    /** null/undefined/0 = not set. */
    leadTimeDays?: number | null;
    /** The server's count of pieces sold at this shop in the last 30 days. */
    soldLast30Days?: number;
  };
  onSave: (data: ReorderPointData) => Promise<void>;
  /** The quantity / max stock / lead time are product-wide: read-only for a
   *  role that cannot edit the product (never sent for it). */
  productWideLocked?: boolean;
  /** True for a role that may not set a shop's level (a catalogue manager). */
  shopLevelLocked?: boolean;
  /** Why the level is locked, when it is (e.g. no active shop). */
  shopLevelNotice?: string;
}

/** Only what changed is in `product` (PUT /products/{id} drops nulls, so a
 *  cleared max stock / lead time is sent as 0 = not set). */
export interface ReorderPointData {
  productId: string;
  /** null = not set (clears this shop's level). The caller sends it only when
   *  it differs from the saved level. */
  reorderPoint: number | null;
  product: { reorder_quantity?: number; max_stock?: number; lead_time_days?: number };
}

/** '' for not set (null, undefined, 0 or less). */
const shown = (n: number | null | undefined) => (n != null && n > 0 ? String(n) : '');
/** The whole number typed, or null for blank / not a whole number >= 0. */
const whole = (text: string) => (/^\d+$/.test(text.trim()) ? Number(text.trim()) : null);

export function ReorderPointModal({ isOpen, onClose, product, onSave, productWideLocked = false, shopLevelLocked = false, shopLevelNotice }: ReorderPointModalProps) {
  const toast = useToast();
  const ids = useId();

  const startDisabled = product.reorderQuantity != null && product.reorderQuantity <= 0;
  const [isSaving, setIsSaving] = useState(false);
  const [reorderPoint, setReorderPoint] = useState(levelText(product.reorderPoint));
  const [levelBadInput, setLevelBadInput] = useState(false);
  const [autoReorderOff, setAutoReorderOff] = useState(startDisabled);
  const [pack, setPack] = useState(shown(product.reorderQuantity));
  const [maxStock, setMaxStock] = useState(shown(product.maxStock));
  const [leadTime, setLeadTime] = useState(shown(product.leadTimeDays));

  const handleSubmit = async () => {
    // Blank = not set; 0 is a real level (alert once this shop runs out).
    if (levelBadInput || !isLevelInputValid(reorderPoint)) {
      toast.error(LEVEL_INPUT_ERROR);
      return;
    }
    const level = typedLevel(reorderPoint);
    const changed: ReorderPointData['product'] = {};
    if (!productWideLocked) {
      if (autoReorderOff !== startDisabled || pack !== shown(product.reorderQuantity)) {
        if (autoReorderOff) {
          changed.reorder_quantity = -1; // the server's "auto-reorder off"
        } else {
          const n = whole(pack);
          if (n == null || n < 1) {
            toast.error('Order in packs of must be at least 1 (or tick "Auto-reorder disabled")');
            return;
          }
          changed.reorder_quantity = n;
        }
      }
      for (const [key, text, saved] of [
        ['max_stock', maxStock, product.maxStock],
        ['lead_time_days', leadTime, product.leadTimeDays],
      ] as const) {
        if (text === shown(saved)) continue;
        const n = text.trim() === '' ? 0 : whole(text);
        if (n == null) {
          toast.error('Max stock and lead time are whole numbers (blank = not set)');
          return;
        }
        changed[key] = n;
      }
      const cap = changed.max_stock ?? (product.maxStock || 0);
      if (level !== null && cap > 0 && cap < level) {
        toast.error('Max stock must be at least the reorder level');
        return;
      }
    }

    setIsSaving(true);
    try {
      await onSave({ productId: product.id, reorderPoint: level, product: changed });
      onClose();
    } catch (error: unknown) {
      toast.error((error as Error)?.message || 'Could not save the reorder settings');
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  // The band is the SERVER's, for the saved level only: a level being typed has
  // no verdict until it is saved (the client never decides low stock).
  const serverStatus =
    typedLevel(reorderPoint) === (product.reorderPoint ?? null) ? product.stockStatus : undefined;
  const lockedWide = isSaving || productWideLocked;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90dvh] overflow-hidden">
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-gray-100 rounded-lg">
              <TrendingDown className="w-6 h-6 text-gray-500" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900">Reorder settings</h2>
              <p className="text-sm text-gray-500">{product.name}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isSaving}
            aria-label="Close"
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="p-6 overflow-y-auto" style={{ maxHeight: 'calc(90vh - 200px)' }}>
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 mb-6">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-gray-600 mb-1">SKU</p>
                <p className="font-medium text-gray-900">{product.sku}</p>
              </div>
              <div>
                <p className="text-sm text-gray-600 mb-1">Brand</p>
                <p className="font-medium text-gray-900">{product.brand}</p>
              </div>
              <div>
                <p className="text-sm text-gray-600 mb-1">In stock here</p>
                <div className="flex items-center gap-2">
                  <p className="font-medium text-gray-900">{product.currentStock}</p>
                  {(serverStatus === 'critical' || serverStatus === 'out-of-stock') && (
                    <span className="px-2 py-1 bg-red-50 text-red-700 text-xs font-medium rounded-full">Critical</span>
                  )}
                  {serverStatus === 'low' && (
                    <span className="px-2 py-1 bg-amber-50 text-amber-700 text-xs font-medium rounded-full">Low</span>
                  )}
                </div>
              </div>
              <div>
                <p className="text-sm text-gray-600 mb-1">Sold in the last 30 days</p>
                <p className="font-medium text-gray-900">{product.soldLast30Days ?? 0}</p>
              </div>
            </div>
          </div>

          {productWideLocked && (
            <p className="mb-4 text-xs text-gray-600">
              You set this shop&apos;s reorder level. The pack size, maximum stock and lead time apply
              to every shop and are set by the catalogue team.
            </p>
          )}
          <div className="space-y-6">
            <div>
              <label htmlFor={`${ids}-level`} className="block text-sm font-medium text-gray-700 mb-2">
                Reorder level at this shop (pieces)
              </label>
              <input
                id={`${ids}-level`}
                type="number"
                min="0"
                value={reorderPoint}
                placeholder="not set"
                onChange={(e) => {
                  setReorderPoint(e.target.value);
                  setLevelBadInput(isBadInput(e.target));
                }}
                disabled={isSaving || shopLevelLocked}
                className="input-field w-full"
              />
              <p className="text-xs text-gray-500 mt-1">
                {shopLevelNotice ? (
                  <span role="alert" className="text-red-600">{shopLevelNotice}</span>
                ) : (
                  'Set: the list buys back to one above it. Not set: it buys the last 30 days’ sales.'
                )}
              </p>
            </div>

            <div>
              <label htmlFor={`${ids}-pack`} className="block text-sm font-medium text-gray-700 mb-2">
                Order in packs of
              </label>
              <label className="flex items-center gap-2 mb-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoReorderOff}
                  onChange={(e) => setAutoReorderOff(e.target.checked)}
                  disabled={lockedWide}
                  className="w-4 h-4 rounded"
                />
                <span className="text-sm text-gray-700">Auto-reorder disabled</span>
              </label>
              <input
                id={`${ids}-pack`}
                type="number"
                min="1"
                value={pack}
                onChange={(e) => setPack(e.target.value)}
                disabled={autoReorderOff || lockedWide}
                className="input-field w-full disabled:bg-gray-100 disabled:text-gray-400"
              />
              <p className="text-xs text-gray-500 mt-1">
                {autoReorderOff
                  ? 'This product is never suggested on the Reorder list.'
                  : 'The suggested buy is rounded up to a whole number of packs.'}
              </p>
            </div>

            <div>
              <label htmlFor={`${ids}-max`} className="block text-sm font-medium text-gray-700 mb-2">
                Maximum stock
              </label>
              <input
                id={`${ids}-max`}
                type="number"
                min="0"
                value={maxStock}
                onChange={(e) => setMaxStock(e.target.value)}
                disabled={lockedWide}
                className="input-field w-full"
              />
              <p className="text-xs text-gray-500 mt-1">The suggested buy never takes a shop above this. Blank = no cap.</p>
            </div>

            <div>
              <label htmlFor={`${ids}-lead`} className="block text-sm font-medium text-gray-700 mb-2">
                Supplier lead time (days)
              </label>
              <input
                id={`${ids}-lead`}
                type="number"
                min="0"
                max="365"
                value={leadTime}
                onChange={(e) => setLeadTime(e.target.value)}
                disabled={lockedWide}
                className="input-field w-full"
              />
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between p-6 border-t border-gray-200 bg-gray-50">
          <button onClick={onClose} disabled={isSaving} className="btn-outline">
            Cancel
          </button>
          <button onClick={handleSubmit} disabled={isSaving} className="btn-primary flex items-center gap-2">
            {isSaving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                Save settings
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
