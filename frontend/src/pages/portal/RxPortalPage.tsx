// ============================================================================
// IMS 2.0 — Customer Prescription (Rx) Portal (public, OTP-gated)
// ============================================================================
// Reached at `/rx-portal`. Because a prescription is medical data, viewing is
// gated by a one-time code (OTP) sent to the customer's registered phone:
//
//   phone -> request OTP -> enter OTP -> short-lived view token -> view Rx
//
// The view token is held only in component state (never persisted) and expires
// server-side in ~15 min. No IMS login involved.

import { useCallback, useState } from 'react';
import {
  Loader2, ShieldCheck, Phone, KeyRound, Eye, AlertTriangle, ArrowLeft,
  Glasses, CalendarDays,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import {
  portalApi,
  type PortalPrescription,
  type PortalRxResponse,
} from '../../services/api/portal';

type Step = 'phone' | 'otp' | 'view';

function errMessage(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return (e as Error)?.message || fallback;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleDateString('en-IN', {
      day: '2-digit', month: 'short', year: 'numeric',
    });
  } catch {
    return String(value);
  }
}

export default function RxPortalPage() {
  const toast = useToast();
  const [step, setStep] = useState<Step>('phone');
  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [viewToken, setViewToken] = useState<string | null>(null);
  const [rx, setRx] = useState<PortalRxResponse | null>(null);

  const requestOtp = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      const trimmed = phone.trim();
      if (trimmed.replace(/\D/g, '').length < 10) {
        toast.error('Please enter a valid 10-digit mobile number.');
        return;
      }
      setSubmitting(true);
      try {
        const resp = await portalApi.requestRxOtp(trimmed);
        // Generic success regardless of whether the number exists.
        toast.success(resp.message || 'If this number is registered, a code has been sent.');
        // Dev-only convenience: backend echoes the OTP when PORTAL_OTP_DEBUG=true.
        if (resp.debug_otp) setOtp(resp.debug_otp);
        setStep('otp');
      } catch (e2: unknown) {
        toast.error(errMessage(e2, 'Could not send a verification code. Please try again.'));
      } finally {
        setSubmitting(false);
      }
    },
    [phone, toast],
  );

  const verifyOtp = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      const code = otp.trim();
      if (code.replace(/\D/g, '').length < 4) {
        toast.error('Please enter the code from your SMS.');
        return;
      }
      setSubmitting(true);
      try {
        const resp = await portalApi.verifyRxOtp(phone.trim(), code);
        setViewToken(resp.view_token);
        // Immediately fetch the prescriptions with the fresh token.
        const data = await portalApi.getMyPrescriptions(resp.view_token);
        setRx(data);
        setStep('view');
      } catch (e2: unknown) {
        toast.error(errMessage(e2, 'That code did not work. Please try again.'));
      } finally {
        setSubmitting(false);
      }
    },
    [otp, phone, toast],
  );

  const reset = useCallback(() => {
    setStep('phone');
    setOtp('');
    setViewToken(null);
    setRx(null);
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-2xl mx-auto px-4 py-4 flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-bv-red-600" />
          <p className="text-xs font-mono uppercase tracking-wide text-gray-500">
            My Prescriptions · Better Vision
          </p>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-4 py-6">
        {step === 'phone' && (
          <PhoneStep
            phone={phone}
            setPhone={setPhone}
            submitting={submitting}
            onSubmit={requestOtp}
          />
        )}

        {step === 'otp' && (
          <OtpStep
            phone={phone}
            otp={otp}
            setOtp={setOtp}
            submitting={submitting}
            onSubmit={verifyOtp}
            onBack={() => setStep('phone')}
            onResend={() => requestOtp()}
          />
        )}

        {step === 'view' && rx && (
          <RxList rx={rx} onDone={reset} hasToken={Boolean(viewToken)} />
        )}
      </main>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Step 1 — phone
// ----------------------------------------------------------------------------

function PhoneStep({
  phone, setPhone, submitting, onSubmit,
}: {
  phone: string;
  setPhone: (v: string) => void;
  submitting: boolean;
  onSubmit: (e?: React.FormEvent) => void;
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-6">
      <h1 className="text-xl font-semibold text-gray-900">View your prescriptions</h1>
      <p className="text-sm text-gray-500 mt-1">
        Enter the mobile number registered with us. We&apos;ll text you a one-time
        code to keep your medical details secure.
      </p>
      <form onSubmit={onSubmit} className="mt-5 space-y-4">
        <div>
          <label htmlFor="rx-phone" className="block text-sm font-medium text-gray-700 mb-1">
            Mobile number
          </label>
          <div className="relative">
            <Phone className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              id="rx-phone"
              type="tel"
              inputMode="numeric"
              autoComplete="tel"
              className="input-field pl-9"
              placeholder="9876543210"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              disabled={submitting}
            />
          </div>
        </div>
        <button
          type="submit"
          disabled={submitting}
          className="btn-primary w-full flex items-center justify-center gap-2"
        >
          {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <KeyRound className="w-4 h-4" />}
          Send verification code
        </button>
      </form>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Step 2 — OTP
// ----------------------------------------------------------------------------

function OtpStep({
  phone, otp, setOtp, submitting, onSubmit, onBack, onResend,
}: {
  phone: string;
  otp: string;
  setOtp: (v: string) => void;
  submitting: boolean;
  onSubmit: (e?: React.FormEvent) => void;
  onBack: () => void;
  onResend: () => void;
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-6">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 mb-4"
      >
        <ArrowLeft className="w-4 h-4" /> Change number
      </button>
      <h1 className="text-xl font-semibold text-gray-900">Enter your code</h1>
      <p className="text-sm text-gray-500 mt-1">
        We sent a 6-digit code to <span className="font-medium text-gray-700">{phone}</span> if
        it&apos;s registered with us. It expires in a few minutes.
      </p>
      <form onSubmit={onSubmit} className="mt-5 space-y-4">
        <div>
          <label htmlFor="rx-otp" className="block text-sm font-medium text-gray-700 mb-1">
            Verification code
          </label>
          <input
            id="rx-otp"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            className="input-field tracking-[0.5em] text-center text-lg font-semibold"
            placeholder="••••••"
            value={otp}
            onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))}
            disabled={submitting}
          />
        </div>
        <button
          type="submit"
          disabled={submitting}
          className="btn-primary w-full flex items-center justify-center gap-2"
        >
          {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Eye className="w-4 h-4" />}
          Verify & view prescriptions
        </button>
        <button
          type="button"
          onClick={onResend}
          disabled={submitting}
          className="w-full text-sm text-gray-500 hover:text-gray-700 disabled:opacity-50"
        >
          Didn&apos;t get a code? Resend
        </button>
      </form>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Step 3 — prescription list (read-only)
// ----------------------------------------------------------------------------

function RxList({
  rx, onDone, hasToken,
}: {
  rx: PortalRxResponse;
  onDone: () => void;
  hasToken: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">
            {rx.customer_first_name ? `${rx.customer_first_name}'s prescriptions` : 'Your prescriptions'}
          </h1>
          <p className="text-sm text-gray-500">
            {rx.count} on record · read-only
          </p>
        </div>
        <button type="button" onClick={onDone} className="btn-outline text-sm">
          Done
        </button>
      </div>

      {!hasToken || rx.count === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-10 text-center">
          <Glasses className="w-10 h-10 mx-auto mb-3 text-gray-400" />
          <p className="text-sm text-gray-600">
            No prescriptions are on file for this number yet. Please visit your
            nearest Better Vision store for an eye examination.
          </p>
        </div>
      ) : (
        rx.prescriptions.map((p) => <RxCard key={p.prescription_id ?? p.prescription_number} p={p} />)
      )}

      <p className="text-xs text-gray-400 text-center px-4">
        This is a read-only copy of your prescription for your reference. For a
        new prescription or any change, please consult your optometrist.
      </p>
    </div>
  );
}

// Pull the canonical eye fields out of whatever shape the record stores them
// in (right_eye/left_eye dicts). We render only what's present.
const EYE_FIELDS: Array<{ key: string; label: string }> = [
  { key: 'sph', label: 'SPH' },
  { key: 'cyl', label: 'CYL' },
  { key: 'axis', label: 'AXIS' },
  { key: 'add', label: 'ADD' },
];

function eyeVal(eye: Record<string, unknown> | null | undefined, key: string): string {
  if (!eye) return '—';
  const v = eye[key] ?? eye[key.toUpperCase()];
  if (v === null || v === undefined || v === '') return '—';
  return String(v);
}

function RxCard({ p }: { p: PortalPrescription }) {
  const expired = p.expiry_date ? new Date(p.expiry_date).getTime() < Date.now() : false;
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900">
            {p.prescription_number || 'Prescription'}
            {p.type && <span className="ml-2 badge-info text-xs">{p.type}</span>}
          </p>
          <p className="text-xs text-gray-500 mt-0.5 flex items-center gap-1.5">
            <CalendarDays className="w-3.5 h-3.5" />
            Issued {formatDate(p.prescription_date)}
          </p>
        </div>
        {p.expiry_date && (
          <span className={`text-xs font-medium ${expired ? 'text-red-600' : 'text-gray-500'}`}>
            {expired ? 'Expired' : 'Valid till'} {formatDate(p.expiry_date)}
          </span>
        )}
      </div>

      <div className="p-5 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500">
              <th className="font-medium pb-2 pr-4">Eye</th>
              {EYE_FIELDS.map((f) => (
                <th key={f.key} className="font-medium pb-2 px-2 text-center">{f.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-gray-100">
              <td className="py-2 pr-4 font-medium text-gray-700">Right (OD)</td>
              {EYE_FIELDS.map((f) => (
                <td key={f.key} className="py-2 px-2 text-center text-gray-800">{eyeVal(p.right_eye, f.key)}</td>
              ))}
            </tr>
            <tr className="border-t border-gray-100">
              <td className="py-2 pr-4 font-medium text-gray-700">Left (OS)</td>
              {EYE_FIELDS.map((f) => (
                <td key={f.key} className="py-2 px-2 text-center text-gray-800">{eyeVal(p.left_eye, f.key)}</td>
              ))}
            </tr>
          </tbody>
        </table>

        {(p.pd || p.add_power || p.optometrist_name || p.store_name) && (
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            {p.pd != null && p.pd !== '' && (
              <div><p className="text-gray-500 text-xs">PD</p><p className="text-gray-800">{String(p.pd)}</p></div>
            )}
            {p.add_power != null && p.add_power !== '' && (
              <div><p className="text-gray-500 text-xs">ADD</p><p className="text-gray-800">{String(p.add_power)}</p></div>
            )}
            {p.optometrist_name && (
              <div><p className="text-gray-500 text-xs">Optometrist</p><p className="text-gray-800">{p.optometrist_name}</p></div>
            )}
            {p.store_name && (
              <div><p className="text-gray-500 text-xs">Store</p><p className="text-gray-800">{p.store_name}</p></div>
            )}
          </div>
        )}

        {p.notes && (
          <div className="mt-4 flex items-start gap-2 text-xs text-gray-500 bg-gray-50 rounded-lg p-3">
            <AlertTriangle className="w-4 h-4 shrink-0 text-amber-500" />
            <span>{p.notes}</span>
          </div>
        )}
      </div>
    </div>
  );
}
