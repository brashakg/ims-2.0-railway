// ============================================================================
// IMS 2.0 - "It's a different product" (owner 2026-11-01)
// ============================================================================
// The same model written differently (RB4340 vs 4340) is asked about on Add
// Product -- in the similar-products strip and in the save popup's "maybe"
// mode. Both share this: the reason box (a reason is required: a quick pick
// and/or free text), "added by <name> on <date>" and Open-it-in-a-new-tab
// (the typed form and its photos are never left behind).

import { useState } from 'react';
import { existingProductPath } from './quickadd/shared';
import type { SimilarProductSummary } from '../../services/api/products';

/** The quick reasons (owner 2026-11-01) -- free text may add to them. */
const DIFFERENT_REASONS = ['Different line or series', 'Junior / kids version', 'Different lens'];

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "12 Sep" from an ISO time (en-IN's own short month says "Sept"); '' when unknown. */
function shortDate(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? `${d.getDate()} ${MONTHS[d.getMonth()]}` : '';
}

/** "added by priya on 12 Sep" (each half only when known). */
export function addedBy(s: SimilarProductSummary): string {
  const who = String(s.created_by_name || '').trim();
  const when = shortDate(s.created_at);
  return [who && `added by ${who}`, when && `on ${when}`].filter(Boolean).join(' ');
}

/** Open a product in a NEW TAB: the typed form and its photos stay put. */
export function openInNewTab(s: SimilarProductSummary | null | undefined): void {
  window.open(existingProductPath(s), '_blank', 'noopener');
}

/** "Why is it different?" -- a reason is required (a quick pick or text). */
export function DifferentProductReason({
  onSave,
  onCancel,
}: {
  onSave: (reason: string) => void;
  onCancel: () => void;
}) {
  const [pick, setPick] = useState('');
  const [text, setText] = useState('');
  const reason = [pick, text.trim()].filter(Boolean).join(' - ');
  return (
    <div className="mt-2 space-y-2" data-testid="different-reason">
      <p className="text-xs font-semibold text-gray-800">Why is it different?</p>
      <div className="flex flex-wrap gap-1.5">
        {DIFFERENT_REASONS.map((r) => (
          <button
            key={r}
            type="button"
            tabIndex={-1}
            aria-pressed={pick === r}
            onClick={() => setPick(pick === r ? '' : r)}
            className={`min-h-9 px-2.5 rounded border text-xs ${
              pick === r ? 'border-bv bg-bv-50 text-gray-900' : 'border-gray-200 bg-white text-gray-700'
            }`}
          >
            {r}
          </button>
        ))}
      </div>
      <input
        type="text"
        value={text}
        maxLength={200}
        onChange={(e) => setText(e.target.value)}
        placeholder="Anything else"
        aria-label="Why is it different?"
        className="input-field w-full"
      />
      <div className="flex gap-2">
        <button
          type="button"
          tabIndex={-1}
          disabled={!reason}
          onClick={() => onSave(reason)}
          className="btn-primary text-xs disabled:opacity-60 disabled:cursor-not-allowed"
        >
          Save as a different product
        </button>
        <button type="button" tabIndex={-1} onClick={onCancel} className="btn-secondary text-xs">
          Cancel
        </button>
      </div>
    </div>
  );
}
