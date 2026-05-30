// ============================================================================
// IMS 2.0 - Family Rx Page
// ============================================================================
// One household, all prescriptions. Search a customer account, then see every
// family member's (patient's) prescriptions grouped together — each row colour-
// coded by validity (valid / expired / unknown). Patients with no Rx still show;
// legacy/imported Rx whose patient isn't on the account land in "Unlinked
// patient". Presentation only — reads GET /prescriptions/family/{customer_id}.

import { useState } from 'react';
import type { ReactElement } from 'react';
import {
  Users,
  User,
  Search,
  Loader2,
  AlertCircle,
  Calendar,
  CheckCircle2,
  XCircle,
  HelpCircle,
  FileText,
} from 'lucide-react';
import { customerApi, prescriptionApi } from '../../services/api';
import type {
  FamilyRxResponse,
  FamilyRxMember,
  FamilyRxPrescription,
} from '../../services/api/sales';
import { useToast } from '../../context/ToastContext';
import { AutoSearch } from '../../components/common/AutoSearch';

// --- Field readers -----------------------------------------------------------
// Backend ships raw Mongo docs, so eye keys vary (sph/sphere, cyl/cylinder,
// add/addition). Read whichever is present without assuming a single shape.
function readEye(eye: Record<string, unknown> | null | undefined, keys: string[]): unknown {
  if (!eye || typeof eye !== 'object') return undefined;
  for (const k of keys) {
    const v = (eye as Record<string, unknown>)[k];
    if (v !== undefined && v !== null && v !== '') return v;
  }
  return undefined;
}

// Render a signed power (SPH/CYL/ADD). Accepts string or number; "-" when blank.
function formatPower(value: unknown): string {
  if (value === undefined || value === null || value === '') return '-';
  const num = typeof value === 'number' ? value : parseFloat(String(value));
  if (Number.isNaN(num)) return String(value);
  return num >= 0 ? `+${num.toFixed(2)}` : num.toFixed(2);
}

function formatAxis(value: unknown): string {
  if (value === undefined || value === null || value === '') return '-';
  return String(value);
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function rxTestDate(rx: FamilyRxPrescription): string | null | undefined {
  return rx.test_date ?? rx.created_at;
}

// Validity → tailwind colour + label. is_valid: true → green, false → red,
// null (no test date / can't compute) → gray.
function validityStyle(isValid: boolean | null): {
  rowBg: string;
  badge: string;
  icon: ReactElement;
  label: string;
} {
  if (isValid === true) {
    return {
      rowBg: 'bg-green-50',
      badge: 'badge-success',
      icon: <CheckCircle2 className="w-3.5 h-3.5" />,
      label: 'Valid',
    };
  }
  if (isValid === false) {
    return {
      rowBg: 'bg-red-50',
      badge: 'badge-error',
      icon: <XCircle className="w-3.5 h-3.5" />,
      label: 'Expired',
    };
  }
  return {
    rowBg: 'bg-gray-50',
    badge: 'badge',
    icon: <HelpCircle className="w-3.5 h-3.5" />,
    label: 'Unknown',
  };
}

// --- Prescription row --------------------------------------------------------
function PrescriptionRow({ rx }: { rx: FamilyRxPrescription }) {
  const v = validityStyle(rx.is_valid);
  const right = rx.right_eye;
  const left = rx.left_eye;
  const optometrist = rx.optometrist_name || rx.doctor_name;

  return (
    <tr className={v.rowBg}>
      <td className="border border-gray-200 px-3 py-2 align-top">
        <div className="flex items-center gap-2 text-gray-700">
          <Calendar className="w-3.5 h-3.5 text-gray-400" />
          <span className="text-sm">{formatDate(rxTestDate(rx))}</span>
        </div>
        {optometrist && (
          <p className="text-xs text-gray-500 mt-0.5 pl-5">by {optometrist}</p>
        )}
      </td>
      <td className="border border-gray-200 px-3 py-2 text-center text-sm whitespace-nowrap">
        {formatPower(readEye(right, ['sphere', 'sph']))} / {formatPower(readEye(right, ['cylinder', 'cyl']))}
        {' '}&times; {formatAxis(readEye(right, ['axis']))}
        {readEye(right, ['add', 'addition', 'add_power']) !== undefined && (
          <span className="text-gray-500"> · Add {formatPower(readEye(right, ['add', 'addition', 'add_power']))}</span>
        )}
      </td>
      <td className="border border-gray-200 px-3 py-2 text-center text-sm whitespace-nowrap">
        {formatPower(readEye(left, ['sphere', 'sph']))} / {formatPower(readEye(left, ['cylinder', 'cyl']))}
        {' '}&times; {formatAxis(readEye(left, ['axis']))}
        {readEye(left, ['add', 'addition', 'add_power']) !== undefined && (
          <span className="text-gray-500"> · Add {formatPower(readEye(left, ['add', 'addition', 'add_power']))}</span>
        )}
      </td>
      <td className="border border-gray-200 px-3 py-2 text-center text-sm whitespace-nowrap">
        {formatDate(rx.expiry_date)}
      </td>
      <td className="border border-gray-200 px-3 py-2 text-center">
        <span className={`${v.badge} whitespace-nowrap`}>
          {v.icon}
          {v.label}
        </span>
      </td>
    </tr>
  );
}

// --- Member card -------------------------------------------------------------
function MemberCard({ member }: { member: FamilyRxMember }) {
  const hasRx = member.prescriptions.length > 0;
  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 rounded-full bg-purple-100 flex items-center justify-center flex-shrink-0">
            <User className="w-5 h-5 text-purple-600" />
          </div>
          <div className="min-w-0">
            <p className="font-medium text-gray-900 truncate">{member.name || 'Unnamed patient'}</p>
            <div className="flex items-center gap-2 mt-0.5 flex-wrap">
              {member.relation ? (
                <span className="badge-info capitalize">{member.relation}</span>
              ) : (
                <span className="badge">Unlinked</span>
              )}
              {member.dob && (
                <span className="text-xs text-gray-500">DOB {formatDate(member.dob)}</span>
              )}
            </div>
          </div>
        </div>
        <div className="text-right flex-shrink-0">
          <span className={member.valid_count > 0 ? 'badge-success' : 'badge'}>
            {member.valid_count}/{member.prescription_count} valid
          </span>
        </div>
      </div>

      {hasRx ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="bg-gray-50">
                <th className="border border-gray-200 px-3 py-2 text-left text-xs font-medium text-gray-600">Test Date</th>
                <th className="border border-gray-200 px-3 py-2 text-center text-xs font-medium text-gray-600">Right (OD) SPH/CYL × AXIS</th>
                <th className="border border-gray-200 px-3 py-2 text-center text-xs font-medium text-gray-600">Left (OS) SPH/CYL × AXIS</th>
                <th className="border border-gray-200 px-3 py-2 text-center text-xs font-medium text-gray-600">Expiry</th>
                <th className="border border-gray-200 px-3 py-2 text-center text-xs font-medium text-gray-600">Status</th>
              </tr>
            </thead>
            <tbody>
              {member.prescriptions.map((rx, i) => (
                <PrescriptionRow key={rx.prescription_id || `${member.patient_id}-${i}`} rx={rx} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-center py-6 text-gray-500 bg-gray-50 border border-gray-200 rounded-lg">
          <FileText className="w-8 h-8 mx-auto mb-1 opacity-40" />
          <p className="text-sm">No prescriptions</p>
        </div>
      )}
    </div>
  );
}

// --- Page --------------------------------------------------------------------
export function FamilyRxPage() {
  const toast = useToast();

  const [family, setFamily] = useState<FamilyRxResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedCustomerName, setSelectedCustomerName] = useState<string>('');

  const loadFamily = async (customerId: string, fallbackName: string) => {
    if (!customerId) return;
    setIsLoading(true);
    setError(null);
    setSelectedCustomerName(fallbackName);
    try {
      const data = await prescriptionApi.getFamilyRx(customerId);
      setFamily(data);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load family prescriptions';
      setError(msg);
      toast.error(msg);
      setFamily(null);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Family Rx</h1>
          <p className="text-gray-500">View a household&rsquo;s prescriptions, grouped by family member</p>
        </div>
      </div>

      {/* Customer search — reuses the same AutoSearch + customerApi.getCustomers
          flow as POS StepCustomer, scoped to the active store. */}
      <div className="card">
        <label className="block text-sm font-medium text-gray-700 mb-2">Find a customer</label>
        <AutoSearch<Record<string, any>>
          fetchResults={async (q, sid) => {
            try {
              const res = await customerApi.getCustomers({ search: q, storeId: sid, limit: 8 });
              return res?.customers || res || [];
            } catch {
              return [];
            }
          }}
          renderItem={(cust) => {
            const custName = cust.name || cust.customer_name || cust.full_name || 'Unknown';
            const custPhone = cust.phone || cust.mobile || '';
            return (
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-purple-600 flex items-center justify-center text-sm font-bold text-white">
                  {custName.charAt(0).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">{custName}</p>
                  <p className="text-xs text-gray-500">
                    {custPhone}
                    {cust.city && ` · ${cust.city}`}
                  </p>
                </div>
              </div>
            );
          }}
          onSelect={(cust) => {
            const id = cust.customer_id || cust._id || cust.id;
            const name = cust.name || cust.customer_name || cust.full_name || 'Customer';
            if (id) loadFamily(id, name);
          }}
          getKey={(cust) => cust.customer_id || cust._id || cust.id || cust.phone || cust.name || 'unknown'}
          placeholder="Search by phone number or name..."
          emptyMessage="No customers found"
          icon={<Search className="w-4 h-4" />}
        />
      </div>

      {/* Error state */}
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3 text-red-600">
            <AlertCircle className="w-5 h-5" />
            <p>{error}</p>
          </div>
        </div>
      )}

      {/* Loading state */}
      {isLoading ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : family ? (
        <>
          {/* Household summary */}
          <div className="card">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-purple-100 flex items-center justify-center">
                  <Users className="w-5 h-5 text-purple-600" />
                </div>
                <div>
                  <p className="font-semibold text-gray-900">
                    {family.customer_name || selectedCustomerName || 'Customer'}
                  </p>
                  <p className="text-xs text-gray-500">Household account</p>
                </div>
              </div>
              <div className="flex items-center gap-4 text-sm">
                <div className="text-center">
                  <p className="text-lg font-bold text-gray-900">{family.member_count}</p>
                  <p className="text-xs text-gray-500">Members</p>
                </div>
                <div className="text-center">
                  <p className="text-lg font-bold text-gray-900">{family.total_prescriptions}</p>
                  <p className="text-xs text-gray-500">Prescriptions</p>
                </div>
              </div>
            </div>
          </div>

          {/* Members */}
          {family.members.length === 0 ? (
            <div className="card text-center py-12 text-gray-500">
              <Users className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No family members on this account</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
              {family.members.map((member, i) => (
                <MemberCard key={member.patient_id || `member-${i}`} member={member} />
              ))}
            </div>
          )}
        </>
      ) : (
        // Empty state — before a customer is chosen
        <div className="card text-center py-16 text-gray-500">
          <Users className="w-14 h-14 mx-auto mb-3 opacity-40" />
          <p className="font-medium text-gray-700">Search for a customer to view their family&rsquo;s prescriptions</p>
          <p className="text-sm mt-1">Every household member&rsquo;s Rx, colour-coded by validity, in one place.</p>
        </div>
      )}
    </div>
  );
}

export default FamilyRxPage;
