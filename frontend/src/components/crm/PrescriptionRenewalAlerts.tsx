// ============================================================================
// IMS 2.0 - Prescription Renewal Alerts
// ============================================================================
// Automated prescription renewal reminders with smart notifications

import { Calendar, AlertTriangle, Clock, Send, Eye } from 'lucide-react';
import clsx from 'clsx';

export interface PrescriptionReminder {
  customerId: string;
  customerName: string;
  customerEmail?: string;
  customerPhone?: string;
  prescriptionId: string;
  testDate: string;
  daysUntilRenewal: number;
  renewalStatus: 'current' | 'upcoming' | 'expired';
  lastReminderSent?: string;
  reminderFrequency: 'daily' | 'weekly' | 'none';
  preferredContactMethod: 'email' | 'sms' | 'whatsapp' | 'call';
}

interface PrescriptionRenewalAlertsProps {
  reminders: PrescriptionReminder[];
  onSendReminder?: (reminderId: string) => Promise<void>;
  onUpdateFrequency?: (reminderId: string, frequency: 'daily' | 'weekly' | 'none') => Promise<void>;
}

export function PrescriptionRenewalAlerts({
  reminders,
  onSendReminder,
  onUpdateFrequency,
}: PrescriptionRenewalAlertsProps) {
  const upcomingReminders = reminders.filter(r => r.daysUntilRenewal <= 30 && r.daysUntilRenewal >= -30);
  const expiredReminders = reminders.filter(r => r.daysUntilRenewal < -30);

  return (
    <div className="space-y-6">
      {/* Summary Statistics */}
      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-red-700 uppercase tracking-wider">Expired</p>
          <p className="text-3xl font-bold text-red-900 mt-2">{expiredReminders.length}</p>
          <p className="text-xs text-red-600 mt-1">Prescriptions overdue for renewal</p>
        </div>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-yellow-700 uppercase tracking-wider">Due Soon</p>
          <p className="text-3xl font-bold text-yellow-900 mt-2">
            {upcomingReminders.filter(r => r.daysUntilRenewal >= 0).length}
          </p>
          <p className="text-xs text-yellow-600 mt-1">Prescriptions due within 30 days</p>
        </div>
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-blue-700 uppercase tracking-wider">Total Tracked</p>
          <p className="text-3xl font-bold text-blue-900 mt-2">{reminders.length}</p>
          <p className="text-xs text-blue-600 mt-1">Active prescription reminders</p>
        </div>
      </div>

      {/* Expired Prescriptions */}
      {expiredReminders.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-red-600" />
            Expired Prescriptions ({expiredReminders.length})
          </h3>
          <div className="space-y-2">
            {expiredReminders.map(reminder => (
              <ReminderCard
                key={reminder.prescriptionId}
                reminder={reminder}
                severity="critical"
                onSendReminder={onSendReminder}
                onUpdateFrequency={onUpdateFrequency}
              />
            ))}
          </div>
        </div>
      )}

      {/* Upcoming Renewals */}
      {upcomingReminders.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
            <Clock className="w-5 h-5 text-yellow-600" />
            Upcoming Renewals ({upcomingReminders.filter(r => r.daysUntilRenewal >= 0).length})
          </h3>
          <div className="space-y-2">
            {upcomingReminders
              .filter(r => r.daysUntilRenewal >= 0)
              .map(reminder => (
                <ReminderCard
                  key={reminder.prescriptionId}
                  reminder={reminder}
                  severity="warning"
                  onSendReminder={onSendReminder}
                  onUpdateFrequency={onUpdateFrequency}
                />
              ))}
          </div>
        </div>
      )}

      {reminders.length === 0 && (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-8 text-center">
          <Eye className="w-12 h-12 text-gray-400 mx-auto mb-4" />
          <p className="text-gray-600 font-medium">No prescription reminders to track</p>
          <p className="text-sm text-gray-500 mt-1">Prescriptions will appear here for renewal tracking</p>
        </div>
      )}
    </div>
  );
}

interface ReminderCardProps {
  reminder: PrescriptionReminder;
  severity: 'critical' | 'warning' | 'info';
  onSendReminder?: (reminderId: string) => Promise<void>;
  onUpdateFrequency?: (reminderId: string, frequency: 'daily' | 'weekly' | 'none') => Promise<void>;
}

function ReminderCard({ reminder, severity, onSendReminder, onUpdateFrequency }: ReminderCardProps) {
  const getSeverityIcon = (sev: 'critical' | 'warning' | 'info') => {
    switch (sev) {
      case 'critical':
        return <AlertTriangle className="w-5 h-5 text-red-600" />;
      case 'warning':
        return <Clock className="w-5 h-5 text-yellow-600" />;
      case 'info':
        return <Eye className="w-5 h-5 text-blue-600" />;
    }
  };

  const getSeverityBadgeColor = (sev: 'critical' | 'warning' | 'info'): string => {
    switch (sev) {
      case 'critical':
        return 'bg-red-100 text-red-800';
      case 'warning':
        return 'bg-yellow-100 text-yellow-800';
      case 'info':
        return 'bg-blue-100 text-blue-800';
    }
  };

  const getSeverityBg = (sev: 'critical' | 'warning' | 'info'): string => {
    switch (sev) {
      case 'critical':
        return 'bg-red-50 border-red-200';
      case 'warning':
        return 'bg-yellow-50 border-yellow-200';
      case 'info':
        return 'bg-blue-50 border-blue-200';
    }
  };

  const formatDate = (dateString: string): string => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-IN', { year: 'numeric', month: 'short', day: 'numeric' });
  };

  const formatDaysUntilRenewal = (days: number): string => {
    if (days < 0) {
      return `Expired ${Math.abs(days)} days ago`;
    }
    if (days === 0) {
      return 'Renew today';
    }
    return `Renew in ${days} days`;
  };

  return (
    <div className={clsx('border rounded-lg p-4 space-y-3', getSeverityBg(severity))}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3 flex-1">
          <div className="flex-shrink-0 mt-1">{getSeverityIcon(severity)}</div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <p className="font-semibold text-gray-900">{reminder.customerName}</p>
              <span className={clsx('px-2 py-1 rounded text-xs font-medium', getSeverityBadgeColor(severity))}>
                {formatDaysUntilRenewal(reminder.daysUntilRenewal)}
              </span>
            </div>
            <p className="text-sm text-gray-600 mb-2 flex items-center gap-2">
              <Calendar className="w-4 h-4" />
              Test Date: {formatDate(reminder.testDate)}
            </p>

            {reminder.customerEmail && (
              <p className="text-xs text-gray-500">Email: {reminder.customerEmail}</p>
            )}
            {reminder.customerPhone && (
              <p className="text-xs text-gray-500">Phone: {reminder.customerPhone}</p>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex flex-col gap-2">
          <button
            onClick={() => onSendReminder?.(reminder.prescriptionId)}
            className="px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg flex items-center gap-2 transition-colors"
          >
            <Send className="w-4 h-4" />
            Send
          </button>
          <select
            value={reminder.reminderFrequency}
            onChange={(e) =>
              onUpdateFrequency?.(reminder.prescriptionId, e.target.value as 'daily' | 'weekly' | 'none')
            }
            className="px-2 py-1 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="none">None</option>
          </select>
        </div>
      </div>

      {reminder.lastReminderSent && (
        <div className="text-xs text-gray-600 pt-2 border-t border-current border-opacity-20">
          Last sent: {formatDate(reminder.lastReminderSent)}
        </div>
      )}
    </div>
  );
}

export default PrescriptionRenewalAlerts;
