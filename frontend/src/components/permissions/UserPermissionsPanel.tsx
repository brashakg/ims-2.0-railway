// ============================================================================
// IMS 2.0 - Per-user Permissions panel (backlog #14)
// ============================================================================
// A discoverable per-user permissions editor + audit-history timeline + revert.
// Wraps the shared PermissionDeltaEditor and adds:
//   - load the user's CURRENT override from GET /users/{id}/permissions
//   - save changed dimensions via PUT /users/{id} (only fields that changed)
//   - the immutable change-history timeline from `history[]`
//   - a "Revert" action per history row -> POST /users/{id}/permissions/revert
//
// All four locked dimensions (discount-cap, module access, returns/refund
// approval capability, per-screen access) are edited through the embedded
// PermissionDeltaEditor; this panel owns persistence + the audit timeline.

import { useCallback, useEffect, useState } from 'react';
import { History, Loader2, RotateCcw, Save, ShieldCheck } from 'lucide-react';
import { adminUserApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import {
  PermissionDeltaEditor,
  type CapabilityOverride,
} from './PermissionDeltaEditor';

interface HistoryRow {
  log_id?: string;
  action?: string;
  // The audit row carries actor_username + timestamp (permission_audit.py).
  // actor_name / created_at are tolerated for forward-compat.
  actor_name?: string;
  actor_username?: string;
  timestamp?: string;
  created_at?: string;
  note?: string;
  [k: string]: unknown;
}

function formatWhen(raw?: string): string {
  if (!raw) return '';
  const d = new Date(raw);
  if (isNaN(d.getTime())) return String(raw).slice(0, 19).replace('T', ' ');
  return d.toLocaleString();
}

function actionLabel(action?: string): string {
  switch (action) {
    case 'PERMISSIONS_CREATE':
      return 'Override set at creation';
    case 'PERMISSIONS_UPDATE':
      return 'Permissions changed';
    case 'PERMISSIONS_REVERT':
      return 'Reverted to a prior state';
    default:
      return action ? action.replace(/_/g, ' ') : 'Permission change';
  }
}

export function UserPermissionsPanel({
  userId,
  roles,
  userName,
}: {
  userId: string;
  roles: string[];
  userName?: string;
}) {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [revertingId, setRevertingId] = useState<string | null>(null);

  const [permissions, setPermissions] = useState<CapabilityOverride>({});
  const [discountCap, setDiscountCap] = useState<number | undefined>(undefined);
  const [moduleAccess, setModuleAccess] = useState<Record<string, boolean>>({});
  const [history, setHistory] = useState<HistoryRow[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await adminUserApi.getUserPermissions(userId);
      setPermissions(data.permissions || {});
      setDiscountCap(data.discount_cap == null ? undefined : data.discount_cap);
      setModuleAccess(data.module_access || {});
      setHistory(Array.isArray(data.history) ? (data.history as HistoryRow[]) : []);
    } catch {
      toast.error('Could not load this user’s permissions.');
    } finally {
      setLoading(false);
    }
  }, [userId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async () => {
    setSaving(true);
    try {
      // Send only the override-bearing fields. The backend escalation-guards +
      // audits each (PUT /users/{id} with exclude_unset on the server side).
      await adminUserApi.updateUser(userId, {
        discountCap,
        moduleAccess,
        permissions,
      });
      toast.success('Permissions saved.');
      await load(); // refresh the history timeline with the new audit row
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save permissions.');
    } finally {
      setSaving(false);
    }
  };

  const handleRevert = async (logId?: string) => {
    if (!logId) return;
    if (!window.confirm('Re-apply this earlier permission state? This is recorded in the audit trail.')) {
      return;
    }
    setRevertingId(logId);
    try {
      await adminUserApi.revertUserPermissions(userId, logId);
      toast.success('Reverted to the selected state.');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to revert.');
    } finally {
      setRevertingId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-gray-500 text-sm py-6">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading permissions...
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-bv-red-600" />
        <h4 className="text-sm font-semibold text-gray-900">
          Customize permissions{userName ? ` — ${userName}` : ''}
        </h4>
      </div>

      <PermissionDeltaEditor
        roles={roles}
        permissions={permissions}
        onPermissionsChange={setPermissions}
        discountCap={discountCap}
        onDiscountCapChange={setDiscountCap}
        moduleAccess={moduleAccess}
        onModuleAccessChange={setModuleAccess}
      />

      <div className="flex justify-end">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="btn-primary flex items-center gap-2 disabled:opacity-60"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          Save permissions
        </button>
      </div>

      {/* ---- Audit-history timeline (history[]) + revert ------------------- */}
      <div className="pt-4 border-t border-gray-200">
        <div className="flex items-center gap-2 mb-3">
          <History className="w-4 h-4 text-gray-500" />
          <h5 className="text-sm font-medium text-gray-700">Change history</h5>
        </div>
        {history.length === 0 ? (
          <p className="text-xs text-gray-500">
            No permission changes recorded yet — this person uses their standard role.
          </p>
        ) : (
          <ol className="space-y-2">
            {history.map((h, idx) => {
              const who = h.actor_name || h.actor_username || 'Someone';
              const when = h.created_at || h.timestamp;
              const logId = h.log_id;
              const isReverting = revertingId === logId;
              return (
                <li
                  key={logId || idx}
                  className="flex items-start justify-between gap-3 p-3 bg-gray-50 border border-gray-200 rounded-lg"
                >
                  <div className="min-w-0">
                    <p className="text-sm text-gray-800">{actionLabel(h.action)}</p>
                    <p className="text-xs text-gray-500">
                      {who}
                      {when ? ` · ${formatWhen(when)}` : ''}
                    </p>
                    {h.note ? (
                      <p className="text-xs text-gray-400 mt-0.5">{String(h.note)}</p>
                    ) : null}
                  </div>
                  {logId && (
                    <button
                      type="button"
                      onClick={() => handleRevert(logId)}
                      disabled={isReverting}
                      className="text-xs flex items-center gap-1 text-bv-red-600 hover:text-bv-red-700 font-medium disabled:opacity-60 flex-shrink-0"
                      title="Re-apply this earlier permission state (re-checks your level)"
                    >
                      {isReverting ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <RotateCcw className="w-3.5 h-3.5" />
                      )}
                      Revert to this
                    </button>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}

export default UserPermissionsPanel;
