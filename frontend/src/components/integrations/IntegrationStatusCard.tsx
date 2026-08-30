// ============================================================================
// IMS 2.0 - Integration Status Card (read-only, SUPERADMIN only)
// ============================================================================
// Honest, read-only view of which external integrations are live vs dormant,
// driven by GET /api/v1/jarvis/integrations/status. Shows KEY presence only -
// never a secret value. Self-gates to SUPERADMIN so it can be dropped on both
// the Jarvis page and the (ADMIN-visible) Settings page without leaking.

import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, Loader2, Check, X, AlertTriangle } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import {
  getIntegrationStatus,
  type IntegrationStatusReport,
  type IntegrationStatusItem,
  type MessagingPreflightRow,
} from '../../services/api/integrations';

const STATE_META: Record<string, { label: string; cls: string }> = {
  live: { label: 'Live', cls: 'bg-green-100 text-green-800' },
  active: { label: 'Active', cls: 'bg-green-100 text-green-800' },
  test_only: { label: 'Test only', cls: 'bg-blue-100 text-blue-800' },
  simulated: { label: 'Simulated', cls: 'bg-amber-100 text-amber-800' },
  dormant: { label: 'Dormant', cls: 'bg-gray-100 text-gray-600' },
  export_only: { label: 'Export only', cls: 'bg-teal-100 text-teal-800' },
  not_wired: { label: 'Not wired', cls: 'bg-gray-100 text-gray-500' },
};

function dispatchBadgeCls(mode: string): string {
  if (mode === 'live') return 'bg-green-100 text-green-800';
  if (mode === 'test') return 'bg-blue-100 text-blue-800';
  return 'bg-gray-100 text-gray-600';
}

function IntegrationRow({ item }: { item: IntegrationStatusItem }) {
  const meta = STATE_META[item.state] ?? { label: item.state, cls: 'bg-gray-100 text-gray-600' };
  return (
    <div className="py-3 border-t border-gray-100 first:border-t-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium text-gray-900">{item.label}</div>
          <div className="text-xs text-gray-500">{item.powers}</div>
        </div>
        <span className={clsx('px-2.5 py-0.5 rounded-full text-xs font-medium whitespace-nowrap', meta.cls)}>
          {meta.label}
        </span>
      </div>

      {/* Env-var KEY presence (names + booleans only, never values) */}
      {item.env_keys.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {item.env_keys.map((k) => (
            <span
              key={k.key}
              className={clsx(
                'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono',
                k.present ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-400'
              )}
              title={k.present ? 'set on Railway' : 'not set'}
            >
              {k.present ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
              {k.key}
            </span>
          ))}
        </div>
      )}

      {/* Integrations-collection state (for Razorpay / Shopify / Shiprocket) */}
      {item.collection && (
        <div className="mt-2 text-xs text-gray-500">
          <span className="font-medium">DB config:</span>{' '}
          {item.collection.exists ? (
            <>
              <span className={item.collection.enabled ? 'text-green-700' : 'text-gray-500'}>
                {item.collection.enabled ? 'enabled' : 'disabled'}
              </span>
              {item.collection.present_keys.length > 0 && (
                <span className="font-mono"> · {item.collection.present_keys.join(', ')}</span>
              )}
              {item.collection.missing_required.length > 0 && (
                <span className="text-amber-700">
                  {' '}· missing: {item.collection.missing_required.join(', ')}
                </span>
              )}
            </>
          ) : (
            <span className="text-gray-400">not configured</span>
          )}
        </div>
      )}

      {item.notes && (
        <div className="mt-1.5 flex items-start gap-1 text-xs text-gray-500">
          <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0 text-amber-500" />
          <span>
            {item.notes}
            {item.docs && (
              <>
                {' '}
                <a
                  href={item.docs}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  Docs
                </a>
              </>
            )}
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Messaging preflight (MSG91 + Coexistence): honest readiness rows, each with
// the owner's named next step. Rendered only when the backend supplies it.
// ---------------------------------------------------------------------------

function PreflightRow({ row }: { row: MessagingPreflightRow }) {
  return (
    <div className="py-2 border-t border-gray-100 first:border-t-0" data-testid={`preflight-${row.id}`}>
      <div className="flex items-start gap-2">
        {row.ok ? (
          <Check className="w-4 h-4 mt-0.5 shrink-0 text-green-600" />
        ) : (
          <X className="w-4 h-4 mt-0.5 shrink-0 text-amber-600" />
        )}
        <div className="min-w-0">
          <span className="text-sm font-medium text-gray-900">{row.label}</span>
          <span className="text-sm text-gray-500"> — {row.detail}</span>
          {!row.ok && row.next_step && (
            <div className="mt-0.5 text-xs text-amber-700">
              Next step: {row.next_step}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function MessagingPreflightPanel({ report }: { report: IntegrationStatusReport }) {
  const pf = report.messaging_preflight;
  if (!pf || !Array.isArray(pf.rows) || pf.rows.length === 0) return null;
  return (
    <div className="mt-5">
      <div className="flex items-center gap-2 mb-1">
        <h4 className="font-semibold text-gray-900 text-sm">Messaging preflight (MSG91 + Coexistence)</h4>
        <span
          className={clsx(
            'px-2 py-0.5 rounded-full text-[10px] font-medium',
            pf.ok ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800',
          )}
        >
          {pf.ok ? 'Ready' : 'Not ready'}
        </span>
      </div>
      <p className="text-xs text-gray-500 mb-2">
        Everything WhatsApp/SMS needs before arming, checked live. Nothing sends
        until DISPATCH_MODE is armed on the server.
      </p>
      <div>
        {pf.rows.map((row) => (
          <PreflightRow key={row.id} row={row} />
        ))}
      </div>
    </div>
  );
}

export function IntegrationStatusCard() {
  const { hasRole } = useAuth();
  const isSuperAdmin = hasRole(['SUPERADMIN']);

  const [report, setReport] = useState<IntegrationStatusReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getIntegrationStatus();
      setReport(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load integration status');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isSuperAdmin) load();
  }, [isSuperAdmin, load]);

  // SUPERADMIN-only surface - render nothing for everyone else.
  if (!isSuperAdmin) return null;

  return (
    <div className="bg-white rounded-lg border-2 border-gray-200 p-6">
      <div className="flex items-center justify-between gap-3 mb-1 flex-wrap">
        <h3 className="font-semibold text-gray-900">Integration status</h3>
        <div className="flex items-center gap-2">
          {report && (
            <span
              className={clsx('px-2.5 py-0.5 rounded-full text-xs font-medium', dispatchBadgeCls(report.dispatch_mode))}
              title="DISPATCH_MODE gates outbound sends, bookings and pushes"
            >
              DISPATCH_MODE: {report.dispatch_mode}
            </span>
          )}
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded hover:bg-gray-100 text-gray-500 transition disabled:opacity-50"
            title="Refresh"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </button>
        </div>
      </div>

      <p className="text-xs text-gray-500 mb-3">
        Read-only. Shows which integrations have credentials set (KEY presence only - never values).
        Outbound sends, bookings and pushes only go live when DISPATCH_MODE=live.
        {report && (
          <>
            {' '}
            <span className="font-medium text-gray-700">
              {report.summary.configured}/{report.summary.total} configured.
            </span>
          </>
        )}
      </p>

      {loading && !report && (
        <div className="flex items-center justify-center py-8 text-gray-500">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      )}

      {error && !report && (
        <div className="py-4 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-3">
          {error}
        </div>
      )}

      {report && (
        <div className="divide-gray-100">
          {report.integrations.map((item) => (
            <IntegrationRow key={item.id} item={item} />
          ))}
        </div>
      )}

      {report && <MessagingPreflightPanel report={report} />}
    </div>
  );
}

export default IntegrationStatusCard;
