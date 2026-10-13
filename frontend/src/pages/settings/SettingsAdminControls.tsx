// ============================================================================
// IMS 2.0 - Settings · Operational Rules (/settings/rules)
// ============================================================================
// Owner rulings 2026-10-08: the Store Modules, Role Permissions and Discount
// Limits editors and every operational rule that duplicated a rule IMS already
// enforces elsewhere (discount caps, oversell block, per-store geo-fence,
// per-shift grace, per-Rx validity, auto-logout, ...) were removed. One rule
// remains, saved to admin_controls.operational_rules.default_credit_limit:
// the till's credit check uses it for a customer with no limit of their own.
// (Bill round-off is not a setting here; the server ignores auto_round_off.)

import { useState, useEffect, useRef } from 'react';
import { settingsApi } from '../../services/api/settings';
import { Save, Loader2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';

const LIMIT_LABEL = 'Default Credit Limit (₹)';

// -----------------------------------------------------------------------
// Save bar
// -----------------------------------------------------------------------

type AdminControlsPayload = Parameters<typeof settingsApi.updateAdminControls>[0];

const LOAD_ERROR = 'Could not load the saved settings, so they cannot be changed or saved. Reload the page to retry.';

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

function SaveBar({ label, payload, dirty, beginSave, blocked }: {
  label: string; payload: () => AdminControlsPayload; dirty: boolean; beginSave: () => () => void; blocked?: boolean;
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
        disabled={isSaving || blocked}
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
  // The box holds the text as typed: a number input fed Number('') puts back
  // '0', and typing after it showed '0200000'.
  const [text, setTextState] = useState('');
  // Save stays off until the saved value is in the box: a failed or pending
  // load must never write a placeholder over the default the till enforces.
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { dirty, markDirty, beginSave } = useDirty();
  const setText = (v: string) => { markDirty(); setTextState(v); };

  useEffect(() => {
    settingsApi.getAdminControls().then((data: { operational_rules?: { default_credit_limit?: unknown } }) => {
      const saved = data?.operational_rules?.default_credit_limit;
      if (typeof saved !== 'number') throw new Error('no default credit limit');
      setTextState(String(saved));
      setLoaded(true);
    }).catch(() => setLoadError(LOAD_ERROR));
  }, []);

  // The server refuses 0 or less (422); say why here instead of a bare "Failed".
  const limit = text.trim() === '' ? NaN : Number(text);
  const limitError = !loaded || (Number.isFinite(limit) && limit > 0) ? null : 'Enter an amount above ₹0';

  return (
    <div className="space-y-6">
      <LoadError message={loadError} />
      <div className="flex items-center justify-between p-3 bg-white border border-gray-200 rounded-lg">
        <div className="flex-1 min-w-0 mr-4">
          <p className="text-sm font-medium text-gray-900">{LIMIT_LABEL}</p>
          <p className="text-xs text-gray-500 mt-0.5">For a customer with no credit limit of their own. A credit sale over the limit is blocked at the till.</p>
        </div>
        <div className="flex-shrink-0 text-right">
          <input
            type="number"
            min={1}
            aria-label={LIMIT_LABEL}
            value={text}
            disabled={!loaded}
            placeholder={loaded || loadError ? undefined : 'Loading...'}
            onChange={(e) => setText(e.target.value)}
            aria-invalid={!!limitError}
            aria-describedby={limitError ? 'credit-limit-error' : loaded ? 'credit-limit-amount' : undefined}
            className="w-32 bg-white border border-gray-300 text-gray-900 rounded px-2 py-1 text-sm text-right disabled:bg-gray-100"
          />
          {limitError ? (
            <p id="credit-limit-error" role="alert" className="mt-1 text-xs text-red-700">{limitError}</p>
          ) : loaded && (
            <p id="credit-limit-amount" className="mt-1 text-xs text-gray-500">= ₹{limit.toLocaleString('en-IN')}</p>
          )}
        </div>
      </div>
      <SaveBar
        label="Operational Rules"
        dirty={dirty}
        beginSave={beginSave}
        blocked={!loaded || !!limitError}
        payload={() => ({ operational_rules: { default_credit_limit: limit } })}
      />
    </div>
  );
}
