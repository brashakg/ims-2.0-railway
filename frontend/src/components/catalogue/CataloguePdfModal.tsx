// ============================================================================
// IMS 2.0 - Share as PDF modal (reusable)
// ============================================================================
// A small, self-contained modal that builds a branded catalogue PDF from EITHER
// a collection (collectionId) OR a hand-picked selection (productIds), with two
// independent toggles: "Include product details" (default OFF) and "Include MRP"
// (default ON). On Generate it POSTs and downloads the returned PDF blob.
//
// Reusable from the collections list AND the catalog manager / quick-share page:
// pass whichever of collectionId / productIds applies.

import { useState } from 'react';
import { FileDown, Loader2, X } from 'lucide-react';
import { cataloguePdfApi } from '../../services/api/cataloguePdf';
import { useToast } from '../../context/ToastContext';

interface Props {
  open: boolean;
  onClose: () => void;
  /** One of these must be supplied. */
  collectionId?: string;
  productIds?: string[];
  /** Cover title (collection name / a label for a hand-picked set). */
  title?: string;
}

function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <label className="flex items-start gap-3 cursor-pointer py-2">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`mt-0.5 shrink-0 relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
          checked ? 'bg-bv-red-600' : 'bg-gray-300'
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
            checked ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </button>
      <span>
        <span className="text-sm font-medium text-gray-900">{label}</span>
        {hint && <span className="block text-xs text-gray-500">{hint}</span>}
      </span>
    </label>
  );
}

export default function CataloguePdfModal({
  open,
  onClose,
  collectionId,
  productIds,
  title,
}: Props) {
  const toast = useToast();
  const [includeDetails, setIncludeDetails] = useState(false);
  const [includeMrp, setIncludeMrp] = useState(true);
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const count = productIds?.length ?? 0;
  const hasSelection = !!collectionId || count > 0;

  const handleGenerate = async () => {
    if (!hasSelection) {
      toast.error('Nothing selected to share.');
      return;
    }
    setBusy(true);
    try {
      await cataloguePdfApi.generatePdf({
        collectionId,
        productIds,
        includeDetails,
        includeMrp,
        title,
      });
      toast.success('Catalogue PDF downloaded.');
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not generate the PDF.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={busy ? undefined : onClose}
    >
      <div
        className="w-full max-w-md rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
            <FileDown size={18} /> Share as PDF
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded p-1 text-gray-400 hover:text-gray-700 disabled:opacity-40"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4">
          <p className="mb-3 text-sm text-gray-500">
            {collectionId
              ? 'Generate a branded PDF of this collection to share with a customer.'
              : `Generate a branded PDF of ${count} selected product${count === 1 ? '' : 's'}.`}
          </p>
          <div className="divide-y divide-gray-100">
            <Toggle
              checked={includeDetails}
              onChange={setIncludeDetails}
              label="Include product details"
              hint="Key attributes (shape, material, colour…) + a short description"
            />
            <Toggle
              checked={includeMrp}
              onChange={setIncludeMrp}
              label="Include MRP"
              hint="Show the price (and any offer price)"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="btn-secondary text-sm disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleGenerate}
            disabled={busy || !hasSelection}
            className="btn-primary inline-flex items-center gap-2 text-sm disabled:opacity-50"
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : <FileDown size={16} />}
            {busy ? 'Generating…' : 'Generate PDF'}
          </button>
        </div>
      </div>
    </div>
  );
}
