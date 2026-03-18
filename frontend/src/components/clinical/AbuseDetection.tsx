// ============================================================================
// IMS 2.0 - Clinical Abuse Detection
// ============================================================================

import { AlertTriangle, TrendingUp, Clock, Copy } from 'lucide-react';

interface AbuseAlert {
  id: string;
  type: 'high-redo-rate' | 'exact-copy' | 'suspicious-speed';
  severity: 'warning' | 'critical';
  optometristName: string;
  optometristId: string;
  details: string;
  timestamp: string;
  prescriptionIds?: string[];
  redoRate?: number;
}

interface AbuseDetectionProps {
  alerts?: AbuseAlert[];
}

const SAMPLE_ALERTS: AbuseAlert[] = [
  {
    id: 'alert-001',
    type: 'high-redo-rate',
    severity: 'critical',
    optometristName: 'Dr. Rajesh Kumar',
    optometristId: 'opt-001',
    details: 'Redo rate is 18.5% for the last 30 days (threshold: 15%)',
    timestamp: new Date(Date.now() - 2 * 60 * 60000).toISOString(),
    redoRate: 18.5,
  },
  {
    id: 'alert-002',
    type: 'exact-copy',
    severity: 'warning',
    optometristName: 'Dr. Priya Sharma',
    optometristId: 'opt-002',
    details: 'Prescription RX-5432 is an exact copy of RX-5401 (created 5 days apart)',
    timestamp: new Date(Date.now() - 6 * 60 * 60000).toISOString(),
    prescriptionIds: ['RX-5432', 'RX-5401'],
  },
  {
    id: 'alert-003',
    type: 'suspicious-speed',
    severity: 'warning',
    optometristName: 'Dr. Amit Patel',
    optometristId: 'opt-003',
    details: '3 prescriptions created within 25 minutes for different patients',
    timestamp: new Date(Date.now() - 12 * 60 * 60000).toISOString(),
  },
];

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

export function AbuseDetection({ alerts = SAMPLE_ALERTS }: AbuseDetectionProps) {
  if (alerts.length === 0) {
    return (
      <div className="p-8 bg-gray-800 rounded-lg border border-gray-700 text-center">
        <div className="text-gray-400">
          <CheckCircle className="w-12 h-12 mx-auto mb-4" />
          <p className="text-lg font-semibold mb-2">No Issues Detected</p>
          <p className="text-sm">All optometrists are performing within acceptable parameters</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="text-sm text-yellow-400 bg-yellow-900 bg-opacity-30 border border-yellow-700 rounded-lg p-4">
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
                ? 'bg-red-900 bg-opacity-20 border-red-600'
                : 'bg-yellow-900 bg-opacity-20 border-yellow-600'
            }`}
          >
            <div className="flex items-start gap-4">
              <div className={`p-3 rounded-full ${
                alert.severity === 'critical' ? 'bg-red-900' : 'bg-yellow-900'
              }`}>
                <Icon className={`w-5 h-5 ${
                  alert.severity === 'critical' ? 'text-red-400' : 'text-yellow-400'
                }`} />
              </div>

              <div className="flex-1">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-semibold text-white">{getAlertTitle(alert.type)}</h3>
                  <span className={`text-xs font-medium px-2 py-1 rounded ${
                    alert.severity === 'critical'
                      ? 'bg-red-900 text-red-200'
                      : 'bg-yellow-900 text-yellow-200'
                  }`}>
                    {alert.severity === 'critical' ? 'CRITICAL' : 'WARNING'}
                  </span>
                </div>

                <p className="text-gray-300 text-sm mb-2">{alert.details}</p>

                <div className="flex items-center justify-between text-xs text-gray-400">
                  <div className="space-y-1">
                    <p>Optometrist: <strong className="text-gray-300">{alert.optometristName}</strong></p>
                    {alert.redoRate && (
                      <p>Redo Rate: <strong className="text-gray-300">{alert.redoRate.toFixed(1)}%</strong></p>
                    )}
                    {alert.prescriptionIds && (
                      <p>Prescriptions: <strong className="text-gray-300">{alert.prescriptionIds.join(', ')}</strong></p>
                    )}
                  </div>
                  <div className="text-right">
                    <p className="text-gray-500">{new Date(alert.timestamp).toLocaleString('en-IN')}</p>
                    <button className="mt-2 px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 rounded transition">
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

// Helper component that was missing
function CheckCircle({ className }: { className: string }) {
  return (
    <svg className={className} fill="currentColor" viewBox="0 0 20 20">
      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
    </svg>
  );
}
