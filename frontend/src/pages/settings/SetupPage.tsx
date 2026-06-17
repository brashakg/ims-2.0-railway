// ============================================================================
// IMS 2.0 — Staff Onboarding
// ============================================================================
// COUNCIL RULING §3: the legacy "Stores" step is removed from this page. Stores
// and legal entities are managed ONLY on the canonical /organization screen
// (it captures the required entity link + derives each store's GSTIN by state).
// This page is now staff onboarding only — one canonical path each: stores in
// Organization, staff here (over the mature, escalation-guarded create_user path).
import { useState, useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { adminStoreApi, adminUserApi } from '../../services/api';
import {
  Building, Users, Plus, Shield, X, ChevronRight, CheckCircle, AlertTriangle,
} from 'lucide-react';
import clsx from 'clsx';

// ---------------------------------------------------------------------------
// Store shape (read-only) — used for the onboarding store-assignment picker.
// ---------------------------------------------------------------------------
interface StoreConfig {
  id?: string;
  name: string;
  code: string;
  brand: 'BETTER_VISION' | 'WIZOPT';
  address: string;
  city: string;
  state: string;
  pincode: string;
  phone: string;
  email: string;
  gstNumber: string;
  openingTime: string;
  closingTime: string;
  categories: string[];
  isActive: boolean;
}

const ROLES = [
  { id: 'SALES_STAFF', label: 'Sales Staff', desc: 'Billing, customer facing' },
  { id: 'CASHIER', label: 'Sales + Cashier', desc: 'Sales + cash drawer access' },
  { id: 'OPTOMETRIST', label: 'Optometrist', desc: 'Eye testing, prescriptions' },
  { id: 'WORKSHOP_STAFF', label: 'Workshop / Fitting', desc: 'Frame fitting, lens mounting' },
  { id: 'STORE_MANAGER', label: 'Store Head', desc: 'Full store operations' },
  { id: 'ACCOUNTANT', label: 'Accountant', desc: 'Finance, GST, reconciliation' },
  { id: 'CATALOG_MANAGER', label: 'Catalog Manager', desc: 'HQ product catalog' },
  { id: 'AREA_MANAGER', label: 'Area Manager', desc: 'Multi-store oversight' },
  { id: 'ADMIN', label: 'Admin (Director)', desc: 'HQ administration' },
  { id: 'SUPERADMIN', label: 'Superadmin (CEO)', desc: 'Full system control' },
];

// ---------------------------------------------------------------------------
// EMPLOYEE ONBOARDING
// ---------------------------------------------------------------------------
interface NewEmployee {
  name: string;
  email: string;
  phone: string;
  roles: string[];
  assignedStores: string[];
  primaryStore: string;
  discountCap: number;
  shiftStart: string;
  shiftEnd: string;
  weekOff: string;
  employeeCode: string;
  joiningDate: string;
  username: string;
  tempPassword: string;
}

const DEFAULT_EMPLOYEE: NewEmployee = {
  name: '', email: '', phone: '', roles: [], assignedStores: [], primaryStore: '',
  discountCap: 0, shiftStart: '10:00', shiftEnd: '20:00', weekOff: 'SUNDAY',
  employeeCode: '', joiningDate: new Date().toISOString().split('T')[0],
  username: '', tempPassword: '',
};

// BUG-132: never ship a default credential ('admin123') in the frontend bundle.
// Generate a strong random temp password per new-employee form -- a usable
// default that is NOT a known constant; the new user is forced to change it on
// first login (must_change_password).
function randomTempPassword(): string {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
  const arr = new Uint32Array(12);
  (window.crypto || (window as unknown as { msCrypto: Crypto }).msCrypto).getRandomValues(arr);
  return Array.from(arr, (n) => chars[n % chars.length]).join('');
}

export default function SetupPage() {
  const { user } = useAuth();
  const toast = useToast();
  // Stores are loaded read-only so the onboarding wizard can offer store
  // assignment. They are CREATED/EDITED only on /organization.
  const [stores, setStores] = useState<StoreConfig[]>([]);
  const [, setEditEmployee] = useState<NewEmployee | null>(null);
  const [showEmployeeForm, setShowEmployeeForm] = useState(false);

  useEffect(() => {
    adminStoreApi.getStores().then((data: any) => {
      if (Array.isArray(data?.stores || data)) {
        const storeList = data?.stores || data;
        setStores(storeList.map((s: any) => ({
          id: s.store_id || s.store_code || s.id,
          name: s.store_name || s.name || '',
          code: s.store_code || s.store_id || '',
          brand: s.brand || 'BETTER_VISION',
          address: s.address || '',
          city: s.city || '',
          state: s.state || '',
          pincode: s.pincode || '',
          phone: s.phone || '',
          email: s.email || '',
          gstNumber: s.gstin || s.gst_number || '',
          openingTime: s.opening_time || '10:00',
          closingTime: s.closing_time || '21:00',
          categories: s.categories || [],
          isActive: s.is_active !== false,
        })));
      }
    }).catch(() => {});
  }, []);

  const isHQ = user?.roles?.some((r: string) => ['ADMIN', 'SUPERADMIN'].includes(r));
  if (!isHQ) {
    return (
      <div className="max-w-4xl mx-auto p-6 text-center">
        <Shield className="w-12 h-12 text-gray-700 mx-auto mb-3" />
        <p className="text-gray-500">Staff onboarding is restricted to Admin and Superadmin roles.</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-4 tablet:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Staff Onboarding</h1>
          <p className="text-sm text-gray-500 mt-0.5 flex items-center gap-1">
            <Building className="w-3.5 h-3.5" />
            Stores &amp; entities are managed on the Organization screen.
          </p>
        </div>
      </div>

      {/* ================================================================ */}
      {/* EMPLOYEES                                                        */}
      {/* ================================================================ */}
      <div className="space-y-4">
        <div className="flex justify-end">
          <button onClick={() => { setEditEmployee(null); setShowEmployeeForm(true); }}
            className="flex items-center gap-1.5 px-4 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700">
            <Plus className="w-4 h-4" /> Onboard Employee
          </button>
        </div>

        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-700">
          <AlertTriangle className="w-4 h-4 inline mr-1" />
          Employee onboarding is a detailed process. Each field controls what the employee can see and do in the system.
          Take your time — getting this right means fewer support requests later.
        </div>

        {/* Placeholder employee list */}
        <div className="bg-white border border-gray-200 rounded-xl p-5 text-center text-gray-500">
          <Users className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p className="text-sm">Employee list loads from the backend. Use "Onboard Employee" to add new staff.</p>
        </div>

        {showEmployeeForm && <EmployeeFormModal
          stores={stores}
          onSave={async (emp) => {
            // Onboard the employee through the ONE canonical user path
            // (adminUserApi -> POST /users). The wizard collects roles[],
            // assigned stores, discount cap + a temp password that must be
            // changed on first login (mustChangePassword: true).
            try {
              await adminUserApi.createUser({
                name: emp.name,
                email: emp.email,
                phone: emp.phone || undefined,
                roles: emp.roles,
                storeIds: emp.assignedStores,
                primaryStoreId: emp.primaryStore || undefined,
                discountCap: emp.discountCap,
                username: emp.username || undefined,
                password: emp.tempPassword || undefined,
                mustChangePassword: true,
              });
              toast.success(`${emp.name || 'Employee'} onboarded`);
              setShowEmployeeForm(false);
            } catch (e: any) {
              toast.error(e?.response?.data?.detail || 'Failed to onboard employee');
            }
          }}
          onClose={() => setShowEmployeeForm(false)}
        />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EMPLOYEE FORM MODAL
// ---------------------------------------------------------------------------
function EmployeeFormModal({ stores, onSave, onClose }: { stores: StoreConfig[]; onSave: (emp: NewEmployee) => void | Promise<void>; onClose: () => void }) {
  const [form, setForm] = useState<NewEmployee>(() => ({
    ...DEFAULT_EMPLOYEE,
    tempPassword: randomTempPassword(),
  }));
  const [step, setStep] = useState(1);

  const toggleRole = (roleId: string) => {
    setForm(p => ({
      ...p, roles: p.roles.includes(roleId) ? p.roles.filter(r => r !== roleId) : [...p.roles, roleId]
    }));
  };

  const toggleStore = (storeId: string) => {
    setForm(p => ({
      ...p, assignedStores: p.assignedStores.includes(storeId) ? p.assignedStores.filter(s => s !== storeId) : [...p.assignedStores, storeId]
    }));
  };

  // Auto-generate username from name
  const autoUsername = form.name.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '').slice(0, 20);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="p-5 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-gray-900">Onboard New Employee</h3>
            <p className="text-xs text-gray-500">Step {step} of 4</p>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded"><X className="w-5 h-5" /></button>
        </div>

        <div className="p-5 space-y-4">
          {/* Step 1: Basic Info */}
          {step === 1 && (
            <>
              <h4 className="font-medium text-gray-900">Personal Information</h4>
              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
                <div><label className="text-xs text-gray-500 block mb-1">Full Name *</label>
                  <input value={form.name} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
                <div><label className="text-xs text-gray-500 block mb-1">Employee Code</label>
                  <input value={form.employeeCode} onChange={e => setForm(p => ({ ...p, employeeCode: e.target.value.toUpperCase() }))} placeholder="BV-EMP-001" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
              </div>
              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
                <div><label className="text-xs text-gray-500 block mb-1">Phone *</label>
                  <input value={form.phone} onChange={e => setForm(p => ({ ...p, phone: e.target.value.replace(/\D/g, '').slice(0, 10) }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
                <div><label className="text-xs text-gray-500 block mb-1">Email</label>
                  <input type="email" value={form.email} onChange={e => setForm(p => ({ ...p, email: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
              </div>
              <div><label className="text-xs text-gray-500 block mb-1">Joining Date</label>
                <input type="date" value={form.joiningDate} onChange={e => setForm(p => ({ ...p, joiningDate: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
            </>
          )}

          {/* Step 2: Roles */}
          {step === 2 && (
            <>
              <h4 className="font-medium text-gray-900">Assign Roles</h4>
              <p className="text-xs text-gray-500">One person can have multiple roles. Each role grants specific permissions.</p>
              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-2">
                {ROLES.map(role => (
                  <button key={role.id} onClick={() => toggleRole(role.id)}
                    className={clsx('p-3 rounded-lg border-2 text-left transition-all',
                      form.roles.includes(role.id) ? 'border-bv-red-600 bg-bv-red-50' : 'border-gray-200 hover:border-gray-300')}>
                    <div className="flex items-center gap-2">
                      <div className={clsx('w-5 h-5 rounded border-2 flex items-center justify-center',
                        form.roles.includes(role.id) ? 'border-bv-red-600 bg-bv-red-600' : 'border-gray-300')}>
                        {form.roles.includes(role.id) && <CheckCircle className="w-3 h-3 text-gray-900" />}
                      </div>
                      <span className="text-sm font-medium">{role.label}</span>
                    </div>
                    <p className="text-xs text-gray-500 mt-1 ml-7">{role.desc}</p>
                  </button>
                ))}
              </div>
            </>
          )}

          {/* Step 3: Store Assignment + Shift */}
          {step === 3 && (
            <>
              <h4 className="font-medium text-gray-900">Store Assignment & Shift</h4>
              <div>
                <label className="text-xs text-gray-500 block mb-2">Assigned Stores</label>
                <div className="space-y-2">
                  {stores.map(s => (
                    <button key={s.id} onClick={() => toggleStore(s.id || '')}
                      className={clsx('w-full p-3 rounded-lg border-2 text-left flex items-center justify-between',
                        form.assignedStores.includes(s.id || '') ? 'border-bv-red-600 bg-bv-red-50' : 'border-gray-200')}>
                      <div>
                        <p className="text-sm font-medium">{s.name}</p>
                        <p className="text-xs text-gray-500">{s.city}</p>
                      </div>
                      {form.assignedStores.includes(s.id || '') && (
                        <button onClick={e => { e.stopPropagation(); setForm(p => ({ ...p, primaryStore: s.id || '' })); }}
                          className={clsx('text-xs px-2 py-1 rounded',
                            form.primaryStore === s.id ? 'bg-bv-red-600 text-white' : 'bg-gray-100 text-gray-600')}>
                          {form.primaryStore === s.id ? 'Primary' : 'Set Primary'}
                        </button>
                      )}
                    </button>
                  ))}
                </div>
              </div>
              <div className="grid grid-cols-1 tablet:grid-cols-2 lg:grid-cols-3 gap-4">
                <div><label className="text-xs text-gray-500 block mb-1">Shift Start</label>
                  <input type="time" value={form.shiftStart} onChange={e => setForm(p => ({ ...p, shiftStart: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
                <div><label className="text-xs text-gray-500 block mb-1">Shift End</label>
                  <input type="time" value={form.shiftEnd} onChange={e => setForm(p => ({ ...p, shiftEnd: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
                <div><label className="text-xs text-gray-500 block mb-1">Week Off</label>
                  <select value={form.weekOff} onChange={e => setForm(p => ({ ...p, weekOff: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
                    {['SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY'].map(d => <option key={d} value={d}>{d}</option>)}
                  </select></div>
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">Discount Authority (%)</label>
                <input type="number" min="0" max="100" value={form.discountCap} onChange={e => setForm(p => ({ ...p, discountCap: Number(e.target.value) }))}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
                <p className="text-xs text-gray-500 mt-1">Maximum discount this employee can apply without approval. Default by role: Sales 10%, Manager 20%, Area Manager 25%</p>
              </div>
            </>
          )}

          {/* Step 4: Login Credentials */}
          {step === 4 && (
            <>
              <h4 className="font-medium text-gray-900">Login Credentials</h4>
              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
                <div><label className="text-xs text-gray-500 block mb-1">Username *</label>
                  <input value={form.username || autoUsername} onChange={e => setForm(p => ({ ...p, username: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
                <div><label className="text-xs text-gray-500 block mb-1">Temporary Password</label>
                  <input value={form.tempPassword} onChange={e => setForm(p => ({ ...p, tempPassword: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
                  <p className="text-xs text-gray-500 mt-1">Employee must change on first login</p></div>
              </div>

              <div className="bg-gray-50 rounded-lg p-4 mt-4">
                <h5 className="text-sm font-medium text-gray-900 mb-2">Summary</h5>
                <div className="grid grid-cols-1 tablet:grid-cols-2 gap-y-1 text-xs">
                  <span className="text-gray-500">Name:</span><span className="font-medium">{form.name}</span>
                  <span className="text-gray-500">Roles:</span><span className="font-medium">{form.roles.join(', ') || 'None'}</span>
                  <span className="text-gray-500">Stores:</span><span className="font-medium">{form.assignedStores.length} store(s)</span>
                  <span className="text-gray-500">Shift:</span><span className="font-medium">{form.shiftStart} – {form.shiftEnd}</span>
                  <span className="text-gray-500">Discount Cap:</span><span className="font-medium">{form.discountCap}%</span>
                  <span className="text-gray-500">Username:</span><span className="font-medium">{form.username || autoUsername}</span>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="p-5 border-t border-gray-200 flex justify-between">
          <button onClick={() => step > 1 ? setStep(step - 1) : onClose()} className="px-4 py-2 border border-gray-300 rounded-lg text-sm">{step === 1 ? 'Cancel' : 'Back'}</button>
          <button onClick={() => step < 4 ? setStep(step + 1) : onSave(form)}
            className="px-6 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700">
            {step === 4 ? 'Create Employee' : 'Continue'} <ChevronRight className="w-4 h-4 inline ml-1" />
          </button>
        </div>
      </div>
    </div>
  );
}
