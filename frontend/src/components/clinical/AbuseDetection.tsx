// ============================================================================
// IMS 2.0 - Clinical Abuse Detection
// ============================================================================

import { useEffect, useState } from 'react';
import { AlertTriangle, TrendingUp, Clock, Copy, CheckCircle, Loader2 } from 'lucide-react';
import { clinicalApi } from '../../services/api';
import type { AbuseAlert } from '../../services/api/clinical';

export type { AbuseAlert };

interface AbuseDetectionProps {
  /** When provided, alerts are rendered as-is (controlled mode).
   *  When omitted, the component fetches its own alerts from the backend. */
  alerts?: AbuseAlert[];
  /** Store to scope the fetch to (self-fetching mode). */
  storeId?: string;
  /** Lookback window in days (self-fetching mode). Defaults to 30. */
  days?: number;
}


const getAlertIcon = (type: AbuseAlert['type']) => {
  switch (type) {
    case 'high-redo-rate':
      return TrendingUp;
    case 'exact-copy':
      return Copy;
    case 'suspicious-speed':
      return Clock;
  }
};

const getAlertTitle = (type: AbuseAlert['type']) => {
  switch (type) {
    case 'high-redo-rate':
      return 'High Redo Rate';
    case 'exact-copy':
      return 'Exact Copy Detected';
    case 'suspicious-speed':
      return 'Suspicious Speed';
  }
};

export function AbuseDetection({ alerts: alertsProp, storeId, days = 30 }: AbuseDetectionProps) {
  // Controlled mode: caller passed alerts -> render them and skip fetching.
  // Self-fetching mode (default): pull alerts from the backend on mount.
  const controlled = alertsProp !== undefined;
  const [fetchedAlerts, setFetchedAlerts] = useState<AbuseAlert[]>([]);
  const [loading, setLoading] = useState(!controlled);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (controlled) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    clinicalApi
      .getAbuseAlerts(storeId, days)
      .then(res => {
        if (cancelled) return;
        setFetchedAlerts(Array.isArray(res?.alerts) ? res.alerts : []);
      })
      .catch(() => {
        if (!cancelled) setError('Failed to load abuse alerts.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [controlled, storeId, days]);

  const alerts = controlled ? (alertsProp as AbuseAlert[]) : fetchedAlerts;

  if (loading) {
    return (
      <div className="p-8 bg-white rounded-lg border border-gray-200 text-center">
        <Loader2 className="w-8 h-8 mx-auto animate-spin text-bv-red-600" />
        <p className="text-sm text-gray-500 mt-3">Scanning recent clinical activity...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 bg-white rounded-lg border border-gray-200 text-center">
        <div className="text-gray-500">
          <AlertTriangle className="w-12 h-12 mx-auto mb-4 text-yellow-600" />
          <p className="text-lg font-semibold mb-2">Could not load alerts</p>
          <p className="text-sm">{error}</p>
        </div>
      </div>
    );
  }

  if (alerts.length === 0) {
    return (
      <div className="p-8 bg-white rounded-lg border border-gray-200 text-center">
        <div className="text-gray-500">
          <CheckCircle className="w-12 h-12 mx-auto mb-4" />
          <p className="text-lg font-semibold mb-2">No Issues Detected</p>
          <p className="text-sm">All optometrists are performing within acceptable parameters</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="text-sm text-yellow-600 bg-yellow-50 bg-opacity-30 border border-yellow-700 rounded-lg p-4">
        <p className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          <strong>{alerts.length} clinical issue(s) detected requiring review</strong>
        </p>
      </div>

      {alerts.map(alert => {
        const Icon = getAlertIcon(alert.type);
        return (
          <div
            key={alert.id}
            className={`p-4 rounded-lg border-2 ${
              alert.severity === 'critical'
                ? 'bg-red-50 bg-opacity-20 border-red-600'
                : 'bg-yellow-50 bg-opacity-20 border-yellow-600'
            }`}
          >
            <div className="flex items-start gap-4">
              <div className={`p-3 rounded-full ${
                alert.severity === 'critical' ? 'bg-red-50' : 'bg-yellow-50'
              }`}>
                <Icon className={`w-5 h-5 ${
                  alert.severity === 'critical' ? 'text-red-600' : 'text-yellow-600'
                }`} />
              </div>

              <div className="flex-1">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-semibold text-gray-900">{getAlertTitle(alert.type)}</h3>
                  <span className={`text-xs font-medium px-2 py-1 rounded ${
                    alert.severity === 'critical'
                      ? 'bg-red-50 text-red-200'
                      : 'bg-yellow-50 text-yellow-200'
                  }`}>
                    {alert.severity === 'critical' ? 'CRITICAL' : 'WARNING'}
                  </span>
                </div>

                <p className="text-gray-600 text-sm mb-2">{alert.details}</p>

                <div className="flex items-center justify-between text-xs text-gray-500">
                  <div className="space-y-1">
                    <p>Optometrist: <strong className="text-gray-600">{alert.optometristName}</strong></p>
                    {alert.redoRate && (
                      <p>Redo Rate: <strong className="text-gray-600">{alert.redoRate.toFixed(1)}%</strong></p>
                    )}
                    {alert.prescriptionIds && (
                      <p>Prescriptions: <strong className="text-gray-600">{alert.prescriptionIds.join(', ')}</strong></p>
                    )}
                  </div>
                  <div className="text-right">
                    <p className="text-gray-500">{new Date(alert.timestamp).toLocaleString('en-IN')}</p>
                    <button className="mt-2 px-3 py-1 text-xs bg-gray-100 hover:bg-gray-200 text-gray-600 rounded transition">
                      Review
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

