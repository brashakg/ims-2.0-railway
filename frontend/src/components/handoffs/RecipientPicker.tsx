// ============================================================================
// IMS 2.0 — Recipient Picker (shared between upload + reshare flows)
// ============================================================================
// Debounced search against `GET /handoffs/eligible-recipients/list`,
// chip-based selection, keyboard-friendly. Shared by HandoffUploadModal
// and the reshare sub-flow inside HandoffResponseModal.

import { useEffect, useState } from 'react';
import { Search, X, Loader2, Users } from 'lucide-react';
import { useDebounce } from '../../hooks/useDebounce';
import { handoffsApi, type EligibleRecipient } from '../../services/api/handoffs';

interface RecipientPickerProps {
  selected: EligibleRecipient[];
  onChange: (next: EligibleRecipient[]) => void;
  /** user_ids to hide from the picker (e.g. parent uploader on a reshare). */
  excludeUserIds?: string[];
  placeholder?: string;
}

const ROLE_LABELS: Record<string, string> = {
  SUPERADMIN: 'Superadmin',
  ADMIN: 'Admin',
  STORE_MANAGER: 'Store Manager',
  ACCOUNTANT: 'Accountant',
};

export function RecipientPicker({
  selected,
  onChange,
  excludeUserIds = [],
  placeholder = 'Search by name or username',
}: RecipientPickerProps) {
  const [query, setQuery] = useState('');
  const debouncedQuery = useDebounce(query, 300);
  const [results, setResults] = useState<EligibleRecipient[]>([]);
  const [searching, setSearching] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Initial population (no query) so the user sees the eligible roster
  // straight away. The eligible-roles set is small per backend — pulling
  // 500 max in one shot is fine.
  useEffect(() => {
    let cancelled = false;
    setSearching(true);
    setLoadError(null);
    handoffsApi
      .listEligibleRecipients(debouncedQuery || undefined)
      .then((r) => {
        if (cancelled) return;
        setResults(r.recipients);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : 'Could not load recipients');
        setResults([]);
      })
      .finally(() => {
        if (!cancelled) setSearching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery]);

  const selectedIds = new Set(selected.map((r) => r.user_id));
  const excludedIds = new Set(excludeUserIds);
  const visible = results.filter(
    (r) => !selectedIds.has(r.user_id) && !excludedIds.has(r.user_id),
  );

  const addRecipient = (r: EligibleRecipient) => {
    onChange([...selected, r]);
  };

  const removeRecipient = (userId: string) => {
    onChange(selected.filter((r) => r.user_id !== userId));
  };

  return (
    <div className="space-y-2">
      {/* Selected chips */}
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selected.map((r) => (
            <span
              key={r.user_id}
              className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2.5 py-1 text-xs text-blue-700 border border-blue-200"
            >
              <span className="font-medium">{r.name || r.username}</span>
              <span className="text-blue-500">· {ROLE_LABELS[r.role] || r.role}</span>
              <button
                type="button"
                onClick={() => removeRecipient(r.user_id)}
                className="text-blue-500 hover:text-blue-700"
                aria-label={`Remove ${r.name}`}
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Search box */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder}
          className="w-full pl-10 pr-9 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-bv-red-500 focus:border-transparent"
        />
        {searching && (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin text-gray-500" />
        )}
      </div>

      {/* Results list */}
      <div className="max-h-48 overflow-y-auto rounded-lg border border-gray-200 bg-white">
        {loadError && (
          <div className="px-3 py-2 text-xs text-red-600 bg-red-50">{loadError}</div>
        )}
        {!loadError && visible.length === 0 && !searching && (
          <div className="flex items-center gap-2 px-3 py-3 text-xs text-gray-500">
            <Users className="w-3.5 h-3.5" />
            {query
              ? 'No matching users.'
              : selected.length > 0
                ? 'All eligible users picked.'
                : 'No eligible recipients available.'}
          </div>
        )}
        {visible.map((r) => (
          <button
            key={r.user_id}
            type="button"
            onClick={() => addRecipient(r)}
            className="w-full text-left px-3 py-2 text-sm hover:bg-gray-50 flex items-center justify-between border-b border-gray-100 last:border-b-0"
          >
            <span>
              <span className="font-medium text-gray-900">
                {r.name || r.username || r.user_id}
              </span>
              {r.username && r.name && (
                <span className="text-gray-500"> · @{r.username}</span>
              )}
            </span>
            <span className="text-xs text-gray-500">
              {ROLE_LABELS[r.role] || r.role}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

export default RecipientPicker;
