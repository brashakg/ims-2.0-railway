// ============================================================================
// IMS 2.0 — Employee Onboarding Wizard
// ============================================================================
// COUNCIL RULING §5 (Settings/Permissions/Nav redesign): repurpose the redundant
// SetupPage store wizard IN-PLACE into a hardened Employee Onboarding wizard over
// the EXISTING, mature `create_user` endpoint (no new endpoint). The mature path
// already enforces the escalation guard (can_assign_roles), sanitizes module
// access, and forces a password change on first login (must_change_password,
// BUG-027). This wizard is a thin, friendly assembly over it.
//
// COUNCIL RULING §3: stores + legal entities are CREATED/EDITED only on the
// canonical /organization screen. Here they are read-only — the wizard offers
// store ASSIGNMENT, nothing more.
//
// Steps:
//   1. Who          — name, Indian-mobile (#367 validator), email, photo.
//   2. Role(s)      — friendly plain-English role list (single default;
//                     multi-role behind an "Advanced" reveal). Capped at the
//                     creating admin's own level (mirrors backend can_assign_roles).
//   3. Store        — defaults to the admin's active store; roles 4-7 get the
//                     geo-fence automatically (shown as a plain note).
//   4. Permissions  — PLACEHOLDER ONLY. Read-only "Uses standard <Role>
//                     permissions" card. The editable override step is PR2.x,
//                     gated on the per-user permission layer. NOT built here.
//   5. Credentials  — username + temp password (or generate) + copyable handoff
//                     card. must_change_password=True is PRESERVED (no skip toggle).
//
// On submit: POST /users (adminUserApi.createUser) then assign each store via
// POST /users/{id}/assign-store. A role-above-actor attempt surfaces the backend
// 403 reason cleanly.
import { useState, useEffect, useMemo } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { adminStoreApi, adminUserApi } from '../../services/api';
// Direct import (not the api barrel): the barrel re-export of newly-added service
// objects intermittently fails to resolve for consumers (TS2614) — the team's
// established reliable pattern is to import the service straight from its module.
import { employeeDocApi } from '../../services/api/hr';
import type { EmployeeDocType } from '../../services/api/hr';
import { validatePhone } from '../../utils/validators';
import { ROLE_HIERARCHY } from './settingsTypes';
import { PermissionDeltaEditor } from '../../components/permissions/PermissionDeltaEditor';
import {
  Building, Users, Plus, Shield, X, ChevronRight, ChevronLeft, CheckCircle,
  AlertTriangle, Copy, RefreshCw, Lock, MapPin, UserPlus, Camera,
  FileText, Upload, Trash2, Loader2, CreditCard, Eye,
} from 'lucide-react';
import clsx from 'clsx';

// ---------------------------------------------------------------------------
// Store shape (read-only) — used only for the onboarding store-assignment picker.
// ---------------------------------------------------------------------------
interface StoreConfig {
  id: string;
  name: string;
  code: string;
  city: string;
  state: string;
  isActive: boolean;
}

// ---------------------------------------------------------------------------
// Friendly, plain-English role catalogue. The owner is NOT a developer, so the
// list reads as sentences, not role codes. `level` mirrors ROLE_HIERARCHY so we
// can hide anything above the creating admin's own level (UI half of the backend
// can_assign_roles guard — the server is still the source of truth).
// `geoFenced` marks the store-staff tiers (roles 4-7) whose login is fenced to
// their assigned store automatically (SYSTEM_INTENT geo-fence).
// ---------------------------------------------------------------------------
interface RoleOption {
  id: string;
  label: string;
  desc: string;
  level: number;
  geoFenced: boolean;
}

const ROLE_CATALOGUE: RoleOption[] = [
  // Sales Cashier was merged into Sales Staff (backlog #12) -- one floor-sales
  // role now: rings up sales, takes payments, opens the drawer, discount up to 10%.
  { id: 'SALES_STAFF', label: 'Sales Staff', desc: 'Rings up sales, takes payments, opens the cash drawer, and searches the catalogue. Discount up to 10%.', level: ROLE_HIERARCHY.SALES_STAFF, geoFenced: true },
  { id: 'CASHIER', label: 'Cashier', desc: 'Takes payments only — no selling, no discounts.', level: ROLE_HIERARCHY.CASHIER ?? 3, geoFenced: true },
  { id: 'OPTOMETRIST', label: 'Optometrist', desc: 'Runs eye tests and writes prescriptions. No selling.', level: ROLE_HIERARCHY.OPTOMETRIST, geoFenced: true },
  { id: 'WORKSHOP_STAFF', label: 'Workshop / Fitting', desc: 'Updates job status — frame fitting, lens mounting. No selling.', level: ROLE_HIERARCHY.WORKSHOP_STAFF, geoFenced: true },
  { id: 'STORE_MANAGER', label: 'Store Manager', desc: 'Runs one store end-to-end. Discount up to 20%.', level: ROLE_HIERARCHY.STORE_MANAGER, geoFenced: false },
  { id: 'ACCOUNTANT', label: 'Accountant', desc: 'Finance, GST and reconciliation. No POS or inventory.', level: ROLE_HIERARCHY.ACCOUNTANT, geoFenced: false },
  { id: 'CATALOG_MANAGER', label: 'Catalog Manager', desc: 'Owns the head-office product catalogue and pricing.', level: ROLE_HIERARCHY.CATALOG_MANAGER, geoFenced: false },
  { id: 'AREA_MANAGER', label: 'Area Manager', desc: 'Oversees several stores. Discount up to 25%.', level: ROLE_HIERARCHY.AREA_MANAGER, geoFenced: false },
  { id: 'ADMIN', label: 'Admin (Director)', desc: 'Head-office administration across all stores and staff.', level: ROLE_HIERARCHY.ADMIN, geoFenced: false },
  { id: 'SUPERADMIN', label: 'Superadmin (CEO)', desc: 'Full control of the entire system, including AI.', level: ROLE_HIERARCHY.SUPERADMIN, geoFenced: false },
];

const ROLE_LABEL: Record<string, string> = Object.fromEntries(
  ROLE_CATALOGUE.map((r) => [r.id, r.label]),
);

// ---------------------------------------------------------------------------
// New-employee form state.
// ---------------------------------------------------------------------------
interface NewEmployee {
  name: string;
  email: string;
  phone: string;
  photoDataUrl: string;
  roles: string[];
  assignedStores: string[];
  primaryStore: string;
  username: string;
  tempPassword: string;
  // Govt-ID + statutory numbers captured at onboarding (all optional).
  aadhaarNo: string;
  panNo: string;
  uanNo: string;
  esicNo: string;
  // Per-user permission overrides set during onboarding (backlog #13). All
  // OPTIONAL: by default the person uses their standard role (empty = DARK).
  //  - permissions: two-sided capability override { grant, deny }
  //  - moduleAccess: deny-only module/screen map { moduleKey: bool }
  //  - discountCap: per-user discount-cap override (undefined = role baseline)
  permissions: { grant?: Record<string, boolean>; deny?: Record<string, boolean> };
  moduleAccess: Record<string, boolean>;
  discountCap?: number;
}

// A document staged in the wizard before the employee exists. The actual upload
// (to GridFS, behind RBAC) happens AFTER the account is created, against the
// new employee_id. `slot` ties a single-file category (Aadhaar/PAN/etc.) to its
// picker; OTHER documents accumulate as multiple entries with slot 'OTHER'.
type DocSlot = EmployeeDocType;
interface StagedDoc {
  key: string;        // local unique id for React + remove
  slot: DocSlot;
  docType: EmployeeDocType;
  file: File;
  status: 'staged' | 'uploading' | 'done' | 'error';
  error?: string;
}

// Single-file ID/paperwork slots shown as labelled pickers (one file each).
const DOC_SLOTS: { slot: DocSlot; label: string; hint: string }[] = [
  { slot: 'AADHAAR', label: 'Aadhaar card', hint: 'PDF or photo' },
  { slot: 'PAN', label: 'PAN card', hint: 'PDF or photo' },
  { slot: 'UAN_PF', label: 'PF / UAN document', hint: 'PDF or photo' },
  { slot: 'ESIC', label: 'ESIC document', hint: 'PDF or photo' },
  { slot: 'RESUME', label: 'Resume / CV', hint: 'PDF or photo' },
  { slot: 'PHOTO', label: 'Passport-size photo', hint: 'JPG / PNG' },
];

// Client-side validation of an upload before it is even staged. The server is
// the authority (it re-checks MIME + 25 MB), but failing fast here is friendlier.
const MAX_DOC_BYTES = 25 * 1024 * 1024;
const ACCEPTED_DOC_MIME = new Set([
  'image/jpeg', 'image/jpg', 'image/png', 'image/heic', 'image/heif',
  'image/webp', 'application/pdf',
]);
function validateDocFile(file: File): string | null {
  if (file.size === 0) return 'That file is empty.';
  if (file.size > MAX_DOC_BYTES) return 'File is larger than 25 MB.';
  // Some browsers leave type blank for odd files; fall back to the extension.
  const mime = (file.type || '').toLowerCase();
  const okByMime = ACCEPTED_DOC_MIME.has(mime);
  const okByExt = /\.(pdf|jpe?g|png|webp|heic|heif)$/i.test(file.name);
  if (!okByMime && !okByExt) return 'Only PDF or image files are allowed.';
  return null;
}

// Inline, fail-soft format hints (mirror the backend's light validators). These
// only WARN — onboarding is never blocked by an ID-format quirk.
function panWarning(v: string): string | null {
  if (!v) return null;
  return /^[A-Za-z]{5}[0-9]{4}[A-Za-z]$/.test(v.trim())
    ? null : 'PAN is usually 10 characters like AAAAA9999A.';
}
function aadhaarWarning(v: string): string | null {
  if (!v) return null;
  return /^\d{12}$/.test(v.replace(/[\s-]/g, ''))
    ? null : 'Aadhaar is usually 12 digits.';
}
// Show only the last 4 digits of an Aadhaar number for display.
function maskAadhaar(v: string): string {
  const digits = v.replace(/\D/g, '');
  if (digits.length < 4) return v;
  return `XXXX XXXX ${digits.slice(-4)}`;
}

const TOTAL_STEPS = 6;

// BUG-132: never ship a default credential ('admin123') in the frontend bundle.
// Generate a strong random temp password per new-employee form -- a usable
// default that is NOT a known constant; the new user is forced to change it on
// first login (must_change_password preserved server-side).
function randomTempPassword(): string {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
  const arr = new Uint32Array(12);
  (window.crypto || (window as unknown as { msCrypto: Crypto }).msCrypto).getRandomValues(arr);
  return Array.from(arr, (n) => chars[n % chars.length]).join('');
}

function defaultEmployee(): NewEmployee {
  return {
    name: '', email: '', phone: '', photoDataUrl: '',
    roles: [], assignedStores: [], primaryStore: '',
    username: '', tempPassword: randomTempPassword(),
    aadhaarNo: '', panNo: '', uanNo: '', esicNo: '',
    // Default: standard role (no overrides) -> empty maps / undefined cap.
    permissions: {}, moduleAccess: {}, discountCap: undefined,
  };
}

// ---------------------------------------------------------------------------
// PAGE
// ---------------------------------------------------------------------------
export default function SetupPage() {
  const { user } = useAuth();
  const toast = useToast();
  // Stores are loaded read-only so the wizard can offer store assignment. They
  // are CREATED/EDITED only on /organization (council ruling §3).
  const [stores, setStores] = useState<StoreConfig[]>([]);
  const [showWizard, setShowWizard] = useState(false);

  useEffect(() => {
    adminStoreApi.getStores().then((data: any) => {
      const list = data?.stores || data;
      if (Array.isArray(list)) {
        setStores(list.map((s: any) => ({
          id: s.store_id || s.store_code || s.id,
          name: s.store_name || s.name || '',
          code: s.store_code || s.store_id || '',
          city: s.city || '',
          state: s.state || '',
          isActive: s.is_active !== false,
        })));
      }
    }).catch(() => {});
  }, []);

  // Gate: who may create users. ADMIN/SUPERADMIN always, plus STORE_MANAGER /
  // AREA_MANAGER (who can create roles at or below their own level). The backend
  // create_user is still the authority (can_assign_roles) -- this is only which
  // roles see the screen.
  const actorLevel = Math.max(
    0,
    ...(user?.roles || []).map((r: string) => ROLE_HIERARCHY[r] || 0),
  );
  const canOnboard = actorLevel >= ROLE_HIERARCHY.STORE_MANAGER; // >= 6

  if (!canOnboard) {
    return (
      <div className="max-w-4xl mx-auto p-6 text-center">
        <Shield className="w-12 h-12 text-gray-400 mx-auto mb-3" />
        <h1 className="text-lg font-semibold text-gray-900">Staff Onboarding</h1>
        <p className="text-gray-500 mt-1">
          Onboarding new staff is restricted to Store Managers and above.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-4 tablet:p-6 space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Staff Onboarding</h1>
          <p className="text-sm text-gray-500 mt-0.5 flex items-center gap-1">
            <Building className="w-3.5 h-3.5" />
            Stores &amp; entities are managed on the Organization screen.
          </p>
        </div>
        <button
          onClick={() => setShowWizard(true)}
          className="flex items-center gap-1.5 px-4 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700 whitespace-nowrap"
        >
          <UserPlus className="w-4 h-4" /> Onboard Employee
        </button>
      </div>

      <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800 flex gap-2">
        <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
        <span>
          The wizard walks you through the few things a new account needs — who
          they are, what they do, where they work, and a login. It takes about a
          minute. The new staff member is asked to set their own password the
          first time they sign in.
        </span>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-8 text-center text-gray-500">
        <Users className="w-12 h-12 mx-auto mb-2 opacity-40" />
        <p className="text-sm">
          Use <span className="font-semibold text-gray-700">Onboard Employee</span> to add a new staff account.
        </p>
        <p className="text-xs mt-1">
          Manage existing staff (edit role, reset password, deactivate) under
          Settings &rarr; Users &amp; Roles.
        </p>
      </div>

      {showWizard && (
        <OnboardingWizard
          stores={stores}
          actorLevel={actorLevel}
          defaultStoreId={user?.activeStoreId || ''}
          onClose={() => setShowWizard(false)}
          onCreated={(name) => {
            toast.success(`${name || 'Employee'} onboarded successfully`);
            setShowWizard(false);
          }}
          onError={(msg) => toast.error(msg)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// WIZARD
// ---------------------------------------------------------------------------
function OnboardingWizard({
  stores, actorLevel, defaultStoreId, onClose, onCreated, onError,
}: {
  stores: StoreConfig[];
  actorLevel: number;
  defaultStoreId: string;
  onClose: () => void;
  onCreated: (name: string) => void;
  onError: (msg: string) => void;
}) {
  const [step, setStep] = useState(1);
  const [advancedRoles, setAdvancedRoles] = useState(false);
  // Step-4: opt-in to editing per-user overrides (default off = standard role).
  const [customizePerms, setCustomizePerms] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [copied, setCopied] = useState(false);
  // Documents staged in the wizard, uploaded AFTER the account is created.
  const [stagedDocs, setStagedDocs] = useState<StagedDoc[]>([]);
  // Once the account is created we keep its id so a partial upload failure can be
  // retried without re-creating the user (the account already exists).
  const [createdUserId, setCreatedUserId] = useState<string>('');
  const [form, setForm] = useState<NewEmployee>(() => {
    const base = defaultEmployee();
    // Default to the admin's active store (council §5: store step defaults to
    // the active store). Only if it's a real, assignable store in the list.
    if (defaultStoreId && stores.some((s) => s.id === defaultStoreId)) {
      base.assignedStores = [defaultStoreId];
      base.primaryStore = defaultStoreId;
    }
    return base;
  });

  // If the store list arrives after the wizard opened, seed the active store.
  useEffect(() => {
    if (!form.assignedStores.length && defaultStoreId && stores.some((s) => s.id === defaultStoreId)) {
      setForm((p) => ({ ...p, assignedStores: [defaultStoreId], primaryStore: defaultStoreId }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stores]);

  // Roles the creating admin is actually allowed to assign (UI mirror of the
  // backend can_assign_roles ceiling). Anything strictly above the actor's level
  // is hidden so the owner is never offered an account they cannot create.
  const assignableRoles = useMemo(
    () => ROLE_CATALOGUE.filter((r) => r.level <= actorLevel),
    [actorLevel],
  );

  const set = (patch: Partial<NewEmployee>) => setForm((p) => ({ ...p, ...patch }));

  // Auto-username from the name, used when the admin doesn't type one.
  const autoUsername = useMemo(
    () => form.name.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '').slice(0, 20),
    [form.name],
  );
  const effectiveUsername = form.username || autoUsername;

  const primaryRole = form.roles[0] || '';
  const primaryRoleOpt = ROLE_CATALOGUE.find((r) => r.id === primaryRole);
  const isGeoFenced = form.roles.some((rid) => ROLE_CATALOGUE.find((r) => r.id === rid)?.geoFenced);
  const fenceStore = stores.find((s) => s.id === form.primaryStore || s.id === form.assignedStores[0]);

  // ---- per-step validation -------------------------------------------------
  const phoneError = form.phone ? validatePhone(form.phone) : 'A mobile number is required.';
  // Email is REQUIRED: the backend UserCreate needs a real EmailStr, and we no
  // longer auto-fill a fake .local address. So an empty or malformed value is an
  // error (was previously "optional / leave blank").
  const emailError = !form.email.trim()
    ? 'An email address is required.'
    : !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())
      ? 'Enter a valid email address.'
      : null;

  const stepValid = (s: number): string | null => {
    if (s === 1) {
      if (form.name.trim().length < 2) return "Enter the employee's full name.";
      if (phoneError) return phoneError;
      if (emailError) return emailError;
      return null;
    }
    if (s === 2) {
      if (!form.roles.length) return 'Pick at least one role.';
      return null;
    }
    if (s === 3) {
      // Store-staff roles MUST have a store (the geo-fence has to point somewhere).
      if (isGeoFenced && !form.assignedStores.length) {
        return 'This role logs in at a store, so at least one store is required.';
      }
      return null;
    }
    if (s === 5) {
      if (effectiveUsername.length < 3) return 'Username must be at least 3 characters.';
      if ((form.tempPassword || '').length < 8) return 'Temporary password must be at least 8 characters.';
      return null;
    }
    return null;
  };

  // Light conflict guard (council §5): warn, don't block, on an odd role+store mix.
  const conflictWarning = useMemo((): string | null => {
    if (step < 3) return null;
    if (isGeoFenced && form.assignedStores.length > 1 && primaryRole !== 'AREA_MANAGER') {
      return 'Store staff usually work at one store. You have assigned more than one — they will be able to log in at any of them.';
    }
    if (primaryRole === 'AREA_MANAGER' && form.assignedStores.length === 1) {
      return 'An Area Manager normally oversees several stores. Only one is assigned.';
    }
    if ((primaryRole === 'ADMIN' || primaryRole === 'SUPERADMIN') && form.assignedStores.length) {
      return 'Admins work across all stores, so a store assignment is usually not needed.';
    }
    return null;
  }, [step, isGeoFenced, form.assignedStores, primaryRole]);

  const goNext = () => {
    const err = stepValid(step);
    if (err) { onError(err); return; }
    setSubmitError('');
    setStep((s) => Math.min(TOTAL_STEPS, s + 1));
  };
  const goBack = () => (step > 1 ? setStep((s) => s - 1) : onClose());

  const toggleRole = (roleId: string) => {
    setForm((p) => {
      const has = p.roles.includes(roleId);
      // Single-role default: when Advanced is OFF, picking a role REPLACES the
      // selection. With Advanced ON we accumulate multiple roles.
      if (!advancedRoles) return { ...p, roles: has ? [] : [roleId] };
      return { ...p, roles: has ? p.roles.filter((r) => r !== roleId) : [...p.roles, roleId] };
    });
  };

  const toggleStore = (storeId: string) => {
    setForm((p) => {
      const has = p.assignedStores.includes(storeId);
      const next = has ? p.assignedStores.filter((s) => s !== storeId) : [...p.assignedStores, storeId];
      let primary = p.primaryStore;
      if (has && primary === storeId) primary = next[0] || '';
      if (!has && !primary) primary = storeId;
      return { ...p, assignedStores: next, primaryStore: primary };
    });
  };

  const onPhoto = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => set({ photoDataUrl: String(reader.result) });
    reader.readAsDataURL(file);
  };

  const copyHandoff = async () => {
    const lines = [
      `IMS 2.0 login for ${form.name || 'new staff'}`,
      `Username: ${effectiveUsername}`,
      `Temporary password: ${form.tempPassword}`,
      `Sign in at the IMS 2.0 app and set your own password when asked.`,
    ].join('\n');
    try {
      await navigator.clipboard.writeText(lines);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      onError('Could not copy automatically — please copy the details manually.');
    }
  };

  // Upload the staged documents against an existing employee_id, one at a time so
  // a slow/large file doesn't stall the rest and per-file status is visible.
  // Returns the count that failed (0 = all good). Skips ones already 'done' so a
  // retry only re-attempts the failures.
  const uploadStagedDocs = async (employeeId: string): Promise<number> => {
    let failed = 0;
    for (const doc of stagedDocs) {
      if (doc.status === 'done') continue;
      setStagedDocs((prev) => prev.map((d) =>
        d.key === doc.key ? { ...d, status: 'uploading', error: undefined } : d));
      try {
        await employeeDocApi.upload(employeeId, doc.file, doc.docType);
        setStagedDocs((prev) => prev.map((d) =>
          d.key === doc.key ? { ...d, status: 'done' } : d));
      } catch (e: any) {
        failed += 1;
        const detail = e?.response?.data?.detail || e?.message || 'Upload failed.';
        setStagedDocs((prev) => prev.map((d) =>
          d.key === doc.key
            ? { ...d, status: 'error', error: typeof detail === 'string' ? detail : 'Upload failed.' }
            : d));
      }
    }
    return failed;
  };

  const submit = async () => {
    // The credential step (5) carries the only hard requirements on the final
    // submit; the documents step (6) is entirely optional.
    const err = stepValid(5);
    if (err) { onError(err); return; }
    setSubmitting(true);
    setSubmitError('');
    try {
      // If the account already exists (a previous submit created it but a doc
      // upload failed), DON'T re-create it -- just retry the remaining uploads.
      let newUserId = createdUserId;
      if (!newUserId) {
        // ONE canonical user path: POST /users via create_user. The endpoint
        // enforces the escalation guard (can_assign_roles), sanitizes module
        // access, and -- because mustChangePassword:true -- forces a password
        // change on first login (BUG-027 preserved; no skip toggle exists).
        // Per-user overrides (backlog #13): only sent when the admin actually
        // customised them (toggle on AND a non-empty map / a set cap), so the
        // common onboarding stays DARK (standard role) server-side.
        const hasPermOverride =
          customizePerms &&
          ((form.permissions.grant && Object.keys(form.permissions.grant).length > 0) ||
            (form.permissions.deny && Object.keys(form.permissions.deny).length > 0));
        const hasModuleOverride =
          customizePerms && Object.keys(form.moduleAccess).length > 0;
        const hasCapOverride = customizePerms && form.discountCap != null;

        const created = await adminUserApi.createUser({
          name: form.name.trim(),
          email: form.email.trim(),
          phone: form.phone || undefined,
          roles: form.roles,
          storeIds: form.assignedStores,
          primaryStoreId: form.primaryStore || form.assignedStores[0] || undefined,
          username: effectiveUsername,
          password: form.tempPassword,
          mustChangePassword: true,
          // Govt-ID + statutory numbers (optional; server fail-soft validates).
          aadhaarNo: form.aadhaarNo.replace(/[\s-]/g, '') || undefined,
          panNo: form.panNo.trim().toUpperCase() || undefined,
          uanNo: form.uanNo.trim() || undefined,
          esicNo: form.esicNo.trim() || undefined,
          // Per-user permission overrides set in step 4 (omitted when DARK).
          permissions: hasPermOverride ? form.permissions : undefined,
          moduleAccess: hasModuleOverride ? form.moduleAccess : undefined,
          discountCap: hasCapOverride ? form.discountCap : undefined,
        });

        newUserId = created?.user_id || '';
        setCreatedUserId(newUserId);

        // Assign each store explicitly via the dedicated endpoint so a store grant
        // is recorded even if create-time store_ids needs reinforcing. Best-effort:
        // the account already exists, so a store-assign hiccup must not fail the
        // whole onboarding -- surface a soft warning instead.
        if (newUserId && form.assignedStores.length) {
          const results = await Promise.allSettled(
            form.assignedStores.map((sid) => adminUserApi.assignStore(newUserId, sid)),
          );
          if (results.some((r) => r.status === 'rejected')) {
            onError('Account created, but one or more store assignments need to be re-applied under Users & Roles.');
          }
        }
      }

      // Upload staged documents against the new employee_id. A doc failure must
      // NOT discard the created account -- keep the wizard open so the admin can
      // retry just the failed files (Create Account becomes "Retry uploads").
      if (newUserId && stagedDocs.length) {
        const failedCount = await uploadStagedDocs(newUserId);
        if (failedCount > 0) {
          setSubmitError(
            `Account created for ${form.name.trim()}, but ${failedCount} document${failedCount > 1 ? 's' : ''} failed to upload. Fix and retry, or finish and add them later under HR.`,
          );
          setSubmitting(false);
          return; // stay open so the admin can retry / finish
        }
      }

      onCreated(form.name.trim());
    } catch (e: any) {
      // Surface the backend reason cleanly -- notably the escalation 403
      // ("You cannot assign a role above your own level: <ROLE>").
      const detail = e?.response?.data?.detail || e?.message || 'Failed to onboard employee.';
      setSubmitError(typeof detail === 'string' ? detail : 'Failed to onboard employee.');
      onError(typeof detail === 'string' ? detail : 'Failed to onboard employee.');
    } finally {
      setSubmitting(false);
    }
  };

  // ---- document staging handlers -------------------------------------------
  // Single-file slots REPLACE any existing file in that slot; OTHER accumulates.
  const stageSlotFile = (slot: DocSlot, file: File | undefined) => {
    if (!file) return;
    const vErr = validateDocFile(file);
    if (vErr) { onError(vErr); return; }
    setStagedDocs((prev) => {
      const withoutSlot = slot === 'OTHER' ? prev : prev.filter((d) => d.slot !== slot);
      return [
        ...withoutSlot,
        { key: `${slot}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          slot, docType: slot, file, status: 'staged' },
      ];
    });
  };
  const stageOtherFiles = (files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach((f) => stageSlotFile('OTHER', f));
  };
  const removeStaged = (key: string) =>
    setStagedDocs((prev) => prev.filter((d) => d.key !== key));

  // Owner directive: ID-numbers + documents are SUPERADMIN/ADMIN ONLY (sensitive
  // govt-ID PII). actorLevel >= ADMIN means the actor is ADMIN or SUPERADMIN; a
  // lower actor (STORE_MANAGER/AREA_MANAGER) never sees the Documents step, and the
  // backend also 403s them on the document endpoints.
  const canManageDocs = actorLevel >= ROLE_HIERARCHY.ADMIN;
  const STEP_TITLES = canManageDocs
    ? ['Who', 'Role', 'Store', 'Permissions', 'Login', 'Documents']
    : ['Who', 'Role', 'Store', 'Permissions', 'Login'];

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[92vh] overflow-y-auto">
        {/* Header + stepper */}
        <div className="p-5 border-b border-gray-200 sticky top-0 bg-white z-10">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-gray-900">Onboard New Employee</h3>
              <p className="text-xs text-gray-500">Step {step} of {TOTAL_STEPS} — {STEP_TITLES[step - 1]}</p>
            </div>
            <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded" aria-label="Close">
              <X className="w-5 h-5" />
            </button>
          </div>
          <div className="flex items-center gap-1.5 mt-3">
            {STEP_TITLES.map((t, i) => (
              <div key={t} className="flex-1">
                <div className={clsx('h-1.5 rounded-full transition-colors',
                  i + 1 < step ? 'bg-bv-red-600' : i + 1 === step ? 'bg-bv-red-400' : 'bg-gray-200')} />
              </div>
            ))}
          </div>
        </div>

        <div className="p-5 space-y-4">
          {/* ============ STEP 1: WHO ============ */}
          {step === 1 && (
            <>
              <h4 className="font-medium text-gray-900">Who is joining?</h4>
              <div className="flex items-start gap-4">
                <label className="flex-shrink-0 cursor-pointer">
                  <div className="w-20 h-20 rounded-full bg-gray-100 border-2 border-dashed border-gray-300 flex items-center justify-center overflow-hidden hover:border-bv-red-400">
                    {form.photoDataUrl
                      ? <img src={form.photoDataUrl} alt="" className="w-full h-full object-cover" />
                      : <Camera className="w-6 h-6 text-gray-400" />}
                  </div>
                  <input type="file" accept="image/*" className="hidden" onChange={onPhoto} />
                  <span className="block text-[11px] text-gray-400 text-center mt-1">Photo (optional)</span>
                </label>
                <div className="flex-1 space-y-3">
                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Full Name *</label>
                    <input value={form.name} onChange={(e) => set({ name: e.target.value })}
                      placeholder="e.g. Priya Sharma"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-bv-red-200 focus:border-bv-red-400 outline-none" />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Mobile Number *</label>
                    <input value={form.phone}
                      onChange={(e) => set({ phone: e.target.value.replace(/[^\d]/g, '').slice(0, 10) })}
                      inputMode="numeric" placeholder="10-digit mobile (starts 6-9)"
                      className={clsx('w-full px-3 py-2 border rounded-lg text-sm outline-none focus:ring-2',
                        form.phone && phoneError ? 'border-red-400 focus:ring-red-200' : 'border-gray-300 focus:ring-bv-red-200 focus:border-bv-red-400')} />
                    {form.phone && phoneError && <p className="text-[11px] text-red-600 mt-1">{phoneError}</p>}
                  </div>
                </div>
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">Email *</label>
                <input type="email" value={form.email} onChange={(e) => set({ email: e.target.value })}
                  placeholder="name@example.com"
                  className={clsx('w-full px-3 py-2 border rounded-lg text-sm outline-none focus:ring-2',
                    form.email && emailError ? 'border-red-400 focus:ring-red-200' : 'border-gray-300 focus:ring-bv-red-200 focus:border-bv-red-400')} />
                {form.email && emailError && <p className="text-[11px] text-red-600 mt-1">{emailError}</p>}
              </div>
            </>
          )}

          {/* ============ STEP 2: ROLE(S) ============ */}
          {step === 2 && (
            <>
              <div className="flex items-center justify-between">
                <h4 className="font-medium text-gray-900">What will they do?</h4>
                <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer">
                  <input type="checkbox" checked={advancedRoles}
                    onChange={(e) => {
                      setAdvancedRoles(e.target.checked);
                      // Leaving advanced mode collapses to a single role.
                      if (!e.target.checked && form.roles.length > 1) set({ roles: [form.roles[0]] });
                    }}
                    className="rounded border-gray-300 text-bv-red-600 focus:ring-bv-red-400" />
                  Advanced (assign more than one role)
                </label>
              </div>
              <p className="text-xs text-gray-500">
                {advancedRoles
                  ? 'Tick every role this person needs. Most staff need just one.'
                  : 'Pick the one role that best describes this person.'}
              </p>
              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-2">
                {assignableRoles.map((role) => {
                  const selected = form.roles.includes(role.id);
                  return (
                    <button key={role.id} type="button" onClick={() => toggleRole(role.id)}
                      className={clsx('p-3 rounded-lg border-2 text-left transition-all',
                        selected ? 'border-bv-red-600 bg-bv-red-50' : 'border-gray-200 hover:border-gray-300')}>
                      <div className="flex items-center gap-2">
                        <div className={clsx('w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0',
                          selected ? 'border-bv-red-600 bg-bv-red-600' : 'border-gray-300')}>
                          {selected && <CheckCircle className="w-3.5 h-3.5 text-white" />}
                        </div>
                        <span className="text-sm font-semibold text-gray-900">{role.label}</span>
                      </div>
                      <p className="text-xs text-gray-500 mt-1 ml-7">{role.desc}</p>
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {/* ============ STEP 3: STORE ============ */}
          {step === 3 && (
            <>
              <h4 className="font-medium text-gray-900">Where do they work?</h4>
              {isGeoFenced && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs text-blue-800 flex gap-2">
                  <MapPin className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  <span>
                    {primaryRoleOpt ? `A ${primaryRoleOpt.label} ` : 'This role '}
                    can only log in while at their assigned store
                    {fenceStore ? ` (${fenceStore.name})` : ''}. This happens automatically — you don&apos;t need to set anything.
                  </span>
                </div>
              )}
              {!stores.length ? (
                <div className="text-sm text-gray-500 bg-gray-50 rounded-lg p-4 text-center">
                  No stores found. Create stores on the Organization screen first.
                </div>
              ) : (
                <div className="space-y-2">
                  {stores.map((s) => {
                    const selected = form.assignedStores.includes(s.id);
                    return (
                      <div key={s.id}
                        className={clsx('w-full p-3 rounded-lg border-2 flex items-center justify-between gap-3',
                          selected ? 'border-bv-red-600 bg-bv-red-50' : 'border-gray-200')}>
                        <button type="button" onClick={() => toggleStore(s.id)} className="flex items-center gap-3 text-left flex-1">
                          <div className={clsx('w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0',
                            selected ? 'border-bv-red-600 bg-bv-red-600' : 'border-gray-300')}>
                            {selected && <CheckCircle className="w-3.5 h-3.5 text-white" />}
                          </div>
                          <div>
                            <p className="text-sm font-medium text-gray-900">
                              {s.name}{!s.isActive && <span className="ml-2 text-[10px] uppercase text-gray-400">inactive</span>}
                            </p>
                            <p className="text-xs text-gray-500">{[s.code, s.city].filter(Boolean).join(' · ')}</p>
                          </div>
                        </button>
                        {selected && form.assignedStores.length > 1 && (
                          <button type="button"
                            onClick={() => set({ primaryStore: s.id })}
                            className={clsx('text-xs px-2 py-1 rounded flex-shrink-0',
                              form.primaryStore === s.id ? 'bg-bv-red-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200')}>
                            {form.primaryStore === s.id ? 'Primary' : 'Set primary'}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              {conflictWarning && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800 flex gap-2">
                  <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  <span>{conflictWarning}</span>
                </div>
              )}
            </>
          )}

          {/* ============ STEP 4: PERMISSIONS (EDITABLE) ============ */}
          {/* Backlog #13: the per-user override editor is now LIVE here, reusing */}
          {/* the SAME PermissionDeltaEditor as Settings > Users. Default is the   */}
          {/* standard role; the admin opts in to customise (discount cap, module  */}
          {/* access, returns/refund approval, extra abilities). Sent on create.   */}
          {step === 4 && (
            <>
              <h4 className="font-medium text-gray-900">Permissions</h4>
              <div className="bg-green-50 border border-green-200 rounded-lg p-4 flex gap-3">
                <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-semibold text-green-900">
                    Uses standard {primaryRoleOpt ? primaryRoleOpt.label : 'role'} permissions
                  </p>
                  <p className="text-xs text-green-800 mt-1">
                    {form.roles.length > 1
                      ? `This person gets the standard access for ${form.roles.map((r) => ROLE_LABEL[r] || r).join(' + ')}.`
                      : 'This person gets exactly the access that comes with their role — nothing more, nothing less.'}
                    {' '}That is the right choice for almost everyone.
                  </p>
                </div>
              </div>

              {/* Opt-in toggle: keep onboarding fast for the common case, reveal */}
              {/* the full editor only when the admin wants to fine-tune. */}
              <label className="flex items-center justify-between gap-3 border border-gray-200 rounded-lg p-4 cursor-pointer hover:bg-gray-50">
                <span className="flex items-center gap-2">
                  <Shield className="w-4 h-4 text-bv-red-600" />
                  <span className="text-sm font-medium text-gray-700">Customize permissions for this person</span>
                </span>
                <input
                  type="checkbox"
                  checked={customizePerms}
                  onChange={(e) => {
                    setCustomizePerms(e.target.checked);
                    // Clearing the toggle resets to the standard role so a half-
                    // edited override is never silently persisted.
                    if (!e.target.checked) {
                      set({ permissions: {}, moduleAccess: {}, discountCap: undefined });
                    }
                  }}
                  className="rounded border-gray-300 text-bv-red-600 focus:ring-bv-red-500"
                />
              </label>

              {customizePerms && (
                <div className="border border-gray-200 rounded-lg p-4">
                  {form.roles.length === 0 ? (
                    <p className="text-xs text-gray-500">Pick a role first to customise permissions.</p>
                  ) : (
                    <PermissionDeltaEditor
                      roles={form.roles}
                      permissions={form.permissions}
                      onPermissionsChange={(next) => set({ permissions: next })}
                      discountCap={form.discountCap}
                      onDiscountCapChange={(next) => set({ discountCap: next })}
                      moduleAccess={form.moduleAccess}
                      onModuleAccessChange={(next) => set({ moduleAccess: next })}
                    />
                  )}
                </div>
              )}
            </>
          )}

          {/* ============ STEP 5: CREDENTIALS ============ */}
          {step === 5 && (
            <>
              <h4 className="font-medium text-gray-900">Create their login</h4>
              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Username *</label>
                  <input value={form.username || autoUsername}
                    onChange={(e) => set({ username: e.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, '') })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm outline-none focus:ring-2 focus:ring-bv-red-200 focus:border-bv-red-400" />
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Temporary Password *</label>
                  <div className="flex gap-2">
                    <input value={form.tempPassword} onChange={(e) => set({ tempPassword: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono outline-none focus:ring-2 focus:ring-bv-red-200 focus:border-bv-red-400" />
                    <button type="button" onClick={() => set({ tempPassword: randomTempPassword() })}
                      className="px-3 py-2 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50" title="Generate a new password">
                      <RefreshCw className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>

              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs text-blue-800 flex gap-2">
                <Lock className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <span>For their security, this is a temporary password — the new staff member is required to set their own the first time they sign in.</span>
              </div>

              {/* Copyable handoff card */}
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <div className="bg-gray-50 px-4 py-2 flex items-center justify-between">
                  <span className="text-xs font-semibold text-gray-600">Login handoff</span>
                  <button type="button" onClick={copyHandoff}
                    className="text-xs flex items-center gap-1 text-bv-red-600 hover:text-bv-red-700 font-medium">
                    {copied ? <><CheckCircle className="w-3.5 h-3.5" /> Copied</> : <><Copy className="w-3.5 h-3.5" /> Copy</>}
                  </button>
                </div>
                <div className="p-4 space-y-1.5 text-sm">
                  <Row label="Name" value={form.name || '—'} />
                  <Row label="Role(s)" value={form.roles.map((r) => ROLE_LABEL[r] || r).join(', ') || '—'} />
                  <Row label="Store(s)" value={
                    form.assignedStores.length
                      ? form.assignedStores.map((id) => stores.find((s) => s.id === id)?.name || id).join(', ')
                      : 'All stores'
                  } />
                  <Row label="Username" value={effectiveUsername} mono />
                  <Row label="Temp password" value={form.tempPassword} mono />
                </div>
              </div>
            </>
          )}

          {/* ============ STEP 6: DOCUMENTS (SUPERADMIN/ADMIN only) ============ */}
          {step === 6 && canManageDocs && (
            <>
              <h4 className="font-medium text-gray-900">ID numbers &amp; documents</h4>
              <p className="text-xs text-gray-500">
                Everything here is optional — you can finish now and add documents
                later under HR. Files are stored securely and are only visible to
                authorised HR roles.
              </p>

              {/* ID numbers */}
              <div className="border border-gray-200 rounded-lg p-4 space-y-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-gray-700">
                  <CreditCard className="w-4 h-4 text-gray-400" /> Government &amp; statutory numbers
                </div>
                <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Aadhaar No.</label>
                    <input value={form.aadhaarNo}
                      onChange={(e) => set({ aadhaarNo: e.target.value.replace(/[^\d\s-]/g, '').slice(0, 14) })}
                      inputMode="numeric" placeholder="12-digit Aadhaar"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm outline-none focus:ring-2 focus:ring-bv-red-200 focus:border-bv-red-400" />
                    {aadhaarWarning(form.aadhaarNo) && (
                      <p className="text-[11px] text-amber-600 mt-1">{aadhaarWarning(form.aadhaarNo)}</p>
                    )}
                    {!aadhaarWarning(form.aadhaarNo) && form.aadhaarNo && (
                      <p className="text-[11px] text-gray-400 mt-1">Saved as {maskAadhaar(form.aadhaarNo)}</p>
                    )}
                  </div>
                  <div>
                    <label className="text-xs text-gray-500 block mb-1">PAN No.</label>
                    <input value={form.panNo}
                      onChange={(e) => set({ panNo: e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 10) })}
                      placeholder="AAAAA9999A"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono outline-none focus:ring-2 focus:ring-bv-red-200 focus:border-bv-red-400" />
                    {panWarning(form.panNo) && (
                      <p className="text-[11px] text-amber-600 mt-1">{panWarning(form.panNo)}</p>
                    )}
                  </div>
                  <div>
                    <label className="text-xs text-gray-500 block mb-1">PF / UAN No.</label>
                    <input value={form.uanNo}
                      onChange={(e) => set({ uanNo: e.target.value.replace(/[^\d]/g, '').slice(0, 12) })}
                      inputMode="numeric" placeholder="12-digit UAN"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm outline-none focus:ring-2 focus:ring-bv-red-200 focus:border-bv-red-400" />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500 block mb-1">ESIC No.</label>
                    <input value={form.esicNo}
                      onChange={(e) => set({ esicNo: e.target.value.replace(/[^\d]/g, '').slice(0, 17) })}
                      inputMode="numeric" placeholder="ESIC insurance number"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm outline-none focus:ring-2 focus:ring-bv-red-200 focus:border-bv-red-400" />
                  </div>
                </div>
              </div>

              {/* Single-file document slots */}
              <div className="border border-gray-200 rounded-lg p-4 space-y-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-gray-700">
                  <FileText className="w-4 h-4 text-gray-400" /> Documents
                </div>
                <div className="grid grid-cols-1 tablet:grid-cols-2 gap-2">
                  {DOC_SLOTS.map((s) => {
                    const staged = stagedDocs.find((d) => d.slot === s.slot);
                    return (
                      <div key={s.slot}
                        className="border border-gray-200 rounded-lg p-2.5 flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-800">{s.label}</p>
                          {staged ? (
                            <DocChip doc={staged} onRemove={() => removeStaged(staged.key)} />
                          ) : (
                            <p className="text-[11px] text-gray-400">{s.hint}</p>
                          )}
                        </div>
                        <label className="flex-shrink-0 cursor-pointer text-xs flex items-center gap-1 px-2.5 py-1.5 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50">
                          <Upload className="w-3.5 h-3.5" /> {staged ? 'Replace' : 'Choose'}
                          <input type="file" accept=".pdf,image/*" className="hidden"
                            onChange={(e) => { stageSlotFile(s.slot, e.target.files?.[0]); e.target.value = ''; }} />
                        </label>
                      </div>
                    );
                  })}
                </div>

                {/* Other documents (multi-file) */}
                <div className="border border-dashed border-gray-300 rounded-lg p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <p className="text-sm font-medium text-gray-800">Other documents</p>
                      <p className="text-[11px] text-gray-400">Add as many as you need — PDF or images.</p>
                    </div>
                    <label className="flex-shrink-0 cursor-pointer text-xs flex items-center gap-1 px-2.5 py-1.5 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50">
                      <Plus className="w-3.5 h-3.5" /> Add files
                      <input type="file" accept=".pdf,image/*" multiple className="hidden"
                        onChange={(e) => { stageOtherFiles(e.target.files); e.target.value = ''; }} />
                    </label>
                  </div>
                  {stagedDocs.filter((d) => d.slot === 'OTHER').length > 0 && (
                    <div className="mt-2 space-y-1.5">
                      {stagedDocs.filter((d) => d.slot === 'OTHER').map((d) => (
                        <div key={d.key} className="flex items-center justify-between gap-2">
                          <DocChip doc={d} onRemove={() => removeStaged(d.key)} />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs text-blue-800 flex gap-2">
                <Lock className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <span>
                  Aadhaar, PAN and the other documents are sensitive ID. They are
                  stored securely behind access control and are never shared with
                  store-floor staff.
                </span>
              </div>

              {submitError && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700 flex gap-2">
                  <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  <span>{submitError}</span>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="p-5 border-t border-gray-200 flex justify-between sticky bottom-0 bg-white">
          <button onClick={goBack} disabled={submitting}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 flex items-center gap-1 disabled:opacity-50">
            {step === 1 ? 'Cancel' : <><ChevronLeft className="w-4 h-4" /> Back</>}
          </button>
          {step < TOTAL_STEPS ? (
            <button onClick={goNext}
              className="px-6 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700 flex items-center gap-1">
              Continue <ChevronRight className="w-4 h-4" />
            </button>
          ) : (
            <div className="flex items-center gap-2">
              {/* Once the account exists, offer a clean exit even if some uploads
                  failed — the account is already created. */}
              {createdUserId && (
                <button onClick={() => onCreated(form.name.trim())} disabled={submitting}
                  className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
                  Finish anyway
                </button>
              )}
              <button onClick={submit} disabled={submitting}
                className="px-6 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700 flex items-center gap-1.5 disabled:opacity-60">
                {submitting
                  ? <><Loader2 className="w-4 h-4 animate-spin" /> Working…</>
                  : createdUserId
                    ? <><RefreshCw className="w-4 h-4" /> Retry uploads</>
                    : <><Plus className="w-4 h-4" /> Create Account</>}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-gray-500">{label}</span>
      <span className={clsx('font-medium text-gray-900 text-right break-all', mono && 'font-mono')}>{value}</span>
    </div>
  );
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// A single staged-document chip: filename + size + per-file upload status + a
// remove button. Used in both the labelled slots and the OTHER list.
function DocChip({ doc, onRemove }: { doc: StagedDoc; onRemove: () => void }) {
  return (
    <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
      {doc.status === 'uploading'
        ? <Loader2 className="w-3.5 h-3.5 text-bv-red-500 animate-spin flex-shrink-0" />
        : doc.status === 'done'
          ? <CheckCircle className="w-3.5 h-3.5 text-green-600 flex-shrink-0" />
          : doc.status === 'error'
            ? <AlertTriangle className="w-3.5 h-3.5 text-red-500 flex-shrink-0" />
            : <Eye className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />}
      <span className="text-[11px] text-gray-600 truncate" title={doc.file.name}>
        {doc.file.name}
      </span>
      <span className="text-[11px] text-gray-400 flex-shrink-0">· {humanSize(doc.file.size)}</span>
      {doc.status === 'error' && doc.error && (
        <span className="text-[11px] text-red-500 truncate" title={doc.error}>· {doc.error}</span>
      )}
      {doc.status !== 'uploading' && doc.status !== 'done' && (
        <button type="button" onClick={onRemove}
          className="ml-1 text-gray-400 hover:text-red-500 flex-shrink-0" aria-label="Remove file">
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}
