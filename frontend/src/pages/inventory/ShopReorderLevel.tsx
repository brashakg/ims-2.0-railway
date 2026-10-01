// ============================================================================
// THIS shop's reorder level on a stock-ledger row (owner ruling D12)
// ============================================================================
// Shows the level the server sends for the selected shop ('not set' when
// there is none -- never -1). A manager (an admin: any shop) taps it to type a
// level or clear it; the write is for this shop only
// (PUT /inventory/reorder-levels/{product_id}).

import { useState } from 'react';
import { useToast } from '../../context/ToastContext';
import { reorderApi } from '../../services/api/inventory';
import { typedLevel, isLevelInputValid, LEVEL_INPUT_ERROR } from '../../utils/reorderLevel';

export function ShopReorderLevel({
  productId,
  storeId,
  level,
  canEdit,
  onSaved,
}: {
  productId: string;
  storeId: string;
  level: number | null | undefined;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState<string | null>(null); // null = not editing
  const [saving, setSaving] = useState(false);
  const shown = level == null ? 'not set' : String(level);

  if (!canEdit) {
    return <span className="text-xs text-gray-500">Reorder at: {shown}</span>;
  }
  if (draft === null) {
    return (
      <button
        type="button"
        onClick={() => setDraft(level == null ? '' : String(level))}
        // min-h: a tablet-sized tap target (owner: tablets are the counter device).
        className="inline-flex items-center min-h-[32px] text-xs text-gray-500 hover:text-bv-red-600 underline decoration-dotted"
        aria-label={`Reorder level at this shop: ${shown}. Change it`}
        title="Change this shop's reorder level"
      >
        Reorder at: {shown}
      </button>
    );
  }

  const save = async () => {
    if (!isLevelInputValid(draft)) {
      toast.error(LEVEL_INPUT_ERROR);
      return;
    }
    const next = typedLevel(draft);
    setSaving(true);
    try {
      await reorderApi.setShopLevel(productId, storeId, next);
      setDraft(null);
      onSaved();
    } catch (err) {
      toast.error(err instanceof Error && err.message ? err.message : 'Could not save the reorder level.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <span className="inline-flex items-center gap-1">
      <input
        type="number"
        min={0}
        autoFocus
        value={draft}
        placeholder="not set"
        aria-label="Reorder level"
        disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void save();
          if (e.key === 'Escape') setDraft(null);
        }}
        className="input-field w-20 text-xs"
      />
      <button type="button" onClick={() => void save()} disabled={saving} className="btn sm primary">
        Save
      </button>
      <button type="button" onClick={() => setDraft(null)} disabled={saving} className="btn sm">
        Cancel
      </button>
    </span>
  );
}
