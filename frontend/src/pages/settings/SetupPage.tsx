// ============================================================================
// IMS 2.0 — Store Setup & Employee Onboarding
// ============================================================================
import { useState, useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { adminStoreApi } from '../../services/api';
import {
  Building, Users, Plus, Edit, MapPin, Phone, Clock,
  Shield, Save, X, ChevronRight, CheckCircle, AlertTriangle,
  Tag, Wand2,
} from 'lucide-react';
import { StoreSetupWizard } from '../../components/settings/StoreSetupWizard';
import clsx from 'clsx';

// ---------------------------------------------------------------------------
// STORE SETUP TAB
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

const ALL_CATEGORIES = [
  'FRAMES', 'SUNGLASSES', 'RX_LENSES', 'CONTACT_LENSES',
  'WRIST_WATCHES', 'SMARTWATCHES', 'ACCESSORIES', 'HEARING_AIDS',
  'WALL_CLOCKS', 'SMART_GLASSES', 'PERFUMES',
];

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
  username: '', tempPassword: 'admin123',
};

export default function SetupPage() {
  const { user } = useAuth();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<'stores' | 'employees' | 'wizard'>('stores');
  const [stores, setStores] = useState<StoreConfig[]>([]);

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
  const [editStore, setEditStore] = useState<StoreConfig | null>(null);
  const [, setEditEmployee] = useState<NewEmployee | null>(null);
  const [showStoreForm, setShowStoreForm] = useState(false);
  const [showEmployeeForm, setShowEmployeeForm] = useState(false);

  const isHQ = user?.roles?.some((r: string) => ['ADMIN', 'SUPERADMIN'].includes(r));
  if (!isHQ) {
    return (
      <div className="max-w-4xl mx-auto p-6 text-center">
        <Shield className="w-12 h-12 text-gray-700 mx-auto mb-3" />
        <p className="text-gray-500">Store setup is restricted to Admin and Superadmin roles.</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-4 tablet:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">System Setup</h1>
        <div className="flex bg-gray-100 rounded-lg p-1">
          <button onClick={() => setActiveTab('stores')} className={clsx('px-4 py-2 rounded-md text-sm font-medium transition-colors', activeTab === 'stores' ? 'bg-white shadow text-gray-900' : 'text-gray-500')}>
            <Building className="w-4 h-4 inline mr-1.5" />Stores
          </button>
          <button onClick={() => setActiveTab('employees')} className={clsx('px-4 py-2 rounded-md text-sm font-medium transition-colors', activeTab === 'employees' ? 'bg-white shadow text-gray-900' : 'text-gray-500')}>
            <Users className="w-4 h-4 inline mr-1.5" />Employees
          </button>
          <button onClick={() => setActiveTab('wizard')} className={clsx('px-4 py-2 rounded-md text-sm font-medium transition-colors', activeTab === 'wizard' ? 'bg-white shadow text-gray-900' : 'text-gray-500')}>
            <Wand2 className="w-4 h-4 inline mr-1.5" />Setup Wizard
          </button>
        </div>
      </div>

      {/* ================================================================ */}
      {/* STORES TAB                                                       */}
      {/* ================================================================ */}
      {activeTab === 'stores' && (
        <div className="space-y-4">
          <div className="flex justify-end">
            <button onClick={() => { setEditStore(null); setShowStoreForm(true); }}
              className="flex items-center gap-1.5 px-4 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700">
              <Plus className="w-4 h-4" /> Add Store
            </button>
          </div>

          {stores.map(store => (
            <div key={store.id} className="bg-white border border-gray-200 rounded-xl p-5">
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-gray-900">{store.name}</h3>
                    <span className={clsx('text-xs px-2 py-0.5 rounded-full',
                      store.brand === 'BETTER_VISION' ? 'bg-red-50 text-red-600' : 'bg-blue-50 text-blue-600')}>
                      {store.brand === 'BETTER_VISION' ? 'Better Vision' : 'WizOpt'}
                    </span>
                    <span className={clsx('text-xs px-2 py-0.5 rounded-full', store.isActive ? 'bg-green-50 text-green-600' : 'bg-gray-100 text-gray-500')}>
                      {store.isActive ? 'Active' : 'Inactive'}
                    </span>
                  </div>
                  <p className="text-sm text-gray-500 mt-1 flex items-center gap-1"><MapPin className="w-3 h-3" />{store.address}, {store.city}, {store.state} - {store.pincode}</p>
                  <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                    <span className="flex items-center gap-1"><Phone className="w-3 h-3" />{store.phone}</span>
                    <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{store.openingTime} – {store.closingTime}</span>
                    <span className="flex items-center gap-1"><Tag className="w-3 h-3" />GST: {store.gstNumber}</span>
                  </div>
                  <div className="flex flex-wrap gap-1 mt-2">
                    {store.categories.map(c => (
                      <span key={c} className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded">{c.replace(/_/g, ' ')}</span>
                    ))}
                  </div>
                </div>
                <button onClick={() => { setEditStore(store); setShowStoreForm(true); }}
                  className="p-2 text-gray-500 hover:text-gray-600 hover:bg-gray-100 rounded-lg">
                  <Edit className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}

          {/* Store Form Modal */}
          {showStoreForm && <StoreFormModal
            store={editStore}
            onSave={async (s) => {
              try {
                const apiData = { name: s.name, code: s.code, address: s.address, city: s.city, state: s.state, phone: s.phone, email: s.email, gst: s.gstNumber };
                if (editStore) {
                  await adminStoreApi.updateStore(editStore.id!, apiData);
                  setStores(prev => prev.map(st => st.id === editStore.id ? { ...st, ...s } : st));
                } else {
                  const result = await adminStoreApi.createStore(apiData);
                  setStores(prev => [...prev, { ...s, id: result?.store_id || `STORE-${Date.now()}`, isActive: true }]);
                }
                setShowStoreForm(false);
                toast.success(editStore ? 'Store updated' : 'Store created');
              } catch (err) {
                toast.error('Failed to save store');
              }
            }}
            onClose={() => setShowStoreForm(false)}
          />}
        </div>
      )}

      {/* ================================================================ */}
      {/* EMPLOYEES TAB                                                    */}
      {/* ================================================================ */}
      {activeTab === 'employees' && (
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
            onSave={() => setShowEmployeeForm(false)}
            onClose={() => setShowEmployeeForm(false)}
          />}
        </div>
      )}

      {/* ================================================================ */}
      {/* SETUP WIZARD TAB                                                 */}
      {/* ================================================================ */}
      {activeTab === 'wizard' && (
        <div className="space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl p-6">
            <div className="flex items-center gap-3 mb-4">
              <Wand2 className="w-6 h-6 text-bv-gold-500" />
              <div>
                <h2 className="text-lg font-semibold text-gray-900">New Store Setup Wizard</h2>
                <p className="text-sm text-gray-500">Step-by-step guided setup for opening a new store location</p>
              </div>
            </div>
            <StoreSetupWizard />
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// STORE FORM MODAL
// ---------------------------------------------------------------------------
function StoreFormModal({ store, onSave, onClose }: { store: StoreConfig | null; onSave: (s: StoreConfig) => void; onClose: () => void }) {
  const [form, setForm] = useState<StoreConfig>(store || {
    name: '', code: '', brand: 'BETTER_VISION', address: '', city: '', state: '', pincode: '',
    phone: '', email: '', gstNumber: '', openingTime: '10:00', closingTime: '21:00',
    categories: ['FRAMES', 'SUNGLASSES', 'RX_LENSES'], isActive: true,
  });

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="p-5 border-b border-gray-200 flex items-center justify-between">
          <h3 className="font-semibold text-gray-900">{store ? 'Edit Store' : 'Add New Store'}</h3>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-5 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-gray-500 block mb-1">Store Name *</label>
              <input value={form.name} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
            <div><label className="text-xs text-gray-500 block mb-1">Store Code *</label>
              <input value={form.code} onChange={e => setForm(p => ({ ...p, code: e.target.value.toUpperCase() }))} placeholder="BV-BOK-03" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-gray-500 block mb-1">Brand *</label>
              <select value={form.brand} onChange={e => setForm(p => ({ ...p, brand: e.target.value as any }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
                <option value="BETTER_VISION">Better Vision</option>
                <option value="WIZOPT">WizOpt</option>
              </select></div>
            <div><label className="text-xs text-gray-500 block mb-1">GST Number *</label>
              <input value={form.gstNumber} onChange={e => setForm(p => ({ ...p, gstNumber: e.target.value.toUpperCase() }))} maxLength={15} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
          </div>
          <div><label className="text-xs text-gray-500 block mb-1">Address *</label>
            <input value={form.address} onChange={e => setForm(p => ({ ...p, address: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
          <div className="grid grid-cols-3 gap-4">
            <div><label className="text-xs text-gray-500 block mb-1">City *</label>
              <input value={form.city} onChange={e => setForm(p => ({ ...p, city: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
            <div><label className="text-xs text-gray-500 block mb-1">State *</label>
              <input value={form.state} onChange={e => setForm(p => ({ ...p, state: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
            <div><label className="text-xs text-gray-500 block mb-1">Pincode *</label>
              <input value={form.pincode} onChange={e => setForm(p => ({ ...p, pincode: e.target.value.replace(/\D/g, '').slice(0, 6) }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-gray-500 block mb-1">Phone *</label>
              <input value={form.phone} onChange={e => setForm(p => ({ ...p, phone: e.target.value.replace(/\D/g, '').slice(0, 10) }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
            <div><label className="text-xs text-gray-500 block mb-1">Email</label>
              <input type="email" value={form.email} onChange={e => setForm(p => ({ ...p, email: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-gray-500 block mb-1">Opening Time</label>
              <input type="time" value={form.openingTime} onChange={e => setForm(p => ({ ...p, openingTime: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
            <div><label className="text-xs text-gray-500 block mb-1">Closing Time</label>
              <input type="time" value={form.closingTime} onChange={e => setForm(p => ({ ...p, closingTime: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
          </div>

          <div>
            <label className="text-xs text-gray-500 block mb-2">Product Categories Enabled *</label>
            <div className="flex flex-wrap gap-2">
              {ALL_CATEGORIES.map(cat => (
                <button key={cat} onClick={() => setForm(p => ({
                  ...p, categories: p.categories.includes(cat) ? p.categories.filter(c => c !== cat) : [...p.categories, cat]
                }))}
                  className={clsx('text-xs px-3 py-1.5 rounded-lg border transition-colors',
                    form.categories.includes(cat) ? 'bg-bv-gold-50 border-bv-red-300 text-bv-gold-700' : 'border-gray-200 text-gray-500 hover:border-gray-300')}>
                  {cat.replace(/_/g, ' ')}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="p-5 border-t border-gray-200 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm">Cancel</button>
          <button onClick={() => onSave(form)} className="px-6 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700">
            <Save className="w-4 h-4 inline mr-1" />{store ? 'Update Store' : 'Create Store'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EMPLOYEE FORM MODAL
// ---------------------------------------------------------------------------
function EmployeeFormModal({ stores, onSave, onClose }: { stores: StoreConfig[]; onSave: () => void; onClose: () => void }) {
  const [form, setForm] = useState<NewEmployee>(DEFAULT_EMPLOYEE);
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
              <div className="grid grid-cols-2 gap-4">
                <div><label className="text-xs text-gray-500 block mb-1">Full Name *</label>
                  <input value={form.name} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
                <div><label className="text-xs text-gray-500 block mb-1">Employee Code</label>
                  <input value={form.employeeCode} onChange={e => setForm(p => ({ ...p, employeeCode: e.target.value.toUpperCase() }))} placeholder="BV-EMP-001" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
              </div>
              <div className="grid grid-cols-2 gap-4">
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
              <div className="grid grid-cols-2 gap-2">
                {ROLES.map(role => (
                  <button key={role.id} onClick={() => toggleRole(role.id)}
                    className={clsx('p-3 rounded-lg border-2 text-left transition-all',
                      form.roles.includes(role.id) ? 'border-bv-red-600 bg-bv-gold-50' : 'border-gray-200 hover:border-gray-300')}>
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
                        form.assignedStores.includes(s.id || '') ? 'border-bv-red-600 bg-bv-gold-50' : 'border-gray-200')}>
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
              <div className="grid grid-cols-3 gap-4">
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
              <div className="grid grid-cols-2 gap-4">
                <div><label className="text-xs text-gray-500 block mb-1">Username *</label>
                  <input value={form.username || autoUsername} onChange={e => setForm(p => ({ ...p, username: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" /></div>
                <div><label className="text-xs text-gray-500 block mb-1">Temporary Password</label>
                  <input value={form.tempPassword} onChange={e => setForm(p => ({ ...p, tempPassword: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
                  <p className="text-xs text-gray-500 mt-1">Employee must change on first login</p></div>
              </div>

              <div className="bg-gray-50 rounded-lg p-4 mt-4">
                <h5 className="text-sm font-medium text-gray-900 mb-2">Summary</h5>
                <div className="grid grid-cols-2 gap-y-1 text-xs">
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
          <button onClick={() => step < 4 ? setStep(step + 1) : onSave()}
            className="px-6 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700">
            {step === 4 ? 'Create Employee' : 'Continue'} <ChevronRight className="w-4 h-4 inline ml-1" />
          </button>
        </div>
      </div>
    </div>
  );
}
