// ============================================================================
// IMS 2.0 - Settings: Login Devices (approved-device gate, owner 2026-09-02)
// ============================================================================
// The owner's approval screen. DESIGNED FOR A PHONE FIRST - his stated
// workflow is "approve devices from my mobile phone by just logging in to
// our IMS using browser on my phone": single column, big cards, 44px
// tap targets, pending requests on top.
//
// Approval is SUPERADMIN-only (backend-enforced; an ADMIN is exempt from the
// gate but cannot approve). The banner states the live gate mode so nobody
// mistakes a dark deploy for an armed one.

import { useCallback, useEffect, useState } from 'react';
import {
  CheckCircle2,
  CloudOff,
  MonitorSmartphone,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { devicesApi } from '../../services/api/devices';
import type { LoginDevice } from '../../services/api/devices';

const MODE_COPY: Record<string, { label: string; tone: string; blurb: string }> = {
  off: {
    label: 'Gate OFF (dark)',
    tone: 'bg-gray-100 text-gray-700',
    blurb:
      'The device gate is deployed but switched off - every sign-in works as before. Devices approved now take effect when the gate is armed.',
  },
  log: {
    label: 'Gate in DRY-RUN',
    tone: 'bg-amber-100 text-amber-800',
    blurb:
      'Sign-ins are checked and logged but never blocked. Watch the logs, then arm enforce mode.',
  },
  enforce: {
    label: 'Gate ENFORCED',
    tone: 'bg-green-100 text-green-800',
    blurb:
      'Staff can sign in only on approved devices. Admin and Superadmin accounts are never blocked.',
  },
};

function when(iso?: string | null): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('en-IN', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

export default function SettingsDevices() {
  const { user } = useAuth();
  const toast = useToast();
  const isSuperadmin = (user?.roles || []).includes('SUPERADMIN');

  const [mode, setMode] = useState<string>('off');
  const [devices, setDevices] = useState<LoginDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await devicesApi.list();
      setMode(data.mode);
      setDevices(data.devices);
    } catch (e: any) {
      setError(
        e?.response?.status === 403
          ? 'Only the Superadmin can view and approve login devices.'
          : 'Could not load devices. Pull to refresh or tap Retry.'
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (device: LoginDevice, action: 'approve' | 'revoke') => {
    setBusyId(device.device_id);
    setConfirmRevokeId(null);
    try {
      const updated =
        action === 'approve'
          ? await devicesApi.approve(device.device_id)
          : await devicesApi.revoke(device.device_id);
      setDevices((prev) =>
        prev.map((d) => (d.device_id === updated.device_id ? updated : d))
      );
      toast.success(
        action === 'approve'
          ? `Approved "${device.device_name}" - it can sign staff in now.`
          : `"${device.device_name}" can no longer sign staff in.`
      );
    } catch {
      toast.error('That did not go through. Check your connection and retry.');
    } finally {
      setBusyId(null);
    }
  };

  const modeCopy = MODE_COPY[mode] || MODE_COPY.off;
  const pending = devices.filter((d) => d.status === 'PENDING');
  const approved = devices.filter((d) => d.status === 'APPROVED');
  const revoked = devices.filter((d) => d.status === 'REVOKED');

  const DeviceCard = ({ device }: { device: LoginDevice }) => {
    const busy = busyId === device.device_id;
    const confirming = confirmRevokeId === device.device_id;
    return (
      <div className="card space-y-3">
        <div className="flex items-start gap-3">
          <MonitorSmartphone className="w-6 h-6 text-gray-400 shrink-0 mt-0.5" />
          <div className="min-w-0">
            <p className="font-semibold text-gray-900 break-words">
              {device.device_name}
            </p>
            <p className="text-sm text-gray-500">
              Requested by{' '}
              <span className="font-medium text-gray-700">
                {device.requested_by?.username || 'unknown'}
              </span>{' '}
              · {when(device.requested_at)}
            </p>
            {device.status === 'APPROVED' && device.approved_at && (
              <p className="text-xs text-gray-400 mt-0.5">
                Approved by {device.approved_by?.username || '-'} ·{' '}
                {when(device.approved_at)}
              </p>
            )}
            {device.status === 'REVOKED' && device.revoked_at && (
              <p className="text-xs text-gray-400 mt-0.5">
                Revoked by {device.revoked_by?.username || '-'} ·{' '}
                {when(device.revoked_at)}
              </p>
            )}
            {device.backup_eligible && (
              <span className="inline-flex items-center gap-1 mt-2 px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 text-xs font-medium">
                <ShieldAlert className="w-3.5 h-3.5" />
                Synced passkey - may also work on this person's other devices
              </span>
            )}
          </div>
        </div>

        {isSuperadmin && device.status === 'PENDING' && (
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => act(device, 'approve')}
              className="btn-primary flex-1 min-h-[44px] justify-center"
            >
              {busy ? (
                <RefreshCw className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  <CheckCircle2 className="w-4 h-4 mr-2" />
                  Approve
                </>
              )}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => act(device, 'revoke')}
              className="btn-outline flex-1 min-h-[44px] justify-center text-red-600 border-red-200"
            >
              Reject
            </button>
          </div>
        )}

        {isSuperadmin && device.status === 'APPROVED' && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              confirming ? act(device, 'revoke') : setConfirmRevokeId(device.device_id)
            }
            className={clsx(
              'btn-outline w-full min-h-[44px] justify-center',
              confirming
                ? 'bg-red-600 text-white border-red-600'
                : 'text-red-600 border-red-200'
            )}
          >
            {busy ? (
              <RefreshCw className="w-4 h-4 animate-spin" />
            ) : confirming ? (
              'Tap again to revoke'
            ) : (
              'Revoke device'
            )}
          </button>
        )}

        {isSuperadmin && device.status === 'REVOKED' && (
          <button
            type="button"
            disabled={busy}
            onClick={() => act(device, 'approve')}
            className="btn-outline w-full min-h-[44px] justify-center"
          >
            Re-approve
          </button>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4 max-w-xl">
      <div className="card space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <ShieldCheck className="w-5 h-5 text-gray-500" />
          <h2 className="text-lg font-semibold text-gray-900">Login Devices</h2>
          <span
            className={clsx(
              'px-2 py-0.5 rounded-full text-xs font-semibold',
              modeCopy.tone
            )}
          >
            {modeCopy.label}
          </span>
        </div>
        <p className="text-sm text-gray-500">
          Staff (everyone except Admin and Superadmin) can sign in only on
          devices you approve here. {modeCopy.blurb}
        </p>
      </div>

      {error && (
        <div className="card flex items-center gap-2 border-red-200 bg-red-50">
          <CloudOff className="w-5 h-5 text-red-500 shrink-0" />
          <span className="text-sm text-red-700">{error}</span>
          <button onClick={load} className="btn-outline ml-auto min-h-[44px] text-sm">
            Retry
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-40">
          <RefreshCw className="w-7 h-7 animate-spin text-gray-400" />
        </div>
      ) : (
        !error && (
          <>
            <section className="space-y-2">
              <h3 className="text-sm font-semibold text-gray-700">
                Awaiting approval {pending.length > 0 && `(${pending.length})`}
              </h3>
              {pending.length === 0 ? (
                <div className="card text-center py-8 text-gray-500 text-sm">
                  No pending requests. When staff register a store device, it
                  appears here for one-tap approval.
                </div>
              ) : (
                pending.map((d) => <DeviceCard key={d.device_id} device={d} />)
              )}
            </section>

            <section className="space-y-2">
              <h3 className="text-sm font-semibold text-gray-700">
                Approved devices {approved.length > 0 && `(${approved.length})`}
              </h3>
              {approved.length === 0 ? (
                <div className="card text-center py-8 text-gray-500 text-sm">
                  No approved devices yet.
                </div>
              ) : (
                approved.map((d) => <DeviceCard key={d.device_id} device={d} />)
              )}
            </section>

            {revoked.length > 0 && (
              <section className="space-y-2">
                <button
                  type="button"
                  onClick={() => setShowRevoked((v) => !v)}
                  className="text-sm font-semibold text-gray-500 min-h-[44px]"
                >
                  {showRevoked ? 'Hide' : 'Show'} revoked devices ({revoked.length})
                </button>
                {showRevoked &&
                  revoked.map((d) => <DeviceCard key={d.device_id} device={d} />)}
              </section>
            )}
          </>
        )
      )}
    </div>
  );
}
