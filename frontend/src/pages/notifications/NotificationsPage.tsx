// ============================================================================
// IMS 2.0 - Notifications (full screen)
// ============================================================================
// Lists the current user's in-app notifications with read + snooze controls.
// Snooze: up to 3 times per notification, each to a custom time.

import { useCallback, useEffect, useState } from 'react';
import { Bell, Check, CheckCheck, Clock, Loader2, AlarmClock } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { notificationsApi, MAX_SNOOZE, type AppNotification } from '../../services/api/notifications';
import { formatDateTimeIST, toDate } from '../../utils/datetime';
import clsx from 'clsx';

type Filter = 'all' | 'unread';

const UNREAD = new Set(['PENDING', 'SENT', 'DELIVERED']);
const isUnread = (n: AppNotification) => UNREAD.has((n.status || '').toUpperCase());
const isSnoozed = (n: AppNotification) => {
  const d = toDate(n.snoozed_until);
  return !!d && d.getTime() > Date.now();
};

const PRIORITY_DOT: Record<string, string> = {
  P0: 'bg-red-500', P1: 'bg-orange-500', URGENT: 'bg-red-500', HIGH: 'bg-orange-500',
  P2: 'bg-yellow-500', NORMAL: 'bg-blue-500', P3: 'bg-gray-400', LOW: 'bg-gray-400',
};

// Quick snooze presets -> absolute Date.
function presets(): { label: string; date: Date }[] {
  const h = (n: number) => new Date(Date.now() + n * 3600_000);
  const tmr9 = new Date(); tmr9.setDate(tmr9.getDate() + 1); tmr9.setHours(9, 0, 0, 0);
  const nextWeek = new Date(); nextWeek.setDate(nextWeek.getDate() + 7); nextWeek.setHours(9, 0, 0, 0);
  return [
    { label: '1 hour', date: h(1) },
    { label: '3 hours', date: h(3) },
    { label: 'Tomorrow 9 AM', date: tmr9 },
    { label: 'Next week', date: nextWeek },
  ];
}

export function NotificationsPage() {
  const toast = useToast();
  const [items, setItems] = useState<AppNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>('all');
  const [snoozeFor, setSnoozeFor] = useState<string | null>(null);
  const [customTime, setCustomTime] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // include_snoozed so the full screen shows snoozed items (labelled).
      const res = await notificationsApi.list({ includeSnoozed: true, limit: 100 });
      setItems(res.notifications || []);
    } catch {
      toast.error('Failed to load notifications');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const markRead = async (id: string) => {
    try { await notificationsApi.markRead(id); await load(); }
    catch { toast.error('Failed to mark read'); }
  };

  const markAllRead = async () => {
    try { const r = await notificationsApi.markAllRead(); toast.success(`Marked ${r.updated ?? 0} read`); await load(); }
    catch { toast.error('Failed to mark all read'); }
  };

  const doSnooze = async (n: AppNotification, date: Date) => {
    if ((n.snooze_count || 0) >= MAX_SNOOZE) { toast.error('Max snoozes reached'); return; }
    if (date.getTime() <= Date.now()) { toast.error('Pick a future time'); return; }
    try {
      const r = await notificationsApi.snooze(n.notification_id, date.toISOString());
      toast.success(`Snoozed · ${r.snoozes_remaining} left`);
      setSnoozeFor(null); setCustomTime('');
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Failed to snooze');
    }
  };

  const filtered = items.filter((n) => (filter === 'unread' ? isUnread(n) : true));
  const unreadCount = items.filter(isUnread).length;

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Bell className="w-5 h-5" /> Notifications
          </h1>
          <p className="text-sm text-gray-500">{unreadCount} unread · snooze up to {MAX_SNOOZE}× each</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-gray-200 overflow-hidden">
            {(['all', 'unread'] as Filter[]).map((f) => (
              <button key={f} onClick={() => setFilter(f)}
                className={clsx('px-3 py-1.5 text-sm', filter === f ? 'bg-bv-red-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50')}>
                {f === 'all' ? 'All' : 'Unread'}
              </button>
            ))}
          </div>
          <button onClick={markAllRead} className="btn-secondary text-sm inline-flex items-center gap-1">
            <CheckCheck className="w-4 h-4" /> Mark all read
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16"><Loader2 className="w-7 h-7 animate-spin text-bv-red-600" /></div>
      ) : filtered.length === 0 ? (
        <div className="card p-12 text-center text-gray-500">
          <Bell className="w-10 h-10 mx-auto mb-2 opacity-40" />
          <p>{filter === 'unread' ? 'No unread notifications' : 'No notifications yet'}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((n) => {
            const unread = isUnread(n);
            const snoozed = isSnoozed(n);
            const snoozeCount = n.snooze_count || 0;
            const canSnooze = snoozeCount < MAX_SNOOZE && unread;
            const dot = PRIORITY_DOT[(n.priority || 'NORMAL').toUpperCase()] || 'bg-blue-500';
            return (
              <div key={n.notification_id}
                className={clsx('card p-4 border-l-4', unread ? 'border-l-bv-red-500' : 'border-l-transparent')}>
                <div className="flex items-start gap-3">
                  <span className={clsx('mt-1.5 w-2 h-2 rounded-full flex-shrink-0', dot)} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className={clsx('text-sm', unread ? 'font-semibold text-gray-900' : 'text-gray-700')}>{n.title}</p>
                      {snoozed && (
                        <span className="inline-flex items-center gap-1 text-xs text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full">
                          <AlarmClock className="w-3 h-3" /> Snoozed → {formatDateTimeIST(n.snoozed_until)}
                        </span>
                      )}
                    </div>
                    {n.message && <p className="text-sm text-gray-600 mt-0.5">{n.message}</p>}
                    <p className="text-xs text-gray-400 mt-1">
                      {formatDateTimeIST(n.created_at)}
                      {snoozeCount > 0 && ` · snoozed ${snoozeCount}/${MAX_SNOOZE}`}
                    </p>

                    {snoozeFor === n.notification_id && (
                      <div className="mt-3 p-3 bg-gray-50 rounded-lg border border-gray-200 space-y-2">
                        <div className="flex flex-wrap gap-2">
                          {presets().map((p) => (
                            <button key={p.label} onClick={() => doSnooze(n, p.date)}
                              className="px-2.5 py-1 text-xs rounded-md bg-white border border-gray-200 hover:border-bv-red-400">
                              {p.label}
                            </button>
                          ))}
                        </div>
                        <div className="flex items-center gap-2">
                          <input type="datetime-local" value={customTime} onChange={(e) => setCustomTime(e.target.value)}
                            className="input-field text-sm py-1.5 flex-1" />
                          <button
                            onClick={() => customTime && doSnooze(n, new Date(customTime))}
                            disabled={!customTime}
                            className="px-3 py-1.5 text-xs rounded-md bg-bv-red-600 text-white disabled:opacity-50">
                            Snooze
                          </button>
                          <button onClick={() => { setSnoozeFor(null); setCustomTime(''); }}
                            className="px-3 py-1.5 text-xs rounded-md text-gray-600 hover:bg-gray-100">Cancel</button>
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-1 flex-shrink-0">
                    {canSnooze && snoozeFor !== n.notification_id && (
                      <button onClick={() => { setSnoozeFor(n.notification_id); setCustomTime(''); }}
                        title="Snooze" className="p-2 text-gray-400 hover:text-amber-600">
                        <Clock className="w-4 h-4" />
                      </button>
                    )}
                    {unread && (
                      <button onClick={() => markRead(n.notification_id)}
                        title="Mark read" className="p-2 text-gray-400 hover:text-green-600">
                        <Check className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default NotificationsPage;
