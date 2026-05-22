// ============================================================================
// IMS 2.0 - Notification Bell (topbar)
// ============================================================================
// Server-backed bell: polls the unread count, opens a dropdown of the user's
// notifications, marks them read, and deep-links to the related entity.
// Replaces the old static (decorative) bell button.

import { useState, useRef, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from './Icon';
import { notificationsApi, type AppNotification } from '../../services/api/notifications';

function relativeTime(iso?: string): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const diff = Date.now() - t;
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

const UNREAD = new Set(['PENDING', 'SENT', 'DELIVERED']);

export function NotificationBell() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const refreshCount = useCallback(async () => {
    try {
      const res = await notificationsApi.unreadCount();
      setUnread(res?.unread_count ?? 0);
    } catch {
      /* offline / unauthed — leave count as-is */
    }
  }, []);

  // Poll the unread count on mount + every 60s.
  useEffect(() => {
    refreshCount();
    const id = setInterval(refreshCount, 60000);
    return () => clearInterval(id);
  }, [refreshCount]);

  // Close on outside click.
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const res = await notificationsApi.list({ limit: 20 });
      setItems(res?.notifications || []);
      setUnread(res?.unread_count ?? 0);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) loadList();
  };

  const onItemClick = async (n: AppNotification) => {
    if (UNREAD.has(n.status)) {
      try {
        await notificationsApi.markRead(n.notification_id);
      } catch {
        /* non-fatal */
      }
    }
    setOpen(false);
    refreshCount();
    if (n.action_url) navigate(n.action_url);
  };

  const onMarkAll = async () => {
    try {
      await notificationsApi.markAllRead();
    } catch {
      /* non-fatal */
    }
    loadList();
  };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        className="btn icon ghost"
        type="button"
        title="Notifications"
        aria-label="Notifications"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={toggle}
        style={{ position: 'relative' }}
      >
        <Icon.bell />
        {unread > 0 && (
          <span
            aria-label={`${unread} unread`}
            style={{
              position: 'absolute',
              top: 2,
              right: 2,
              minWidth: 15,
              height: 15,
              padding: '0 3px',
              borderRadius: 9,
              background: 'var(--bv, #DC2626)',
              color: '#fff',
              fontSize: 9.5,
              fontWeight: 700,
              lineHeight: '15px',
              textAlign: 'center',
            }}
          >
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: 6,
            width: 320,
            background: 'var(--surface)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-md)',
            boxShadow: 'var(--sh-md)',
            zIndex: 60,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '10px 12px',
              borderBottom: '1px solid var(--line)',
            }}
          >
            <strong style={{ font: '600 13px var(--font-sans)', color: 'var(--ink-1)' }}>
              Notifications{unread > 0 ? ` (${unread})` : ''}
            </strong>
            {items.length > 0 && (
              <button
                type="button"
                onClick={onMarkAll}
                style={{
                  background: 'transparent',
                  border: 0,
                  color: 'var(--bv)',
                  font: '500 11.5px var(--font-sans)',
                  cursor: 'pointer',
                }}
              >
                Mark all read
              </button>
            )}
          </div>

          <div style={{ maxHeight: 360, overflowY: 'auto' }}>
            {loading ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--ink-4)', fontSize: 12 }}>
                Loading…
              </div>
            ) : items.length === 0 ? (
              <div style={{ padding: 24, textAlign: 'center', color: 'var(--ink-4)', fontSize: 12 }}>
                No notifications
              </div>
            ) : (
              items.map((n) => {
                const isUnread = UNREAD.has(n.status);
                return (
                  <button
                    key={n.notification_id}
                    type="button"
                    onClick={() => onItemClick(n)}
                    style={{
                      width: '100%',
                      textAlign: 'left',
                      padding: '10px 12px',
                      border: 0,
                      borderBottom: '1px solid var(--line)',
                      borderLeft: isUnread ? '3px solid var(--bv)' : '3px solid transparent',
                      background: isUnread ? 'var(--bv-50, #FEF2F2)' : 'transparent',
                      cursor: 'pointer',
                      display: 'block',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        gap: 8,
                        marginBottom: 2,
                      }}
                    >
                      <span style={{ font: '600 12.5px var(--font-sans)', color: 'var(--ink-1)' }}>
                        {n.title}
                      </span>
                      <span style={{ color: 'var(--ink-4)', fontSize: 10.5, flexShrink: 0 }}>
                        {relativeTime(n.created_at)}
                      </span>
                    </div>
                    {n.message && (
                      <div style={{ color: 'var(--ink-3)', fontSize: 11.5, lineHeight: 1.4 }}>
                        {n.message}
                      </div>
                    )}
                  </button>
                );
              })
            )}
          </div>
          <button
            type="button"
            onClick={() => { setOpen(false); navigate('/notifications'); }}
            style={{
              width: '100%',
              padding: '10px 12px',
              border: 0,
              borderTop: '1px solid var(--line)',
              background: 'transparent',
              cursor: 'pointer',
              font: '600 12px var(--font-sans)',
              color: 'var(--bv, #DC2626)',
              textAlign: 'center',
            }}
          >
            View all notifications
          </button>
        </div>
      )}
    </div>
  );
}
