// ============================================================================
// IMS 2.0 — Go-Live Readiness Checklist
// ============================================================================
// One screen showing what's done vs still missing before the first live sale:
// stores entered, GSTIN on file, staff logins, products loaded, product tax
// codes set, invoice numbering. Each item shows a status and a 'do this next'
// link. Read-only — it reflects the real DB; it doesn't change anything.

import { useEffect, useState, type ReactElement } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CheckCircle2, AlertTriangle, XCircle, Loader2, RefreshCw, ArrowRight, Rocket,
} from 'lucide-react';
import { storeApi } from '../../services/api';
import type { GoLiveChecklist, GoLiveCheck } from '../../services/api/stores';

const ICON: Record<GoLiveCheck['status'], ReactElement> = {
  PASS: <CheckCircle2 className="w-5 h-5 text-emerald-600" />,
  WARN: <AlertTriangle className="w-5 h-5 text-amber-500" />,
  FAIL: <XCircle className="w-5 h-5 text-red-600" />,
};

const ROW_BORDER: Record<GoLiveCheck['status'], string> = {
  PASS: 'border-l-emerald-400',
  WARN: 'border-l-amber-400',
  FAIL: 'border-l-red-500',
};

export function GoLiveChecklistPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<GoLiveChecklist | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    storeApi.getGoLiveChecklist()
      .then(setData)
      .catch((e) => setError(e?.response?.data?.detail || e?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="p-6 space-y-4 max-w-3xl mx-auto">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900 inline-flex items-center gap-2">
            <Rocket className="w-5 h-5 text-bv-red-600" /> Go-Live Readiness
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Everything that needs to be in place before your first real sale. Fix the red and
            amber items — green means you're ready.
          </p>
        </div>
        <button onClick={load} disabled={loading} className="btn-outline text-sm inline-flex items-center gap-1">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      {loading && (
        <div className="h-40 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
        </div>
      )}
      {error && (
        <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">
          <AlertTriangle className="w-4 h-4 mt-0.5" /> {error}
        </div>
      )}

      {data && !loading && (
        <>
          {/* Banner */}
          {data.ready ? (
            <div className="flex items-center gap-3 bg-emerald-50 border border-emerald-200 rounded-lg p-4">
              <CheckCircle2 className="w-6 h-6 text-emerald-600 flex-shrink-0" />
              <div>
                <p className="font-semibold text-emerald-800">You're ready to go live.</p>
                <p className="text-sm text-emerald-700">
                  No hard blockers.{' '}
                  {data.summary.warn > 0 && `${data.summary.warn} item(s) worth tidying up, but you can start billing.`}
                </p>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-3 bg-red-50 border border-red-200 rounded-lg p-4">
              <XCircle className="w-6 h-6 text-red-600 flex-shrink-0" />
              <div>
                <p className="font-semibold text-red-800">Not ready yet.</p>
                <p className="text-sm text-red-700">
                  {data.summary.fail} blocker(s) must be fixed before your first sale.
                </p>
              </div>
            </div>
          )}

          {/* Progress strip */}
          <div className="flex items-center gap-4 text-sm">
            <span className="inline-flex items-center gap-1 text-emerald-700"><CheckCircle2 className="w-4 h-4" /> {data.summary.pass} done</span>
            <span className="inline-flex items-center gap-1 text-amber-600"><AlertTriangle className="w-4 h-4" /> {data.summary.warn} to tidy</span>
            <span className="inline-flex items-center gap-1 text-red-600"><XCircle className="w-4 h-4" /> {data.summary.fail} blocking</span>
          </div>

          {/* Checks */}
          <div className="space-y-2">
            {data.checks.map((c) => (
              <div
                key={c.key}
                className={`flex items-center gap-3 bg-white border border-gray-200 border-l-4 ${ROW_BORDER[c.status]} rounded-lg p-3`}
              >
                <div className="flex-shrink-0">{ICON[c.status]}</div>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-gray-900">{c.label}</p>
                  <p className="text-xs text-gray-500">{c.hint}</p>
                </div>
                {c.route && c.status !== 'PASS' && (
                  <button
                    onClick={() => navigate(c.route!)}
                    className="text-sm text-bv-red-600 hover:text-bv-red-700 inline-flex items-center gap-1 flex-shrink-0"
                  >
                    Fix <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default GoLiveChecklistPage;
