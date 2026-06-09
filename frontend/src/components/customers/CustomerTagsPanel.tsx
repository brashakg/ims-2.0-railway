// ============================================================================
// IMS 2.0 - F39 Customer tags panel (Customer 360)
// ============================================================================
// Shows a customer's manager-approved tags as neutral outline chips. Any staff
// (SALES_STAFF+) can SUGGEST a tag; STORE_MANAGER+ sees pending suggestions and
// approves/rejects them. Approved tags feed the next-day NBA daily call list.
// Restrained UI: neutral chips, single accent only via the existing buttons.

import { useCallback, useEffect, useState } from 'react';
import api from '../../services/api/client';
import { useAuth } from '../../context/AuthContext';

interface TagSuggestion {
  suggestion_id: string;
  tag: string;
  status: string;
  suggested_by?: string;
}

const MANAGER_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];

export function CustomerTagsPanel({
  customerId,
  tags = [],
}: {
  customerId: string;
  tags?: string[];
}) {
  const { user } = useAuth();
  const isManager = (user?.roles || []).some((r) => MANAGER_ROLES.includes(r));

  const [currentTags, setCurrentTags] = useState<string[]>(tags);
  const [suggestions, setSuggestions] = useState<TagSuggestion[]>([]);
  const [showSuggest, setShowSuggest] = useState(false);
  const [newTag, setNewTag] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => {
    setCurrentTags(tags);
  }, [tags]);

  const loadSuggestions = useCallback(async () => {
    if (!isManager) return;
    try {
      const { data } = await api.get(`/customers/${customerId}/tags/suggestions`);
      setSuggestions(Array.isArray(data?.suggestions) ? data.suggestions : []);
    } catch {
      setSuggestions([]);
    }
  }, [customerId, isManager]);

  useEffect(() => {
    loadSuggestions();
  }, [loadSuggestions]);

  const submitSuggest = async () => {
    if (newTag.trim().length < 1) return;
    setBusy(true);
    setMsg('');
    try {
      await api.post(`/customers/${customerId}/tags/suggest`, { tag: newTag.trim() });
      setNewTag('');
      setShowSuggest(false);
      setMsg('Suggested. A manager will review it.');
      await loadSuggestions();
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || 'Could not suggest tag.');
    } finally {
      setBusy(false);
    }
  };

  const review = async (sugId: string, action: 'approve' | 'reject') => {
    setBusy(true);
    try {
      const { data } = await api.post(`/customers/${customerId}/tags/suggestions/${sugId}/${action}`);
      if (action === 'approve' && data?.tag) {
        setCurrentTags((prev) => (prev.includes(data.tag) ? prev : [...prev, data.tag]));
      }
      await loadSuggestions();
    } catch {
      /* fail-soft */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 mt-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Tags</span>
        <button
          type="button"
          onClick={() => setShowSuggest((s) => !s)}
          className="text-sm text-gray-600 underline hover:text-gray-800"
        >
          Suggest tag
        </button>
      </div>

      <div className="flex flex-wrap gap-1.5 mt-2">
        {currentTags.length === 0 ? (
          <span className="text-gray-400 text-sm">No tags yet.</span>
        ) : (
          currentTags.map((t) => (
            <span key={t} className="border border-gray-300 text-gray-600 text-xs rounded px-1.5 py-0.5">
              {t}
            </span>
          ))
        )}
      </div>

      {showSuggest && (
        <div className="flex items-center gap-2 mt-3">
          <input
            type="text"
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            placeholder="e.g. Zeiss fan"
            maxLength={50}
            className="border border-gray-200 rounded px-2 py-1 text-sm flex-1"
          />
          <button
            type="button"
            onClick={submitSuggest}
            disabled={busy || newTag.trim().length < 1}
            className="bg-bv-red text-white rounded px-3 py-1 text-sm disabled:opacity-40"
          >
            Submit
          </button>
        </div>
      )}
      {msg && <p className="text-xs text-gray-500 mt-2">{msg}</p>}

      {isManager && suggestions.length > 0 && (
        <div className="mt-3 border-t border-gray-100 pt-3">
          <p className="text-xs text-gray-500 mb-2">{suggestions.length} pending suggestion(s)</p>
          <div className="flex flex-col gap-2">
            {suggestions.map((s) => (
              <div key={s.suggestion_id} className="flex items-center justify-between">
                <span className="text-sm text-gray-700">{s.tag}</span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => review(s.suggestion_id, 'approve')}
                    disabled={busy}
                    className="text-sm text-green-700 underline hover:text-green-800"
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => review(s.suggestion_id, 'reject')}
                    disabled={busy}
                    className="text-sm text-gray-500 underline hover:text-gray-700"
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default CustomerTagsPanel;
