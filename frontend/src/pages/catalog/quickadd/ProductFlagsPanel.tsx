// ============================================================================
// IMS 2.0 - Open flags on the product edit page (owner 2026-10-12)
// ============================================================================
// A manager flagged this product as needing work (Catalog page). The
// cataloguer fixes it here and presses Mark fixed; the last fixed flag of a
// task completes that task on the server (services/catalog_flags, one rule).

import { useState } from 'react';
import { CheckCircle2, Flag, Loader2 } from 'lucide-react';
import { catalogBulkApi, type ProductFlag } from '../../../services/api/catalog';
import { useToast } from '../../../context/ToastContext';
import { flagLabel, whenIST } from '../catalogListState';

// The flags come from the product the edit page already loaded (one fetch).
export function ProductFlagsPanel({
  productId,
  initialFlags,
}: {
  productId: string;
  initialFlags: ProductFlag[];
}) {
  const toast = useToast();
  const [flags, setFlags] = useState<ProductFlag[]>(initialFlags);
  const [busy, setBusy] = useState<string | null>(null);

  const open = flags.filter((f) => f.status === 'open');
  if (open.length === 0) return null;

  const markFixed = async (flagId: string) => {
    setBusy(flagId);
    try {
      const res = await catalogBulkApi.fixFlags(productId, [flagId]);
      setFlags(res.flags || []);
      toast.success('Marked fixed.');
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Could not mark it fixed.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div
      className="mb-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3"
      data-testid="product-flags-panel"
    >
      <p className="flex items-center gap-2 text-sm font-semibold text-red-900">
        <Flag className="h-4 w-4" /> Flagged as needing work
      </p>
      <ul className="mt-2 space-y-2">
        {open.map((f) => (
          <li key={f.flag_id} className="flex flex-wrap items-start gap-3 rounded-lg bg-white px-3 py-2">
            <div className="min-w-[200px] flex-1 text-sm">
              <span className="font-semibold text-gray-900">{flagLabel(f.type)}</span>
              {f.note && <span className="text-gray-800"> - {f.note}</span>}
              <div className="text-xs text-gray-500">
                From {f.by_name || 'a manager'}, {whenIST(f.at)}
              </div>
            </div>
            <button
              type="button"
              onClick={() => void markFixed(f.flag_id)}
              disabled={busy !== null}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 text-sm font-medium text-gray-900 hover:bg-gray-50 disabled:opacity-60"
            >
              {busy === f.flag_id ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4 text-green-600" />
              )}
              Mark fixed
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
