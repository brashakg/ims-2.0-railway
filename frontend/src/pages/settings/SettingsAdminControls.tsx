// ============================================================================
// IMS 2.0 - Settings · Operational Rules (/settings/rules)
// ============================================================================
// Owner rulings 2026-10-08: the Store Modules, Role Permissions and Discount
// Limits editors and every operational rule that duplicated a rule IMS already
// enforces elsewhere (discount caps, oversell block, per-store geo-fence,
// per-shift grace, per-Rx validity, auto-logout, ...) were removed. Two rules
// remain, saved to admin_controls.operational_rules:
//   default_credit_limit  live - the till's credit check uses it for a
//                         customer with no limit of their own
//   auto_round_off        not read yet; the round-off job decides it

import { useState, useEffect, useRef } from 'react';
import { settingsApi } from '../../services/api/settings';
import { Save, Loader2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

interface OperationalRule {
  id: 'auto_round_off' | 'default_credit_limit';
  label: string;
  description: string;
  value: boolean | number;
  type: 'toggle' | 'number';
}

// Shown until the load lands. The credit default the till enforces comes from
// the server (GET returns the value in force), never from this placeholder.
const DEFAULT_RULES: OperationalRule[] = [
  { id: 'auto_round_off', label: 'Auto Round-off to Nearest ₹1', description: 'Round invoice totals', value: true, type: 'toggle' },
  { id: 'default_credit_limit', label: 'Default Credit Limit (₹)', description: 'For a customer with no credit limit of their own. A credit sale over the limit is blocked at the till.', value: 150000, type: 'number' },
];

// -----------------------------------------------------------------------
// Save bar
// -----------------------------------------------------------------------

type AdminControlsPayload = Parameters<typeof settingsApi.updateAdminControls>[0];

const LOAD_ERROR = 'Could not load the saved settings - the values below are defaults, not what is stored. Do not save until this is resolved; reload the page to retry.';

/** Visible failure for the stored-settings load (never silently show defaults). */
function LoadError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="p-3 rounded-lg border border-red-200 bg-red-50 text-sm text-red-700">
      {message}
    </div>
  );
}

/**
 * Unsaved-edit guard. The app runs on <BrowserRouter> (not a data router), so
 * react-router's useBlocker is unavailable; this follows the codebase's own
 * window.confirm pattern. While `dirty`: closing/reloading the tab prompts via
 * beforeunload, and a click on any in-app link (the settings rail included)
 * asks before leaving.
 */
function useUnsavedGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    const onClick = (e: MouseEvent) => {
      // Ctrl/Cmd/Shift/Alt or a non-primary button opens a new tab/window (or
      // downloads) - this page is not left, so no prompt.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      const a = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!a || a.target === '_blank' || a.hasAttribute('download') || a.getAttribute('href')?.startsWith('#')) return;
      // mailto:/tel: and a link back to this very page do not leave it.
      if (a.protocol !== 'http:' && a.protocol !== 'https:') return;
      if (a.pathname === window.location.pathname) return;
      if (!window.confirm('You have unsaved changes on this page - leave without saving?')) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onClick, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      document.removeEventListener('click', onClick, true);
    };
  }, [dirty]);
}

/**
 * dirty flag + guard: markDirty() on a user edit. beginSave() is called when a
 * save starts and returns the function to call when it succeeds; that clears
 * dirty only if no edit landed in between (an edit made while the save was in
 * flight is not in the saved payload and must stay guarded).
 */
function useDirty() {
  const [dirty, setDirty] = useState(false);
  const version = useRef(0);
  useUnsavedGuard(dirty);
  return {
    dirty,
    markDirty: () => { version.current += 1; setDirty(true); },
    beginSave: () => {
      const at = version.current;
      return () => { if (version.current === at) setDirty(false); };
    },
  };
}

function SaveBar({ label, payload, dirty, beginSave }: {
  label: string; payload: () => AdminControlsPayload; dirty: boolean; beginSave: () => () => void;
}) {
  const toast = useToast();
  const [isSaving, setIsSaving] = useState(false);

  const handleSave = async () => {
    setIsSaving(true);
    const saved = beginSave();
    try {
      await settingsApi.updateAdminControls(payload());
      saved();
      toast.success('Admin settings saved successfully');
    } catch {
      toast.error('Failed to save settings');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex items-center justify-end gap-3 pt-4 border-t border-gray-200">
      {dirty && <span className="text-xs text-amber-700">Unsaved changes</span>}
      <button
        onClick={handleSave}
        disabled={isSaving}
        className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50"
      >
        {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
        {isSaving ? 'Saving...' : `Save ${label}`}
      </button>
    </div>
  );
}

// -----------------------------------------------------------------------
// /settings/rules - the operational rules
// -----------------------------------------------------------------------

export function OperationalRulesSection() {
  // Operational rules
  const [rules, setRulesState] = useState<OperationalRule[]>(DEFAULT_RULES);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { dirty, markDirty, beginSave } = useDirty();
  // User edits go through this so they mark the page dirty; the load below does not.
  const setRules = (u: (prev: OperationalRule[]) => OperationalRule[]) => { markDirty(); setRulesState(u); };

  useEffect(() => {
    // Load saved admin controls
    settingsApi.getAdminControls().then((data: any) => {
      if (data?.operational_rules && Object.keys(data.operational_rules).length > 0) {
        setRulesState(prev => prev.map(r => ({
          ...r,
          value: data.operational_rules[r.id] !== undefined ? data.operational_rules[r.id] : r.value,
        })));
      }
    }).catch(() => setLoadError(LOAD_ERROR));
  }, []);

  return (
    <div className="space-y-6">
      <LoadError message={loadError} />
      <div className="space-y-3">
        {rules.map(rule => (
          <div key={rule.id} className="flex items-center justify-between p-3 bg-white border border-gray-200 rounded-lg">
            <div className="flex-1 min-w-0 mr-4">
              <p className="text-sm font-medium text-gray-900">{rule.label}</p>
              <p className="text-xs text-gray-500 mt-0.5">{rule.description}</p>
            </div>
            <div className="flex-shrink-0">
              {rule.type === 'toggle' && (
                <button
                  onClick={() => setRules(prev => prev.map(r =>
                    r.id === rule.id ? { ...r, value: !r.value } : r
                  ))}
                  aria-label={rule.label}
                  aria-pressed={!!rule.value}
                  className={clsx(
                    'relative inline-flex h-7 w-12 items-center rounded-full transition-colors',
                    rule.value ? 'bg-green-600' : 'bg-gray-600'
                  )}
                >
                  <span className={clsx(
                    'inline-block h-5 w-5 rounded-full bg-white transition-transform',
                    rule.value ? 'translate-x-6' : 'translate-x-1'
                  )} />
                </button>
              )}
              {rule.type === 'number' && (
                <input
                  type="number"
                  min={1}
                  aria-label={rule.label}
                  value={rule.value as number}
                  onChange={(e) => setRules(prev => prev.map(r =>
                    r.id === rule.id ? { ...r, value: Number(e.target.value) } : r
                  ))}
                  className="w-32 bg-white border border-gray-300 text-gray-900 rounded px-2 py-1 text-sm text-right"
                />
              )}
            </div>
          </div>
        ))}
      </div>
      <SaveBar
        label="Operational Rules"
        dirty={dirty}
        beginSave={beginSave}
        payload={() => ({ operational_rules: Object.fromEntries(rules.map(r => [r.id, r.value])) })}
      />
    </div>
  );
}
