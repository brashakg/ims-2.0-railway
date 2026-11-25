// Audit b21 review: a failed handoff read is never shown as an empty Hub section.
/** Shared "couldn't load" row for the Hub handoff sections (never a false empty). */
export default function HandoffsUnavailable({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex items-center justify-between gap-2 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 mb-2">
      <span>{what} are unavailable right now.</span>
      <button type="button" onClick={onRetry} className="text-xs font-semibold text-bv-red-600 hover:underline">Retry</button>
    </div>
  );
}
