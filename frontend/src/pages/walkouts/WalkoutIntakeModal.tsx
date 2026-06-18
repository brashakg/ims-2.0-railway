// ============================================================================
// IMS 2.0 — Walkout Intake Modal (Pune Incentive Module i, Phase 1)
// ============================================================================
// Logged-in sales staff opens this from /walkouts → "+ Log Walkout".
// 30 fields across 4 sections; submit POSTs to /api/v1/walkouts and
// the server stamps walkout_id, store_id, sales_person_name, and
// links/auto-creates a customer from the mobile.
//
// See docs/PUNE_INCENTIVE_BUILD_PLAN.md §"Module (i)" for the
// schema + field list.

import { useEffect, useState } from 'react';
import { X, Loader2, AlertTriangle, PhoneOff } from 'lucide-react';
import { walkoutsApi } from '../../services/api';
import { adminStoreApi } from '../../services/api/stores';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  WALKOUT_AGE_GROUPS,
  WALKOUT_GENDERS,
  WALKOUT_PRODUCT_CATEGORIES,
  WALKOUT_PRICE_RANGES,
  WALKOUT_REASONS,
  WALKOUT_PURCHASE_PLANS,
} from '../../types';
import type {
  AgeGroup,
  WalkoutGender,
  WalkoutProductCategory,
  YesNo,
  WalkoutPriceRange,
  WalkoutReason,
  PurchasePlan,
  CreateWalkoutRequest,
} from '../../types';

interface WalkoutIntakeModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Called with the new walkout_id after a successful save so the
   * parent can refresh its list. */
  onSaved: (walkoutId: string) => void;
}

interface StaffOption {
  user_id: string;
  name: string;
}

const MOBILE_RE = /^\d{10}$/;

// Roles that can attribute a walkout to a salesperson other than
// themselves. Mirrors backend `_REATTRIBUTE_ROLES`. Lower-tier roles
// (sales staff, cashier, optometrist) are auto-locked to themselves.
const REATTRIBUTE_ROLES = new Set([
  'SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT',
]);

function canReattribute(roles: readonly string[] | undefined, activeRole?: string): boolean {
  if (activeRole && REATTRIBUTE_ROLES.has(activeRole)) return true;
  if (!roles) return false;
  return roles.some(r => REATTRIBUTE_ROLES.has(r));
}

export function WalkoutIntakeModal({ isOpen, onClose, onSaved }: WalkoutIntakeModalProps) {
  const { user } = useAuth();
  const toast = useToast();

  const userId = (user as any)?.id || (user as any)?.user_id || '';
  const userName = (user as any)?.name || (user as any)?.full_name || userId;
  const showSalesPersonPicker = canReattribute(
    (user as any)?.roles,
    (user as any)?.activeRole,
  );

  const [form, setForm] = useState<CreateWalkoutRequest>(emptyForm);
  const [staffOptions, setStaffOptions] = useState<StaffOption[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<keyof CreateWalkoutRequest, string>>>({});
  const [showNoMobileWarning, setShowNoMobileWarning] = useState(false);

  // Reset on open + load staff dropdown options scoped to active store.
  // Always default sales_person_id to the logged-in user (the backend
  // enforces this for non-elevated roles anyway).
  useEffect(() => {
    if (!isOpen) return;
    setForm({ ...emptyForm(), sales_person_id: userId });
    setErrors({});
    setIsSaving(false);
    setShowNoMobileWarning(false);
    if (showSalesPersonPicker) {
      loadStaff();
    } else {
      // Lower-tier role: ensure the user themselves is the only option.
      setStaffOptions(userId ? [{ user_id: userId, name: userName }] : []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // Sales-attributable roles only. Mirrors POS SalespersonPicker (PR #276)
  // so the same staff list drives POS + Walkouts — see POSLayout.tsx.
  // adminUserApi.getUsers required ADMIN role + ignored the storeId filter,
  // which is why this dropdown rendered EMPTY for SUPERADMIN-as-store-staff
  // and for every non-admin (the QA agent saw empty for BV-BOK-01 with 3
  // active sales staff). storesApi.getStoreUsers is public-ish, store-scoped,
  // and accepts the same role + active-only filters POS uses.
  const SALES_ATTRIBUTABLE_ROLES = [
    'STORE_MANAGER',
    // SALES_CASHIER merged into SALES_STAFF (backlog #12).
    'SALES_STAFF',
    'OPTICIAN',
    'CASHIER',
  ];

  const loadStaff = async () => {
    if (!user?.activeStoreId) {
      setStaffOptions([]);
      return;
    }
    try {
      const resp: any = await adminStoreApi.getStoreUsers(user.activeStoreId, {
        roles: SALES_ATTRIBUTABLE_ROLES,
        activeOnly: true,
      });
      const list = resp?.users || resp || [];
      setStaffOptions(
        Array.isArray(list)
          ? list.map((u: any) => ({
              user_id: u.user_id || u.id,
              name: u.name || u.full_name || u.username || u.user_id,
            }))
          : [],
      );
    } catch {
      setStaffOptions([]);
    }
  };

  if (!isOpen) return null;

  const set = <K extends keyof CreateWalkoutRequest>(
    key: K,
    value: CreateWalkoutRequest[K],
  ) => setForm(prev => ({ ...prev, [key]: value }));

  // -------------------------------------------------------------------------
  // Validation — fast client-side; server re-validates.
  // Mobile is now OPTIONAL (some customers don't share their number).
  // Empty is valid; any digits 1-9 long or non-numeric is rejected so
  // the operator doesn't end up with a half-typed junk number.
  // -------------------------------------------------------------------------
  const validate = (): boolean => {
    const e: typeof errors = {};
    if (!form.customer_name?.trim()) e.customer_name = 'Required';
    const mob = (form.mobile || '').trim();
    if (mob !== '' && !MOBILE_RE.test(mob)) {
      e.mobile = 'Mobile must be 10 digits or left blank';
    }
    if (!form.age_group) e.age_group = 'Required';
    if (!form.gender) e.gender = 'Required';
    if (!form.product_interested) e.product_interested = 'Required';
    if (!form.has_prescription) e.has_prescription = 'Required';
    if (!form.displayed_price_range) e.displayed_price_range = 'Required';
    if (!form.required_price_range) e.required_price_range = 'Required';
    if (!form.primary_walkout_reason) e.primary_walkout_reason = 'Required';
    if (!form.purchase_planned_in) e.purchase_planned_in = 'Required';
    if (!form.sales_person_id) e.sales_person_id = 'Required';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const performSave = async () => {
    setIsSaving(true);
    try {
      // Send mobile as the trimmed string (or empty); the server
      // normalizes empty -> None and skips customer auto-create.
      const saved = await walkoutsApi.createWalkout({
        ...form,
        mobile: (form.mobile || '').trim(),
      });
      toast.success(`Walkout logged · ${saved.walkout_id}`);
      // F45 D3 -- surface the reason-driven follow-up policy (non-blocking).
      const action = saved.policy_suggestion?.action;
      if (action === 'MANAGER_ESCALATE') {
        toast.warning('Staff-behaviour flagged — a manager escalation task was created.');
      } else if (action === 'PROMO_VOUCHER') {
        toast.info('Price objection — this customer may be eligible for a promo voucher (see reminder rules).');
      } else if (action === 'RESTOCK_WATCH') {
        toast.info('Availability objection — flagged for a restock follow-up.');
      }
      onSaved(saved.walkout_id);
      onClose();
    } catch (e: any) {
      const msg =
        e?.response?.data?.detail ||
        e?.message ||
        'Could not log walkout';
      toast.error(typeof msg === 'string' ? msg : 'Could not log walkout');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSubmit = async () => {
    if (!validate()) {
      toast.error('Please fix the highlighted fields');
      return;
    }
    // Mobile optional path: open the confirmation modal before saving
    // so the operator can either go back and ask for a number or
    // explicitly acknowledge that follow-up SMS/WhatsApp/call routes
    // will be impossible.
    if ((form.mobile || '').trim() === '') {
      setShowNoMobileWarning(true);
      return;
    }
    await performSave();
  };

  const handleConfirmNoMobile = async () => {
    setShowNoMobileWarning(false);
    await performSave();
  };

  return (
    <>
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-3xl max-h-[92vh] overflow-hidden flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Log Walkout</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Capture the customer who walked out without buying. The
              system auto-creates a customer record if their mobile is new.
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded text-gray-500" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* SECTION 1 — Customer */}
          <Section title="Customer">
            <Field label="Full name *" error={errors.customer_name}>
              <input
                type="text"
                value={form.customer_name}
                onChange={e => set('customer_name', e.target.value)}
                placeholder="As told to you"
                className="input"
                autoFocus
              />
            </Field>
            <Field
              label="Mobile"
              hint="Optional — leave blank if the customer didn't share"
              error={errors.mobile}
            >
              <input
                type="tel"
                inputMode="numeric"
                value={form.mobile || ''}
                onChange={e => set('mobile', e.target.value.replace(/\D/g, '').slice(0, 10))}
                placeholder="10 digits, or leave blank"
                className="input font-mono"
              />
            </Field>
            <Row>
              <Field label="Age group *" error={errors.age_group}>
                <Select
                  value={form.age_group}
                  onChange={(v) => set('age_group', v as AgeGroup)}
                  options={WALKOUT_AGE_GROUPS}
                />
              </Field>
              <Field label="Gender *" error={errors.gender}>
                <Select
                  value={form.gender}
                  onChange={(v) => set('gender', v as WalkoutGender)}
                  options={WALKOUT_GENDERS}
                />
              </Field>
            </Row>
          </Section>

          {/* SECTION 2 — Discovery */}
          <Section title="What were they looking for?">
            <Row>
              <Field label="Product *" error={errors.product_interested}>
                <Select
                  value={form.product_interested}
                  onChange={(v) => set('product_interested', v as WalkoutProductCategory)}
                  options={WALKOUT_PRODUCT_CATEGORIES}
                />
              </Field>
              <Field label="Has Rx? *" error={errors.has_prescription}>
                <Select
                  value={form.has_prescription}
                  onChange={(v) => set('has_prescription', v as YesNo)}
                  options={['YES', 'NO']}
                />
              </Field>
            </Row>
            <Row>
              <Field label="Displayed price range *" error={errors.displayed_price_range}>
                <Select
                  value={form.displayed_price_range}
                  onChange={(v) => set('displayed_price_range', v as WalkoutPriceRange)}
                  options={WALKOUT_PRICE_RANGES}
                />
              </Field>
              <Field label="Required price range *" error={errors.required_price_range}>
                <Select
                  value={form.required_price_range}
                  onChange={(v) => set('required_price_range', v as WalkoutPriceRange)}
                  options={WALKOUT_PRICE_RANGES}
                />
              </Field>
            </Row>
            <Row>
              <Field label="Primary walkout reason *" error={errors.primary_walkout_reason}>
                <Select
                  value={form.primary_walkout_reason}
                  onChange={(v) => set('primary_walkout_reason', v as WalkoutReason)}
                  options={WALKOUT_REASONS}
                />
              </Field>
              <Field label="Secondary reason">
                <Select
                  value={form.secondary_walkout_reason || ''}
                  onChange={(v) =>
                    set('secondary_walkout_reason', (v || undefined) as WalkoutReason | undefined)
                  }
                  options={['', ...WALKOUT_REASONS]}
                />
              </Field>
            </Row>
            <Row>
              <Field label="Brand interest">
                <input
                  type="text"
                  value={form.brand_interest || ''}
                  onChange={e => set('brand_interest', e.target.value)}
                  placeholder="e.g. Ray-Ban"
                  className="input"
                />
              </Field>
              <Field label="Competitor mentioned">
                <input
                  type="text"
                  value={form.competitor_mentioned || ''}
                  onChange={e => set('competitor_mentioned', e.target.value)}
                  placeholder="e.g. Lenskart"
                  className="input"
                />
              </Field>
            </Row>
            <Field label="Purchase planned in *" error={errors.purchase_planned_in}>
              <Select
                value={form.purchase_planned_in}
                onChange={(v) => set('purchase_planned_in', v as PurchasePlan)}
                options={WALKOUT_PURCHASE_PLANS}
              />
            </Field>
          </Section>

          {/* SECTION 3 — Sales attribution */}
          <Section title="Attribution">
            <Field label="Sales person *" error={errors.sales_person_id}>
              {showSalesPersonPicker ? (
                <select
                  value={form.sales_person_id}
                  onChange={e => set('sales_person_id', e.target.value)}
                  className="input"
                >
                  <option value="">— pick —</option>
                  {staffOptions.map(s => (
                    <option key={s.user_id} value={s.user_id}>{s.name}</option>
                  ))}
                </select>
              ) : (
                <div className="flex items-center gap-2 px-3 py-2 border border-gray-200 rounded bg-gray-50 text-sm text-gray-700">
                  <span className="font-medium">{userName || '—'}</span>
                  <span className="text-xs text-gray-400">(you)</span>
                </div>
              )}
            </Field>
          </Section>

          {/* SECTION 4 — Notes */}
          <Section title="Notes">
            <Field label="Action remarks">
              <textarea
                rows={3}
                value={form.action_remarks || ''}
                onChange={e => set('action_remarks', e.target.value)}
                placeholder="Anything that would help the follow-up call?"
                className="input resize-none"
              />
            </Field>
          </Section>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-gray-200 bg-gray-50">
          <button
            onClick={onClose}
            disabled={isSaving}
            className="px-4 py-2 rounded text-sm font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSaving}
            className="px-5 py-2 rounded text-sm font-semibold bg-bv-red-600 text-white hover:bg-bv-red-700 disabled:opacity-50 inline-flex items-center gap-2"
          >
            {isSaving && <Loader2 className="w-4 h-4 animate-spin" />}
            Log walkout
          </button>
        </div>
      </div>
    </div>

    {showNoMobileWarning && (
      <div className="fixed inset-0 z-[60] bg-black/50 flex items-center justify-center p-4">
        <div className="bg-white rounded-xl w-full max-w-md shadow-2xl overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-200 flex items-start gap-3">
            <div className="rounded-full p-2 bg-amber-50 text-amber-700 mt-0.5 shrink-0">
              <PhoneOff className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-gray-900">
                Save without mobile number?
              </h3>
              <p className="text-xs text-gray-500 mt-1">
                The customer didn&apos;t share their phone.
              </p>
            </div>
          </div>
          <div className="px-5 py-4 text-sm text-gray-700">
            You won&apos;t be able to schedule call / WhatsApp / SMS follow-ups,
            only in-person.
          </div>
          <div className="px-5 py-3 bg-gray-50 border-t border-gray-200 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowNoMobileWarning(false)}
              disabled={isSaving}
              className="px-4 py-2 rounded text-sm font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmNoMobile}
              disabled={isSaving}
              className="px-5 py-2 rounded text-sm font-semibold bg-bv-red-600 text-white hover:bg-bv-red-700 disabled:opacity-50 inline-flex items-center gap-2"
            >
              {isSaving && <Loader2 className="w-4 h-4 animate-spin" />}
              Save anyway
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}

// ============================================================================
// Local presentation helpers
// ============================================================================

function emptyForm(): CreateWalkoutRequest {
  return {
    customer_name: '',
    mobile: '',
    age_group: '' as unknown as AgeGroup,
    gender: '' as unknown as WalkoutGender,
    product_interested: '' as unknown as WalkoutProductCategory,
    has_prescription: '' as unknown as YesNo,
    displayed_price_range: '' as unknown as WalkoutPriceRange,
    required_price_range: '' as unknown as WalkoutPriceRange,
    primary_walkout_reason: '' as unknown as WalkoutReason,
    secondary_walkout_reason: undefined,
    brand_interest: '',
    competitor_mentioned: '',
    purchase_planned_in: '' as unknown as PurchasePlan,
    sales_person_id: '',
    action_remarks: '',
  };
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{title}</h3>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3">{children}</div>;
}

function Field({
  label,
  error,
  hint,
  children,
}: {
  label: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="text-xs font-medium text-gray-700 block mb-1">{label}</label>
      {children}
      {hint && !error && (
        <p className="mt-1 text-xs text-gray-500">{hint}</p>
      )}
      {error && (
        <p className="mt-1 text-xs text-red-600 inline-flex items-center gap-1">
          <AlertTriangle className="w-3 h-3" />
          {error}
        </p>
      )}
    </div>
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: readonly string[];
}) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="input"
    >
      <option value="">— pick —</option>
      {options.filter(o => o !== '').map(o => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
}

// Inline className util — mirrors the existing `input` class used by
// AddCustomerModal so the modal looks consistent. We can't import that
// CSS module directly because it's a plain Tailwind class string.
// Pure-CSS fallback so .input renders sanely even if global utility hasn't compiled.
if (typeof document !== 'undefined') {
  const styleId = 'walkout-input-style';
  if (!document.getElementById(styleId)) {
    const s = document.createElement('style');
    s.id = styleId;
    s.textContent = `.input{width:100%;padding:8px 12px;border:1px solid #d1d5db;border-radius:4px;font-size:14px;color:#111827;background:#fff;outline:none}.input:focus{border-color:#fca5a5}`;
    document.head.appendChild(s);
  }
}

export default WalkoutIntakeModal;
