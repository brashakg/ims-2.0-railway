// ============================================================================
// IMS 2.0 — Walkout Detail Page (Pune Incentive Module i, Phase 2)
// ============================================================================
// View + edit a single walkout. RBAC mirrors backend rules in
// backend/api/routers/walkouts.py:
//   - SUPERADMIN/ADMIN: edit any walkout, edit sales_person_id, delete any
//   - STORE_MANAGER/AREA_MANAGER: edit any in store, re-attribute, delete in store
//   - ACCOUNTANT: edit + re-attribute (no delete)
//   - SALES_STAFF/SALES_CASHIER/CASHIER: edit only own walkouts; cannot
//     re-attribute or delete
// Phase 3 will overlay follow-ups + result on this page.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Loader2, Save, Trash2, AlertTriangle } from 'lucide-react';
import { walkoutsApi } from '../../services/api';
import { adminUserApi } from '../../services/api/stores';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  WALKOUT_AGE_GROUPS,
  WALKOUT_GENDERS,
  WALKOUT_PRODUCT_CATEGORIES,
  WALKOUT_PRICE_RANGES,
  WALKOUT_REASONS,
  WALKOUT_PURCHASE_PLANS,
  type AgeGroup,
  type WalkoutGender,
  type WalkoutProductCategory,
  type WalkoutPriceRange,
  type WalkoutReason,
  type PurchasePlan,
  type YesNo,
  type Walkout,
  type UpdateWalkoutRequest,
} from '../../types';
import { FollowUpPanel } from './FollowUpPanel';
import { ResultPanel, WalkoutResultBadge } from './ResultPanel';

const REATTRIBUTE_ROLES = new Set([
  'SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT',
]);
const DELETE_ROLES = new Set([
  'SUPERADMIN', 'STORE_MANAGER', 'AREA_MANAGER', 'ADMIN',
]);
const MOBILE_RE = /^\d{10}$/;

function hasAnyRole(roles: readonly string[] | undefined, activeRole: string | undefined, allowed: Set<string>): boolean {
  if (activeRole && allowed.has(activeRole)) return true;
  if (!roles) return false;
  return roles.some(r => allowed.has(r));
}

interface StaffOption { user_id: string; name: string; }

export function WalkoutDetailPage() {
  const { walkoutId = '' } = useParams<{ walkoutId: string }>();
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();

  const userId = (user as any)?.id || (user as any)?.user_id || '';
  const userRoles = (user as any)?.roles as string[] | undefined;
  const activeRole = (user as any)?.activeRole as string | undefined;

  const canReattribute = hasAnyRole(userRoles, activeRole, REATTRIBUTE_ROLES);
  const canDelete = hasAnyRole(userRoles, activeRole, DELETE_ROLES);

  const [walkout, setWalkout] = useState<Walkout | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState<UpdateWalkoutRequest>({});
  const [staffOptions, setStaffOptions] = useState<StaffOption[]>([]);

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteReason, setDeleteReason] = useState('');

  // Sales-staff-and-below: editable only if they own the walkout.
  const canEdit = useMemo(() => {
    if (!walkout) return false;
    if (canReattribute) return true;
    return walkout.sales_person_id === userId;
  }, [walkout, canReattribute, userId]);

  const loadWalkout = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const w = await walkoutsApi.getWalkout(walkoutId);
      setWalkout(w);
      setDraft({});
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load';
      setError(typeof msg === 'string' ? msg : 'Failed to load');
    } finally {
      setIsLoading(false);
    }
  }, [walkoutId]);

  const loadStaff = useCallback(async () => {
    if (!canReattribute) return;
    try {
      const resp: any = await (adminUserApi as any).getUsers?.({
        storeId: (user as any)?.activeStoreId,
      });
      const list = resp?.users || resp || [];
      setStaffOptions(
        Array.isArray(list)
          ? list.map((u: any) => ({
              user_id: u.user_id || u.id,
              name: u.name || u.full_name || u.username || u.user_id,
            }))
          : []
      );
    } catch {
      setStaffOptions([]);
    }
  }, [canReattribute, user]);

  useEffect(() => {
    loadWalkout();
  }, [loadWalkout]);

  useEffect(() => {
    loadStaff();
  }, [loadStaff]);

  const update = <K extends keyof UpdateWalkoutRequest>(key: K, value: UpdateWalkoutRequest[K]) => {
    setDraft(prev => ({ ...prev, [key]: value }));
  };

  const valueOf = <K extends keyof UpdateWalkoutRequest>(key: K): any => {
    if (key in draft) return (draft as any)[key];
    return walkout ? (walkout as any)[key] : '';
  };

  const isDirty = Object.keys(draft).length > 0;

  const handleSave = async () => {
    if (!walkout || !isDirty) return;
    if (draft.mobile !== undefined) {
      const mob = (draft.mobile || '').trim();
      if (mob !== '' && !MOBILE_RE.test(mob)) {
        toast.error('Mobile must be 10 digits or left blank');
        return;
      }
    }
    setIsSaving(true);
    try {
      const updated = await walkoutsApi.updateWalkout(walkout.walkout_id, draft);
      setWalkout(updated);
      setDraft({});
      toast.success('Walkout updated');
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Save failed';
      toast.error(typeof msg === 'string' ? msg : 'Save failed');
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!walkout || !deleteReason.trim()) {
      toast.error('Please provide a reason');
      return;
    }
    setIsDeleting(true);
    try {
      await walkoutsApi.deleteWalkout(walkout.walkout_id, deleteReason.trim());
      toast.success('Walkout deleted');
      navigate('/walkouts');
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Delete failed';
      toast.error(typeof msg === 'string' ? msg : 'Delete failed');
    } finally {
      setIsDeleting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="p-6 flex items-center justify-center text-gray-500">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        Loading walkout…
      </div>
    );
  }

  if (error || !walkout) {
    return (
      <div className="p-6">
        <Link to="/walkouts" className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 mb-4">
          <ArrowLeft className="w-4 h-4" />
          Back to walkouts
        </Link>
        <div className="card p-8 text-center">
          <AlertTriangle className="w-10 h-10 text-amber-500 mx-auto mb-2" />
          <p className="text-gray-700 font-medium">{error || 'Walkout not found.'}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl">
      <Link to="/walkouts" className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 mb-4">
        <ArrowLeft className="w-4 h-4" />
        Back to walkouts
      </Link>

      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-gray-900">{walkout.customer_name}</h1>
            <WalkoutResultBadge value={walkout.result} />
          </div>
          <p className="text-sm text-gray-500 mt-1">
            <span className="font-mono">{walkout.walkout_id}</span> · {walkout.date_str} · {walkout.store_id}
          </p>
        </div>
        <div className="flex gap-2">
          {canDelete && (
            <button
              type="button"
              onClick={() => setShowDeleteConfirm(true)}
              className="btn-secondary inline-flex items-center gap-1 text-rose-600"
              disabled={isSaving || isDeleting}
            >
              <Trash2 className="w-4 h-4" />
              Delete
            </button>
          )}
          {canEdit && (
            <button
              type="button"
              onClick={handleSave}
              disabled={!isDirty || isSaving}
              className="btn-primary inline-flex items-center gap-1 disabled:opacity-50"
            >
              {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Save changes
            </button>
          )}
        </div>
      </div>

      {!canEdit && (
        <div className="mb-4 px-4 py-2 bg-amber-50 border border-amber-200 rounded text-sm text-amber-800">
          Read-only — only the assigned salesperson and store managers can edit this walkout.
        </div>
      )}

      <div className="space-y-5">
        <Section title="Customer">
          <Row>
            <Field label="Name">
              <input
                className="input"
                value={valueOf('customer_name') || ''}
                onChange={e => update('customer_name', e.target.value)}
                disabled={!canEdit}
              />
            </Field>
            <Field label="Mobile">
              <input
                className="input font-mono"
                value={valueOf('mobile') || ''}
                onChange={e => update('mobile', e.target.value.replace(/\D/g, '').slice(0, 10))}
                disabled={!canEdit}
                maxLength={10}
              />
            </Field>
          </Row>
          <Row>
            <Field label="Age group">
              <Select
                value={valueOf('age_group')}
                onChange={(v) => update('age_group', v as AgeGroup)}
                options={WALKOUT_AGE_GROUPS}
                disabled={!canEdit}
              />
            </Field>
            <Field label="Gender">
              <Select
                value={valueOf('gender')}
                onChange={(v) => update('gender', v as WalkoutGender)}
                options={WALKOUT_GENDERS}
                disabled={!canEdit}
              />
            </Field>
          </Row>
        </Section>

        <Section title="Discovery">
          <Row>
            <Field label="Product interested">
              <Select
                value={valueOf('product_interested')}
                onChange={(v) => update('product_interested', v as WalkoutProductCategory)}
                options={WALKOUT_PRODUCT_CATEGORIES}
                disabled={!canEdit}
              />
            </Field>
            <Field label="Has prescription">
              <Select
                value={valueOf('has_prescription')}
                onChange={(v) => update('has_prescription', v as YesNo)}
                options={['YES', 'NO']}
                disabled={!canEdit}
              />
            </Field>
          </Row>
          <Row>
            <Field label="Displayed price">
              <Select
                value={valueOf('displayed_price_range')}
                onChange={(v) => update('displayed_price_range', v as WalkoutPriceRange)}
                options={WALKOUT_PRICE_RANGES}
                disabled={!canEdit}
              />
            </Field>
            <Field label="Required price">
              <Select
                value={valueOf('required_price_range')}
                onChange={(v) => update('required_price_range', v as WalkoutPriceRange)}
                options={WALKOUT_PRICE_RANGES}
                disabled={!canEdit}
              />
            </Field>
          </Row>
          <Row>
            <Field label="Primary reason">
              <Select
                value={valueOf('primary_walkout_reason')}
                onChange={(v) => update('primary_walkout_reason', v as WalkoutReason)}
                options={WALKOUT_REASONS}
                disabled={!canEdit}
              />
            </Field>
            <Field label="Secondary reason">
              <Select
                value={valueOf('secondary_walkout_reason') || ''}
                onChange={(v) => update('secondary_walkout_reason', (v || null) as any)}
                options={['', ...WALKOUT_REASONS]}
                disabled={!canEdit}
              />
            </Field>
          </Row>
          <Row>
            <Field label="Brand interest">
              <input
                className="input"
                value={valueOf('brand_interest') || ''}
                onChange={e => update('brand_interest', e.target.value)}
                disabled={!canEdit}
              />
            </Field>
            <Field label="Competitor mentioned">
              <input
                className="input"
                value={valueOf('competitor_mentioned') || ''}
                onChange={e => update('competitor_mentioned', e.target.value)}
                disabled={!canEdit}
              />
            </Field>
          </Row>
          <Row>
            <Field label="Purchase planned in">
              <Select
                value={valueOf('purchase_planned_in')}
                onChange={(v) => update('purchase_planned_in', v as PurchasePlan)}
                options={WALKOUT_PURCHASE_PLANS}
                disabled={!canEdit}
              />
            </Field>
          </Row>
        </Section>

        <Section title="Attribution">
          <Field label="Sales person">
            {canReattribute ? (
              <select
                className="input"
                value={valueOf('sales_person_id')}
                onChange={e => update('sales_person_id', e.target.value)}
                disabled={!canEdit}
              >
                <option value={walkout.sales_person_id}>
                  {walkout.sales_person_name || walkout.sales_person_id} (current)
                </option>
                {staffOptions
                  .filter(s => s.user_id !== walkout.sales_person_id)
                  .map(s => (
                    <option key={s.user_id} value={s.user_id}>{s.name}</option>
                  ))}
              </select>
            ) : (
              <div className="px-3 py-2 border border-gray-200 rounded bg-gray-50 text-sm text-gray-700">
                {walkout.sales_person_name || walkout.sales_person_id}
              </div>
            )}
          </Field>
        </Section>

        <Section title="Notes">
          <Field label="Action remarks">
            <textarea
              rows={4}
              className="input resize-none"
              value={valueOf('action_remarks') || ''}
              onChange={e => update('action_remarks', e.target.value)}
              disabled={!canEdit}
            />
          </Field>
        </Section>

        <ResultPanel
          walkout={walkout}
          canEdit={canEdit}
          onChanged={(w) => { setWalkout(w); setDraft({}); }}
        />

        <FollowUpPanel
          walkout={walkout}
          canEdit={canEdit}
          storeId={walkout.store_id}
          onChanged={(w) => { setWalkout(w); setDraft({}); }}
        />
      </div>

      {/* Delete confirm modal */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Delete walkout?</h3>
            <p className="text-sm text-gray-600 mb-3">
              This soft-deletes the row — it disappears from the list but is kept for audit.
            </p>
            <label className="block text-xs text-gray-500 mb-1">Reason</label>
            <textarea
              rows={3}
              className="input resize-none mb-4"
              value={deleteReason}
              onChange={e => setDeleteReason(e.target.value)}
              placeholder="Why is this being deleted?"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => { setShowDeleteConfirm(false); setDeleteReason(''); }}
                disabled={isDeleting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary inline-flex items-center gap-1"
                style={{ background: '#e11d48' }}
                onClick={handleDelete}
                disabled={isDeleting || !deleteReason.trim()}
              >
                {isDeleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        .input {
          width: 100%;
          padding: 8px 12px;
          border: 1px solid #d1d5db;
          border-radius: 4px;
          font-size: 14px;
          color: #111827;
          background: #fff;
          outline: none;
        }
        .input:focus { border-color: #fca5a5; }
        .input:disabled { background: #f9fafb; color: #6b7280; }
      `}</style>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-5 space-y-3">
      <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{title}</h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">{children}</div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500 mb-1 block">{label}</span>
      {children}
    </label>
  );
}

function Select({
  value, onChange, options, disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  options: readonly string[];
  disabled?: boolean;
}) {
  return (
    <select
      value={value || ''}
      onChange={e => onChange(e.target.value)}
      className="input"
      disabled={disabled}
    >
      {options.map(o => <option key={o} value={o}>{o || '— none —'}</option>)}
    </select>
  );
}

export default WalkoutDetailPage;
