// ============================================================================
// IMS 2.0 - Refund Policy (F27) — Settings section
// ============================================================================
// The refund approval rule as it really runs (owner ruling 2026-10-23): when a
// bill's refunds would go above Rs 2,000 in total, cashier / sales staff need a
// store manager's (or higher) PIN approval. Shown, not edited here:
//   - the rule's master switch (ON; only a Superadmin may change it -- the
//     server refuses anyone else, so it is only written when changed)
//   - the original-tender hard-lock toggle (refunds always go back to the
//     original payment method)
// plus the approver's own PIN (reused from ApprovalPinSection). The old
// refund.tier.* amount bands no longer decide customer refunds and are not
// shown here (they remain under Policy Matrix for other approval types).
//
// REUSE: this is a thin view over the existing E2 policy engine — every value is
// read/written through policiesApi (/settings/policies/*). It defines NO new
// settings store and duplicates NO refund logic; the matrix enforcement lives
// server-side in returns.py. Scope follows the locked global -> entity -> store
// hierarchy; this page edits the GLOBAL scope (the chain root). The generic
// Policy Matrix tab still exposes per-store/entity overrides for the same keys.

import { useCallback, useEffect, useState } from 'react';
import {
  RotateCcw, Save, Loader2, ShieldCheck, Lock, Info,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { policiesApi } from '../../services/api';
import { ApprovalPinSection } from '../../components/settings/ApprovalPinSection';
import { useAuth } from '../../context/AuthContext';
import { getErrorMessage } from '../../utils/errorHandler';

// The two policy keys this page edits.
const K_MATRIX = 'refund.matrix_enabled';
const K_TENDER = 'refund.original_tender_enforce';

interface FormState {
  matrixEnabled: boolean;
  tenderEnforce: boolean;
}

const DEFAULTS: FormState = {
  matrixEnabled: true,
  tenderEnforce: true,
};

export function RefundPolicySection() {
  const toast = useToast();
  const { user } = useAuth();
  // The saved-matrix switch is the owner's: the server refuses anyone else.
  const isSuperadmin = (user?.roles || []).includes('SUPERADMIN');
  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // True only once the server's current values were read. A Save from a form
  // that never loaded them would write guesses -- e.g. switch the Rs 2,000
  // manager-approval rule OFF -- so it is refused.
  const [loaded, setLoaded] = useState(false);
  // What the server held: the switch is written only when it was changed.
  const [initial, setInitial] = useState<FormState>(DEFAULTS);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Read the effective GLOBAL values; fall back to code defaults per key.
      const res = await policiesApi.getAll('global');
      const pol = res?.policies || {};
      const next: FormState = { ...DEFAULTS };
      if (typeof pol[K_MATRIX]?.value === 'boolean') next.matrixEnabled = pol[K_MATRIX].value;
      if (typeof pol[K_TENDER]?.value === 'boolean') next.tenderEnforce = pol[K_TENDER].value;
      setForm(next);
      setInitial(next);
      setLoaded(true);
    } catch {
      // Keep code defaults on a read error (page never blanks out).
      setForm(DEFAULTS);
      setLoaded(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    if (!loaded) {
      toast.error('The current refund policy could not be loaded, so nothing was saved. Reload the page and try again.');
      return;
    }
    setSaving(true);
    try {
      // Write to the GLOBAL scope (scope=null -> global on the backend).
      const writes = [policiesApi.set(K_TENDER, form.tenderEnforce, null)];
      if (form.matrixEnabled !== initial.matrixEnabled) {
        writes.push(policiesApi.set(K_MATRIX, form.matrixEnabled, null));
      }
      await Promise.all(writes);
      toast.success('Refund policy saved.');
      await load();
    } catch (e) {
      toast.error(getErrorMessage(e, 'Failed to save refund policy'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-gray-500">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        <span className="text-sm">Loading refund policy…</span>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* The rule as it runs */}
      <div className="card">
        <div className="flex items-center gap-2 mb-1">
          <RotateCcw className="w-5 h-5 text-gray-500" />
          <h2 className="text-lg font-semibold text-gray-900">Refund Approval Rule</h2>
        </div>
        <p className="text-sm text-gray-600">
          When a bill&apos;s refunds would go above <strong>₹2,000 in total</strong> (everything
          already refunded on that bill plus this refund), a cashier or sales staff member needs a
          store manager (or higher) to approve it with their PIN. Store managers and HQ are not
          gated. The rule is the owner's and always applies - no one can switch it off here.
        </p>
      </div>

      {/* Master switch + original tender */}
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Enforcement</h2>
        <div className="space-y-3">
          <ToggleRow
            icon={<ShieldCheck className="w-5 h-5 text-gray-500" />}
            title="Apply the saved stricter refund matrix"
            desc={
              isSuperadmin
                ? "ON: a saved, stricter refund matrix applies on top of the owner's ₹2,000 rule. OFF never loosens the owner's rule."
                : "Only a Superadmin can change this. The owner's ₹2,000 rule applies either way."
            }
            checked={form.matrixEnabled}
            disabled={!isSuperadmin}
            onChange={(v) => setForm((f) => ({ ...f, matrixEnabled: v }))}
          />
          <ToggleRow
            icon={<Lock className="w-5 h-5 text-gray-500" />}
            title="Refund to original tender (hard-lock)"
            desc="Force every refund back to the order's original payment method (card → card, UPI → UPI, cash → cash). Turn OFF to allow an audit-logged tender override."
            checked={form.tenderEnforce}
            onChange={(v) => setForm((f) => ({ ...f, tenderEnforce: v }))}
          />
        </div>

        {!form.matrixEnabled && (
          <p className="mt-3 text-xs text-gray-500 flex items-center gap-1">
            <Info className="w-3.5 h-3.5" />
            No saved stricter matrix applies — the owner&apos;s ₹2,000 rule still does.
            The original-tender lock applies independently of this switch.
          </p>
        )}

        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="btn-primary mt-4"
        >
          {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
          {saving ? 'Saving…' : 'Save Refund Policy'}
        </button>
      </div>

      {/* The approver's own PIN — required to action the refund queue. */}
      <ApprovalPinSection />
    </div>
  );
}

function ToggleRow({
  icon,
  title,
  desc,
  checked,
  onChange,
  disabled = false,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4 p-3 bg-gray-50 rounded-lg">
      <div className="flex items-start gap-3 min-w-0">
        <div className="mt-0.5 shrink-0">{icon}</div>
        <div className="min-w-0">
          <p className="font-medium text-gray-900">{title}</p>
          <p className="text-sm text-gray-500">{desc}</p>
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={title}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
          checked ? 'bg-blue-600' : 'bg-gray-300'
        }`}
      >
        <span
          className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-5' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );
}

export default RefundPolicySection;
