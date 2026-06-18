// ============================================================================
// IMS 2.0 - User Activity Log (SUPERADMIN only)
// ============================================================================
// "Who did what, when." A SUPERADMIN-only audit-trail explorer: pick a user
// (or all), a date range, and an optional action, then see every recorded
// action newest-first with expandable before/after detail. All data is live
// from GET /settings/audit-logs (the immutable `audit_logs` collection) — the
// SAME trail JARVIS reads, so the AI can answer the same questions in chat.
//
// Read-only by construction: this screen never mutates; it only queries the
// audit trail. Gated to SUPERADMIN at the route AND reinforced here.

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import {
  ShieldCheck,
  Loader2,
  ChevronDown,
  AlertCircle,
  RefreshCw,
  Sparkles,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { settingsApi } from '../../services/api/settings';
import { adminUserApi, orgStoreApi } from '../../services/api/stores';
import { entitiesApi } from '../../services/api/entities';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

interface AuditRow {
  log_id?: string;
  id?: string;
  timestamp?: string;
  user_id?: string;
  username?: string;
  user_name?: string;
  action?: string;
  entity_type?: string;
  entity_id?: string;
  entity_name?: string;
  store_id?: string;
  /** Resolved store display name (router enrichment); falls back to store_id. */
  store_name?: string;
  severity?: string;
  status?: string;
  description?: string;
  details?: unknown;
  ip_address?: string;
  changes?: Array<{
    field?: string;
    oldValue?: unknown;
    newValue?: unknown;
    old_value?: unknown;
    new_value?: unknown;
  }>;
}

interface UserOpt {
  id: string;
  label: string;
  role?: string;
}

interface StoreOpt {
  // `value` is what lands in audit rows' store_id (the human store code, e.g.
  // BV-BOK-01) so it can be passed straight to the store filter.
  value: string;
  label: string;
  orgId?: string; // entity_id, for narrowing the Store list to a chosen org
}

interface OrgOpt {
  id: string; // entity_id
  label: string;
}

const PAGE_SIZE = 100;

const ACTION_TONE: Record<string, string> = {
  CREATE: 'bg-green-100 text-green-700',
  UPDATE: 'bg-blue-100 text-blue-700',
  DELETE: 'bg-red-100 text-red-700',
  VIEW: 'bg-gray-100 text-gray-600',
  LOGIN: 'bg-green-100 text-green-700',
  LOGOUT: 'bg-gray-100 text-gray-600',
  EXPORT: 'bg-purple-100 text-purple-700',
  APPROVE: 'bg-green-100 text-green-700',
  REJECT: 'bg-red-100 text-red-700',
};

const ymd = (d: Date): string => d.toISOString().slice(0, 10);

function toneFor(action?: string): string {
  if (!action) return 'bg-gray-100 text-gray-600';
  const key = action.toUpperCase();
  for (const k of Object.keys(ACTION_TONE)) {
    if (key.includes(k)) return ACTION_TONE[k];
  }
  return 'bg-gray-100 text-gray-600';
}

function fmtTs(ts?: string): string {
  if (!ts) return '—';
  const d = new Date(ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString('en-IN', { hour12: true });
}

function rowKey(r: AuditRow, i: number): string {
  return r.log_id || r.id || `${r.user_id || ''}-${r.timestamp || ''}-${i}`;
}

export default function ActivityLogPage() {
  const toast = useToast();
  const { hasRole } = useAuth();
  const isSuperadmin = hasRole(['SUPERADMIN']);

  const [users, setUsers] = useState<UserOpt[]>([]);
  const [userId, setUserId] = useState('');
  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [storeId, setStoreId] = useState(''); // audit store_id (human store code)
  const [orgs, setOrgs] = useState<OrgOpt[]>([]);
  const [orgId, setOrgId] = useState(''); // entity_id
  const [action, setAction] = useState('');
  const [startDate, setStartDate] = useState(ymd(new Date(Date.now() - 7 * 864e5)));
  const [endDate, setEndDate] = useState(ymd(new Date()));

  const [rows, setRows] = useState<AuditRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  // Load the user roster once for the picker (resolve id -> friendly label).
  useEffect(() => {
    if (!isSuperadmin) return;
    let alive = true;
    (async () => {
      try {
        const data = await adminUserApi.getUsers();
        const list: any[] = Array.isArray(data) ? data : data?.users || [];
        if (!alive) return;
        const opts = list
          .map((u: any) => ({
            id: String(u.user_id || u.id || u._id || ''),
            label: String(u.full_name || u.name || u.username || u.user_id || u.id || ''),
            role: Array.isArray(u.roles) ? u.roles[0] : u.role,
          }))
          .filter((o: UserOpt) => o.id)
          .sort((a: UserOpt, b: UserOpt) => a.label.localeCompare(b.label));
        setUsers(opts);
      } catch {
        // Non-fatal: the picker just stays empty (operator can still filter by date/action).
      }
    })();
    return () => {
      alive = false;
    };
  }, [isSuperadmin]);

  // Load the store + organization (legal entity) lists once for the two new
  // filters. Same fail-soft pattern as the user roster: an error just leaves
  // the dropdown empty (operator can still filter by the other controls).
  useEffect(() => {
    if (!isSuperadmin) return;
    let alive = true;
    (async () => {
      try {
        const [storeRes, orgRes] = await Promise.all([
          orgStoreApi.list().catch(() => ({ stores: [], total: 0 })),
          entitiesApi.list(true).catch(() => ({ entities: [], total: 0 })),
        ]);
        if (!alive) return;
        const storeOpts: StoreOpt[] = (storeRes?.stores || [])
          .map((s) => ({
            // Audit rows record the human store code, so filter by store_code.
            value: String(s.store_code || s.store_id || ''),
            label: String(s.store_name || s.store_code || s.store_id || ''),
            orgId: s.entity_id ? String(s.entity_id) : undefined,
          }))
          .filter((o) => o.value)
          .sort((a, b) => a.label.localeCompare(b.label));
        setStores(storeOpts);
        const orgOpts: OrgOpt[] = (orgRes?.entities || [])
          .map((e) => ({
            id: String(e.entity_id || ''),
            label: String(e.name || e.legal_name || e.entity_id || ''),
          }))
          .filter((o) => o.id)
          .sort((a, b) => a.label.localeCompare(b.label));
        setOrgs(orgOpts);
      } catch {
        // Non-fatal: the store/org pickers stay empty.
      }
    })();
    return () => {
      alive = false;
    };
  }, [isSuperadmin]);

  const load = useCallback(
    async (nextOffset: number, append: boolean) => {
      setLoading(true);
      try {
        const res = await settingsApi.getAuditLogs({
          user_id: userId || undefined,
          store_id: storeId || undefined,
          org_id: orgId || undefined,
          action: action.trim() ? action.trim().toUpperCase() : undefined,
          start_date: startDate || undefined,
          end_date: endDate || undefined,
          limit: PAGE_SIZE,
          offset: nextOffset,
        });
        const newRows: AuditRow[] = res?.logs || [];
        setRows((prev) => (append ? [...prev, ...newRows] : newRows));
        setTotal(res?.total ?? newRows.length);
        setOffset(nextOffset);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Failed to load activity log');
      } finally {
        setLoading(false);
      }
    },
    [userId, storeId, orgId, action, startDate, endDate, toast]
  );

  // Initial load + reload whenever a filter changes (debounced for the action text).
  useEffect(() => {
    if (!isSuperadmin) return;
    const t = setTimeout(() => load(0, false), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, storeId, orgId, action, startDate, endDate, isSuperadmin]);

  const selectedUserLabel = useMemo(
    () => users.find((u) => u.id === userId)?.label,
    [users, userId]
  );

  const selectedOrgLabel = useMemo(
    () => orgs.find((o) => o.id === orgId)?.label,
    [orgs, orgId]
  );

  const selectedStoreLabel = useMemo(
    () => stores.find((s) => s.value === storeId)?.label,
    [stores, storeId]
  );

  // When an org is picked, narrow the Store dropdown to that org's stores. If
  // the currently selected store belongs to a different org, reset it so the
  // two filters never contradict each other.
  const visibleStores = useMemo(
    () => (orgId ? stores.filter((s) => s.orgId === orgId) : stores),
    [stores, orgId]
  );

  const onOrgChange = useCallback(
    (nextOrg: string) => {
      setOrgId(nextOrg);
      if (nextOrg && storeId) {
        const stillValid = stores.some(
          (s) => s.value === storeId && s.orgId === nextOrg
        );
        if (!stillValid) setStoreId('');
      }
    },
    [stores, storeId]
  );

  if (!isSuperadmin) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <ShieldCheck className="w-14 h-14 mx-auto text-gray-300 mb-3" />
          <h2 className="text-xl font-semibold text-gray-700">Superadmin only</h2>
          <p className="text-gray-500">The activity log is restricted to the Superadmin.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-full">
      {/* Header */}
      <div className="mb-5 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
            Audit Trail · Superadmin
          </div>
          <h1 className="text-2xl font-semibold text-gray-900 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5" /> User Activity Log
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Who did what, and when. Pick a user and a date range to see every recorded action.
          </p>
        </div>
        <Link
          to="/jarvis"
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          title="JARVIS reads the same audit trail — ask it to summarise a user's activity"
        >
          <Sparkles className="w-4 h-4" /> Ask JARVIS
        </Link>
      </div>

      {/* Filter bar */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label className="block text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
              User
            </label>
            <select
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              aria-label="Filter by user"
            >
              <option value="">All users</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.label}
                  {u.role ? ` · ${u.role}` : ''}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
              Organization
            </label>
            <select
              value={orgId}
              onChange={(e) => onOrgChange(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              aria-label="Filter by organization"
            >
              <option value="">All organizations</option>
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
              Store
            </label>
            <select
              value={storeId}
              onChange={(e) => setStoreId(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              aria-label="Filter by store"
            >
              <option value="">All stores</option>
              {visibleStores.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
              From
            </label>
            <input
              type="date"
              value={startDate}
              max={endDate || undefined}
              onChange={(e) => setStartDate(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              aria-label="Start date"
            />
          </div>
          <div>
            <label className="block text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
              To
            </label>
            <input
              type="date"
              value={endDate}
              min={startDate || undefined}
              onChange={(e) => setEndDate(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              aria-label="End date"
            />
          </div>
          <div>
            <label className="block text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
              Action (optional)
            </label>
            <input
              type="text"
              value={action}
              onChange={(e) => setAction(e.target.value)}
              placeholder="e.g. UPDATE, LOGIN"
              className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              aria-label="Filter by action"
            />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {(['Today', '7 days', '30 days'] as const).map((label, i) => {
            const days = [0, 7, 30][i];
            return (
              <button
                key={label}
                type="button"
                onClick={() => {
                  setStartDate(ymd(new Date(Date.now() - days * 864e5)));
                  setEndDate(ymd(new Date()));
                }}
                className="rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-600 hover:bg-gray-50"
              >
                {label}
              </button>
            );
          })}
          <div className="flex-1" />
          <button
            type="button"
            onClick={() => load(0, false)}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Refresh
          </button>
        </div>
      </div>

      {/* Summary line */}
      <div className="mb-3 text-sm text-gray-500">
        {loading && rows.length === 0 ? (
          'Loading…'
        ) : (
          <>
            <span className="font-medium text-gray-900">{total}</span> action
            {total === 1 ? '' : 's'}
            {selectedUserLabel ? (
              <>
                {' '}
                by <span className="font-medium text-gray-900">{selectedUserLabel}</span>
              </>
            ) : (
              ' across all users'
            )}
            {selectedStoreLabel ? (
              <>
                {' '}
                at <span className="font-medium text-gray-900">{selectedStoreLabel}</span>
              </>
            ) : selectedOrgLabel ? (
              <>
                {' '}
                in <span className="font-medium text-gray-900">{selectedOrgLabel}</span>
              </>
            ) : null}
            {startDate && endDate ? ` · ${startDate} → ${endDate}` : ''}
          </>
        )}
      </div>

      {/* Results */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        {rows.length === 0 && !loading ? (
          <div className="px-6 py-12 text-center">
            <AlertCircle className="w-10 h-10 mx-auto text-gray-300 mb-2" />
            <p className="text-sm text-gray-500">
              No recorded activity for this filter. Widen the date range or clear the user.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[10.5px] font-mono uppercase tracking-wider text-gray-400 bg-gray-50 border-b border-gray-200">
                <th className="text-left px-3 py-2.5">When</th>
                <th className="text-left px-3 py-2.5">User</th>
                <th className="text-left px-3 py-2.5">Action</th>
                <th className="text-left px-3 py-2.5">On</th>
                <th className="text-left px-3 py-2.5">Store</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const key = rowKey(r, i);
                const isOpen = expanded === key;
                const who = r.username || r.user_name || r.user_id || '—';
                const onWhat =
                  r.entity_name || r.entity_id
                    ? `${r.entity_type || ''}${
                        r.entity_name ? `: ${r.entity_name}` : r.entity_id ? `: ${r.entity_id}` : ''
                      }`
                    : r.entity_type || '—';
                const hasDetail =
                  (r.changes && r.changes.length > 0) || !!r.description || !!r.details;
                return (
                  <Fragment key={key}>
                    <tr
                      className={`border-b border-gray-100 last:border-0 ${
                        hasDetail ? 'cursor-pointer hover:bg-gray-50' : ''
                      }`}
                      onClick={() => hasDetail && setExpanded(isOpen ? null : key)}
                    >
                      <td className="px-3 py-2.5 whitespace-nowrap text-gray-600 tabular-nums">
                        {fmtTs(r.timestamp)}
                      </td>
                      <td className="px-3 py-2.5 font-medium text-gray-900">{who}</td>
                      <td className="px-3 py-2.5">
                        <span
                          className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-semibold ${toneFor(
                            r.action
                          )}`}
                        >
                          {r.action || '—'}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-gray-700">{onWhat}</td>
                      <td className="px-3 py-2.5 text-gray-500">{r.store_name || r.store_id || '—'}</td>
                      <td className="px-3 py-2.5 text-gray-400">
                        {hasDetail && (
                          <ChevronDown
                            className={`w-4 h-4 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                          />
                        )}
                      </td>
                    </tr>
                    {isOpen && hasDetail && (
                      <tr key={`${key}-detail`} className="bg-gray-50 border-b border-gray-100">
                        <td colSpan={6} className="px-4 py-3">
                          <div className="grid grid-cols-2 gap-3 mb-2 text-xs">
                            <div>
                              <span className="text-gray-500">User ID:</span>{' '}
                              <span className="font-mono text-gray-800">{r.user_id || '—'}</span>
                            </div>
                            {r.ip_address && (
                              <div>
                                <span className="text-gray-500">IP:</span>{' '}
                                <span className="font-mono text-gray-800">{r.ip_address}</span>
                              </div>
                            )}
                            <div>
                              <span className="text-gray-500">Timestamp:</span>{' '}
                              <span className="font-mono text-gray-800">{r.timestamp || '—'}</span>
                            </div>
                            {r.severity && (
                              <div>
                                <span className="text-gray-500">Severity:</span>{' '}
                                <span className="font-mono text-gray-800">{r.severity}</span>
                              </div>
                            )}
                          </div>
                          {r.changes && r.changes.length > 0 && (
                            <div className="rounded border border-gray-200 bg-white p-2 space-y-1.5">
                              {r.changes.map((c, ci) => (
                                <div key={ci} className="text-xs">
                                  <span className="font-mono text-gray-700">{c.field}</span>
                                  <span className="ml-2 text-red-600 line-through">
                                    {String(c.old_value ?? c.oldValue ?? '')}
                                  </span>
                                  <span className="mx-1 text-gray-400">→</span>
                                  <span className="text-green-600">
                                    {String(c.new_value ?? c.newValue ?? '')}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}
                          {(r.description || r.details != null) && (
                            <pre className="mt-2 rounded border border-gray-200 bg-white p-2 text-xs text-gray-700 whitespace-pre-wrap break-words">
                              {r.description || JSON.stringify(r.details, null, 2)}
                            </pre>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {/* Load more */}
      {rows.length < total && (
        <div className="mt-4 text-center">
          <button
            type="button"
            onClick={() => load(offset + PAGE_SIZE, true)}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
            Load more ({total - rows.length} older)
          </button>
        </div>
      )}
    </div>
  );
}
