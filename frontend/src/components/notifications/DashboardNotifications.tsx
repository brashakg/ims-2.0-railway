// ============================================================================
// IMS 2.0 - Dashboard notifications widget
// ============================================================================
// Small "what needs you" panel shown on the Hub right after login. Lists the
// most recent unread notifications and links to the full Notifications screen.

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Bell, ChevronRight } from 'lucide-react';
import { notificationsApi, type AppNotification } from '../../services/api/notifications';
import { formatDateTimeIST } from '../../utils/datetime';
import clsx from 'clsx';

const PRIORITY_DOT: Record<string, string> = {
  P0: 'bg-red-500', P1: 'bg-orange-500', URGENT: 'bg-red-500', HIGH: 'bg-orange-500',
  P2: 'bg-yellow-500', NORMAL: 'bg-blue-500', P3: 'bg-gray-400', LOW: 'bg-gray-400',
};

export function DashboardNotifications() {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    notificationsApi
      .list({ unreadOnly: true, limit: 5 })
      .then((r) => {
        if (!alive) return;
        setItems(r.notifications || []);
        setUnread(r.unread_count || 0);
      })
      .catch(() => { /* fail-soft: widget just stays empty */ })
      .finally(() => alive && setLoaded(true));
    return () => { alive = false; };
  }, []);

  // Don't take up dashboard space until we know there's something (or it loaded empty).
  if (!loaded) return null;

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <Bell className="w-4 h-4" /> Notifications
          {unread > 0 && (
            <span className="text-xs font-medium bg-bv-red-100 text-bv-red-700 rounded-full px-2 py-0.5">{unread}</span>
          )}
        </h3>
        <Link to="/notifications" className="text-xs text-bv-red-600 hover:underline inline-flex items-center gap-0.5">
          View all <ChevronRight className="w-3 h-3" />
        </Link>
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-gray-400 py-2">You're all caught up.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {items.map((n) => (
            <li key={n.notification_id} className="py-2 flex items-start gap-2">
              <span className={clsx('mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0',
                PRIORITY_DOT[(n.priority || 'NORMAL').toUpperCase()] || 'bg-blue-500')} />
              <div className="min-w-0 flex-1">
                <p className="text-sm text-gray-800 truncate">{n.title}</p>
                <p className="text-xs text-gray-400">{formatDateTimeIST(n.created_at)}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default DashboardNotifications;
