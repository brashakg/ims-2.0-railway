// ============================================================================
// IMS 2.0 - Customer self-service Rx portal (PUBLIC)
// ============================================================================
// Phone -> OTP -> view your own prescriptions. Standalone page (no staff shell)
// and a bare fetch (no staff-token interceptor) hitting /customer-portal/*.

import { useState } from 'react';
import { Eye, Phone, ShieldCheck, Loader2, ArrowLeft } from 'lucide-react';

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1';

interface Rx {
  prescription_id?: string;
  prescription_date?: string;
  test_date?: string;
  created_at?: string;
  right_eye_sph?: number | string; right_eye_cyl?: number | string; right_eye_axis?: number | string; right_eye_add?: number | string;
  left_eye_sph?: number | string; left_eye_cyl?: number | string; left_eye_axis?: number | string; left_eye_add?: number | string;
  pd?: number | string; optometrist_name?: string; validity_months?: number; remarks?: string;
}

type Step = 'phone' | 'otp' | 'rx';

const fmt = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : String(v));

export default function CustomerRxPortal() {
  const [step, setStep] = useState<Step>('phone');
  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [name, setName] = useState('');
  const [rxs, setRxs] = useState<Rx[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');

  const requestOtp = async () => {
    if (phone.replace(/\D/g, '').length < 10) { setError('Enter a valid 10-digit mobile number'); return; }
    setLoading(true); setError(''); setInfo('');
    try {
      const r = await fetch(`${API_BASE}/customer-portal/request-otp`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone }),
      });
      const d = await r.json().catch(() => ({}));
      setInfo(d.message || 'If this number is registered, an OTP has been sent.');
      setStep('otp');
    } catch {
      setError('Could not send OTP. Please try again.');
    } finally { setLoading(false); }
  };

  const verifyOtp = async () => {
    if (otp.length < 4) { setError('Enter the OTP'); return; }
    setLoading(true); setError('');
    try {
      const r = await fetch(`${API_BASE}/customer-portal/verify-otp`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, otp }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setError(d.detail || 'Invalid or expired code'); return;
      }
      const d = await r.json();
      setName(d.customer?.name || '');
      await loadRx(d.token);
      setStep('rx');
    } catch {
      setError('Verification failed. Please try again.');
    } finally { setLoading(false); }
  };

  const loadRx = async (tok: string) => {
    const r = await fetch(`${API_BASE}/customer-portal/prescriptions`, {
      headers: { Authorization: `Bearer ${tok}` },
    });
    const d = await r.json().catch(() => ({ prescriptions: [] }));
    setRxs(d.prescriptions || []);
  };

  const reset = () => { setStep('phone'); setOtp(''); setRxs([]); setError(''); setInfo(''); };

  return (
    <div className="min-h-screen bg-gray-50 flex items-start justify-center p-4 sm:p-8">
      <div className="w-full max-w-2xl">
        <div className="text-center mb-6">
          <div className="inline-flex items-center gap-2 text-bv-red-600">
            <Eye className="w-7 h-7" />
            <span className="text-2xl font-bold">Better Vision</span>
          </div>
          <p className="text-gray-500 mt-1">View your prescription</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
          {error && <div className="mb-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>}

          {step === 'phone' && (
            <div className="space-y-4">
              <h1 className="text-lg font-semibold text-gray-900 flex items-center gap-2"><Phone className="w-5 h-5" /> Enter your mobile number</h1>
              <p className="text-sm text-gray-500">We'll send a one-time code to the number registered with us.</p>
              <input type="tel" inputMode="numeric" value={phone} onChange={(e) => setPhone(e.target.value)}
                placeholder="10-digit mobile" className="input-field text-lg" maxLength={15}
                onKeyDown={(e) => e.key === 'Enter' && requestOtp()} />
              <button onClick={requestOtp} disabled={loading}
                className="w-full bg-bv-red-600 text-white rounded-lg py-2.5 font-medium hover:bg-bv-red-700 disabled:opacity-60 inline-flex items-center justify-center gap-2">
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />} Send OTP
              </button>
            </div>
          )}

          {step === 'otp' && (
            <div className="space-y-4">
              <button onClick={() => setStep('phone')} className="text-sm text-gray-500 inline-flex items-center gap-1 hover:text-gray-700"><ArrowLeft className="w-4 h-4" /> Change number</button>
              <h1 className="text-lg font-semibold text-gray-900">Enter the OTP</h1>
              {info && <p className="text-sm text-gray-500">{info}</p>}
              <input type="text" inputMode="numeric" value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))}
                placeholder="6-digit code" className="input-field text-2xl tracking-[0.4em] text-center" maxLength={6}
                onKeyDown={(e) => e.key === 'Enter' && verifyOtp()} />
              <button onClick={verifyOtp} disabled={loading}
                className="w-full bg-bv-red-600 text-white rounded-lg py-2.5 font-medium hover:bg-bv-red-700 disabled:opacity-60 inline-flex items-center justify-center gap-2">
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Verify &amp; view
              </button>
            </div>
          )}

          {step === 'rx' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h1 className="text-lg font-semibold text-gray-900">{name ? `${name}'s prescriptions` : 'Your prescriptions'}</h1>
                <button onClick={reset} className="text-sm text-bv-red-600 hover:underline">Sign out</button>
              </div>
              {rxs.length === 0 ? (
                <p className="text-sm text-gray-500 py-6 text-center">No prescriptions on record yet.</p>
              ) : (
                rxs.map((rx, i) => (
                  <div key={rx.prescription_id || i} className="border border-gray-200 rounded-lg p-4">
                    <div className="text-xs text-gray-400 mb-2">
                      {fmt(rx.prescription_date || rx.test_date || rx.created_at)}
                      {rx.optometrist_name ? ` · ${rx.optometrist_name}` : ''}
                      {rx.validity_months ? ` · valid ${rx.validity_months} mo` : ''}
                    </div>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-xs text-gray-500 text-left">
                          <th className="py-1"></th><th className="py-1">SPH</th><th className="py-1">CYL</th><th className="py-1">AXIS</th><th className="py-1">ADD</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr className="border-t border-gray-100">
                          <td className="py-1 font-medium text-gray-700">Right (OD)</td>
                          <td className="py-1">{fmt(rx.right_eye_sph)}</td><td className="py-1">{fmt(rx.right_eye_cyl)}</td><td className="py-1">{fmt(rx.right_eye_axis)}</td><td className="py-1">{fmt(rx.right_eye_add)}</td>
                        </tr>
                        <tr className="border-t border-gray-100">
                          <td className="py-1 font-medium text-gray-700">Left (OS)</td>
                          <td className="py-1">{fmt(rx.left_eye_sph)}</td><td className="py-1">{fmt(rx.left_eye_cyl)}</td><td className="py-1">{fmt(rx.left_eye_axis)}</td><td className="py-1">{fmt(rx.left_eye_add)}</td>
                        </tr>
                      </tbody>
                    </table>
                    {(rx.pd || rx.remarks) && (
                      <p className="text-xs text-gray-500 mt-2">{rx.pd ? `PD: ${rx.pd}  ` : ''}{rx.remarks || ''}</p>
                    )}
                  </div>
                ))
              )}
            </div>
          )}
        </div>
        <p className="text-center text-xs text-gray-400 mt-4">Your data is private. This page shows only your own prescriptions.</p>
      </div>
    </div>
  );
}
