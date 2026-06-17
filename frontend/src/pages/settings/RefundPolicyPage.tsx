// ============================================================================
// IMS 2.0 - Refund Policy (F27) — Settings section
// ============================================================================
// A focused editor for the configurable refund-approval matrix (DECISIONS sec 6):
//   - three approval-threshold tiers: auto-approve below / needs ADMIN above /
//     needs SUPERADMIN above (rupee inputs; stored as money_paisa policy keys)
//   - the refund approval-matrix master switch (gate is DARK by default)
//   - the original-tender hard-lock toggle (refunds always go back to the
//     original payment method)
// plus the approver's own PIN (reused from ApprovalPinSection).
//
// REUSE: this is a thin view over the existing E2 policy engine — every value is
// read/written through policiesApi (/settings/policies/*). It defines NO new
// settings store and duplicates NO refund logic; the matrix enforcement lives
// server-side in returns.py. Scope follows the locked global -> entity -> store
// hierarchy; this page edits the GLOBAL scope (the chain root). The generic
// Policy Matrix tab still exposes per-store/entity overrides for the same keys.

import { useCallback, useEffect, useState } from 'react';
import {
  RotateCcw, Save, Loader2, AlertTriangle, ShieldCheck, Lock, Info,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { policiesApi } from '../../services/api';
import { ApprovalPinSection } from '../../components/settings/ApprovalPinSection';

// The five policy keys this page edits (the three money tiers + two flags).
const K_AUTO = 'refund.tier.auto_below';
const K_ADMIN = 'refund.tier.admin_above';
const K_SUPER = 'refund.tier.super_above';
const K_MATRIX = 'refund.matrix_enabled';
const K_TENDER = 'refund.original_tender_enforce';

interface FormState {
  autoBelow: number;   // rupees
  adminAbove: number;  // rupees
  superAbove: number;  // rupees
  matrixEnabled: boolean;
  tenderEnforce: boolean;
}

const DEFAULTS: FormState = {
  autoBelow: 500,
  adminAbove: 2000,
  superAbove: 10000,
  matrixEnabled: false,
  tenderEnforce: true,
};

/** money_paisa policy values are integer paise; the UI works in rupees. */
const paiseToRupees = (v: unknown): number =>
  typeof v === 'number' && Number.isFinite(v) ? Math.round(v) / 100 : NaN;
const rupeesToPaise = (rupees: number): number => Math.round(rupees * 100);

export function RefundPolicySection() {
  const toast = useToast();
  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Read the effective GLOBAL values; fall back to code defaults per key.
      const res = await policiesApi.getAll('global');
      const pol = res?.policies || {};
      const next: FormState = { ...DEFAULTS };
      const a = paiseToRupees(pol[K_AUTO]?.value);
      const ad = paiseToRupees(pol[K_ADMIN]?.value);
      const su = paiseToRupees(pol[K_SUPER]?.value);
      if (!Number.isNaN(a)) next.autoBelow = a;
      if (!Number.isNaN(ad)) next.adminAbove = ad;
      if (!Number.isNaN(su)) next.superAbove = su;
      if (typeof pol[K_MATRIX]?.value === 'boolean') next.matrixEnabled = pol[K_MATRIX].value;
      if (typeof pol[K_TENDER]?.value === 'boolean') next.tenderEnforce = pol[K_TENDER].value;
      setForm(next);
    } catch {
      // Keep code defaults on a read error (page never blanks out).
      setForm(DEFAULTS);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Ordering invariant mirrors the backend (auto < admin < super) so a save can
  // never strand a tier; surfaced inline rather than only as a server 400.
  const ordered =
    form.autoBelow > 0 &&
    form.adminAbove > form.autoBelow &&
    form.superAbove > form.adminAbove;

  const save = async () => {
    if (!ordered) {
      toast.error('Thresholds must increase: auto-approve < admin < superadmin.');
      return;
    }
    setSaving(true);
    try {
      // Write to the GLOBAL scope (scope=null -> global on the backend).
      await Promise.all([
        policiesApi.set(K_AUTO, rupeesToPaise(form.autoBelow), null),
        policiesApi.set(K_ADMIN, rupeesToPaise(form.adminAbove), null),
        policiesApi.set(K_SUPER, rupeesToPaise(form.superAbove), null),
        policiesApi.set(K_MATRIX, form.matrixEnabled, null),
        policiesApi.set(K_TENDER, form.tenderEnforce, null),
      ]);
      toast.success('Refund policy saved.');
      await load();
    } catch (e) {
      const msg = (e instanceof Error && e.message) || 'Failed to save refund policy';
      toast.error(msg);
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
      {/* Approval thresholds */}
      <div className="card">
        <div className="flex items-center gap-2 mb-1">
          <RotateCcw className="w-5 h-5 text-gray-500" />
          <h2 className="text-lg font-semibold text-gray-900">Refund Approval Thresholds</h2>
        </div>
        <p className="text-sm text-gray-500 mb-4">
          Who can clear a refund depends on its amount. Below the auto-approve
          floor a cashier/manager can refund without sign-off; above each tier a
          higher role must PIN-approve. Set at HQ (global); per-store overrides
          live under <strong>Policy Matrix</strong>.
        </p>

        <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
          <RupeeField
            label="Auto-approve below (₹)"
            help="Refunds strictly under this need no approval."
            value={form.autoBelow}
            onChange={(v) => setForm((f) => ({ ...f, autoBelow: v }))}
          />
          <RupeeField
            label="Needs ADMIN above (₹)"
            help="At/above this an Admin (or Area Manager) must approve."
            value={form.adminAbove}
            onChange={(v) => setForm((f) => ({ ...f, adminAbove: v }))}
          />
          <RupeeField
            label="Needs SUPERADMIN above (₹)"
            help="At/above this only a Superadmin can approve."
            value={form.superAbove}
            onChange={(v) => setForm((f) => ({ ...f, superAbove: v }))}
          />
        </div>

        {!ordered && (
          <p className="mt-3 text-xs text-red-600 flex items-center gap-1">
            <AlertTriangle className="w-3.5 h-3.5" />
            Thresholds must increase: auto-approve &lt; admin &lt; superadmin.
          </p>
        )}
      </div>

      {/* Master switch + original tender */}
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Enforcement</h2>
        <div className="space-y-3">
          <ToggleRow
            icon={<ShieldCheck className="w-5 h-5 text-gray-500" />}
            title="Refund approval matrix enabled"
            desc="When ON, refunds above a tier require a PIN-approved request before they are recorded. When OFF (default), refunds are not gated."
            checked={form.matrixEnabled}
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
            The approval matrix is currently OFF — thresholds are saved but not
            enforced until you enable it. The original-tender lock applies
            independently of this switch.
          </p>
        )}

        <button
          type="button"
          onClick={save}
          disabled={saving || !ordered}
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

function RupeeField({
  label,
  help,
  value,
  onChange,
}: {
  label: string;
  help: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-600 mb-1">{label}</label>
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-gray-500">₹</span>
        <input
          type="number"
          min={0}
          step={1}
          value={Number.isFinite(value) ? value : 0}
          onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))}
          className="input-field pl-7"
        />
      </div>
      <p className="text-xs text-gray-400 mt-1">{help}</p>
    </div>
  );
}

function ToggleRow({
  icon,
  title,
  desc,
  checked,
  onChange,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  checked: boolean;
  onChange: (v: boolean) => void;
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
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
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
