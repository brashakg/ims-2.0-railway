// Quick Add - "Colour Name" with a Suggest button (owner 2026-10-23): the
// server reads the product's OWN photo 1 and suggests a retail colour name in
// the house style ("Gold / Green", "Havana / Brown Gradient"). The suggestion
// is only shown; Accept (or Change, to edit it first) puts it in the field and
// the normal Save stores it as attributes.colour_name - the name the website
// colour option is built from. Nothing is written by Suggest itself.

import { useEffect, useRef, useState } from 'react';
import { Loader2, Sparkles } from 'lucide-react';
import { useToast } from '../../../context/ToastContext';
import { FE_CODE_TO_CANONICAL } from '../../../domain/catalog/productAdd/categoryFields';
import { productApi } from '../../../services/api/products';
import { getErrorMessage } from '../../../utils/errorHandler';

/** Registry categories whose colour name is the frame / lens retail name.
 *  Not a grid field on these (the grid order is owner-locked); contact
 *  lenses keep their own Colour Name field in the grid. */
export const COLOUR_NAME_CANONICALS: ReadonlySet<string> = new Set([
  'FRAME', 'SUNGLASS', 'READING_GLASSES', 'SMARTGLASSES',
]);

export const hasColourNameSuggest = (code: string): boolean =>
  COLOUR_NAME_CANONICALS.has(FE_CODE_TO_CANONICAL[code] ?? '');

const FALLBACK = 'Could not suggest a colour name - please try again.';

interface Props {
  category: string;
  attributes: Record<string, string>;
  value: string;
  onChange: (value: string) => void;
  /** The saved product when editing; null otherwise. */
  productId: string | null;
  /** The catalog draft being reviewed; null otherwise. */
  catalogId?: string | null;
  /** The form's photo 1 (may be unsaved). */
  photo: string | null;
  /** The grid's input classes (caps entry, amber flag). */
  inputClassName: string;
}

export function ColourNameSuggest({
  category, attributes, value, onChange, productId, catalogId = null, photo, inputClassName,
}: Props) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // A suggestion belongs to the product + photo it was read from. One for
  // anything else (the form moved on while the call was out) is never shown.
  const subject = `${productId ?? ''}|${catalogId ?? ''}|${photo ?? ''}`;
  const [result, setResult] = useState<{ subject: string; name: string } | null>(null);
  const suggestion = result?.subject === subject ? result.name : '';
  const subjectRef = useRef(subject);
  useEffect(() => {
    subjectRef.current = subject;
  }, [subject]);

  const suggest = async () => {
    if (busy || !photo) return;
    const asked = subject;
    setBusy(true);
    setResult(null);
    try {
      const res = await productApi.suggestColourName({
        ...(productId ? { product_id: productId } : {}),
        ...(catalogId ? { catalog_id: catalogId } : {}),
        image_url: photo,
        category,
        attributes,
      });
      if (subjectRef.current !== asked) return;
      if (res?.status === 'SUGGESTED' && res.suggestion) setResult({ subject: asked, name: res.suggestion });
      else toast.error(res?.message || FALLBACK);
    } catch (err) {
      if (subjectRef.current === asked) toast.error(getErrorMessage(err, FALLBACK));
    } finally {
      setBusy(false);
    }
  };

  const take = (edit: boolean) => {
    onChange(suggestion);
    setResult(null);
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
        className={inputClassName}
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
