// Quick Add - "Colour Name" with a Suggest button (owner 2026-10-23): the
// server reads the product's OWN photo 1 and suggests a retail colour name in
// the house style ("Gold / Green", "Havana / Brown Gradient"). The suggestion
// is only shown; Accept (or Change, to edit it first) puts it in the field and
// the normal Save stores it as attributes.colour_name - the name the website
// colour option is built from. Nothing is written by Suggest itself.

import { useRef, useState } from 'react';
import { Loader2, Sparkles } from 'lucide-react';
import { useToast } from '../../../context/ToastContext';
import { productApi } from '../../../services/api/products';

/** Eyewear-like categories that carry a frame / lens colour name. Contact
 *  lenses already have their own Colour Name field. */
export const COLOUR_NAME_CATEGORIES = new Set(['SG', 'FR', 'RG', 'SMTFR']);

interface Props {
  category: string;
  attributes: Record<string, string>;
  value: string;
  onChange: (value: string) => void;
  /** The saved product when editing; null on Add product. */
  productId: string | null;
  /** The form's photo 1 (may be unsaved). */
  photo: string | null;
}

export function ColourNameSuggest({ category, attributes, value, onChange, productId, photo }: Props) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [suggestion, setSuggestion] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  const suggest = async () => {
    if (busy || !photo) return;
    setBusy(true);
    setSuggestion('');
    try {
      const res = await productApi.suggestColourName({
        ...(productId ? { product_id: productId } : {}),
        image_url: photo,
        category,
        attributes,
      });
      if (res?.status === 'SUGGESTED' && res.suggestion) setSuggestion(res.suggestion);
      else toast.error(res?.message || 'Could not suggest a colour name - please try again.');
    } catch (err) {
      toast.error(err instanceof Error && err.message ? err.message : 'Could not suggest a colour name - please try again.');
    } finally {
      setBusy(false);
    }
  };

  const take = (edit: boolean) => {
    onChange(suggestion);
    setSuggestion('');
    if (edit) inputRef.current?.focus();
  };

  return (
    <div className="mt-3" data-testid="qa-colour-name">
      <div className="flex items-center justify-between mb-1">
        <label className="block text-xs font-medium text-gray-700" htmlFor="qa-field-colour_name">
          Colour Name
        </label>
        <button
          type="button"
          onClick={() => void suggest()}
          disabled={busy || !photo}
          title={photo ? 'Suggest a colour name from photo 1' : 'Add a photo first'}
          className="inline-flex items-center gap-1 rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
          Suggest
        </button>
      </div>
      <input
        ref={inputRef}
        id="qa-field-colour_name"
        type="text"
        title="Colour Name"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="e.g. Gold / Green"
        className="input-field w-full"
      />
      {suggestion && (
        <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-700" data-testid="qa-colour-suggestion">
          Suggested from photo 1: <span className="font-semibold">{suggestion}</span>
          <button
            type="button"
            onClick={() => take(false)}
            className="rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 hover:bg-blue-100"
          >
            Accept
          </button>
          <button
            type="button"
            onClick={() => take(true)}
            className="rounded-md border border-gray-300 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-700 hover:bg-gray-50"
          >
            Change
          </button>
        </p>
      )}
    </div>
  );
}
