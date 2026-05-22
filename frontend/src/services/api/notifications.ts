// ============================================================================
// IMS 2.0 - In-app notifications (staff bell)
// ============================================================================
// Backed by GET/PATCH /api/v1/notifications. Written by the task escalation
// engine; read by the topbar NotificationBell.

import api from './client';

export interface AppNotification {
  notification_id: string;
  notification_type: string;
  user_id: string;
  title: string;
  message?: string;
  entity_type?: string;
  entity_id?: string;
  action_url?: string;
  priority?: string;
  status: string;
  created_at?: string;
  read_at?: string;
  snoozed_until?: string | null;
  snooze_count?: number;
}

export const MAX_SNOOZE = 3;

export interface NotificationListResponse {
  notifications: AppNotification[];
  unread_count: number;
  total: number;
}

export const notificationsApi = {
  list: async (opts?: { unreadOnly?: boolean; includeSnoozed?: boolean; limit?: number }) => {
    const params: Record<string, string | number | boolean> = {};
    if (opts?.unreadOnly !== undefined) params.unread_only = opts.unreadOnly;
    if (opts?.includeSnoozed !== undefined) params.include_snoozed = opts.includeSnoozed;
    if (opts?.limit) params.limit = opts.limit;
    const res = await api.get('/notifications', { params });
    return res.data as NotificationListResponse;
  },

  /** Snooze a notification until `until` (ISO datetime). Max 3 snoozes. */
  snooze: async (id: string, until: string) => {
    const res = await api.post(`/notifications/${id}/snooze`, null, { params: { until } });
    return res.data as {
      notification_id: string;
      snoozed_until: string;
      snooze_count: number;
      snoozes_remaining: number;
    };
  },

  unreadCount: async () => {
    const res = await api.get('/notifications/unread-count');
    return res.data as { unread_count: number };
  },

  markRead: async (id: string) => {
    const res = await api.patch(`/notifications/${id}/read`);
    return res.data;
  },

  markAllRead: async () => {
    const res = await api.post('/notifications/mark-all-read');
    return res.data;
  },
};
