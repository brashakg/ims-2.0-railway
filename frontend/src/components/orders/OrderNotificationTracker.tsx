// ============================================================================
// IMS 2.0 - Order Notification Tracker
// ============================================================================
// Order status timeline with notification tracking for Indian optical retail

import { useState } from 'react';
import {
  Check,
  Clock,
  Truck,
  Package,
  MessageSquare,
  Phone,
  X,
  ChevronDown,
  ChevronUp,
  Send,
} from 'lucide-react';
import {
  NOTIFICATION_TEMPLATES,
  populateTemplate,
} from '../../constants/notifications';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type OrderStatus =
  | 'DRAFT'
  | 'CONFIRMED'
  | 'IN_PROGRESS'
  | 'READY'
  | 'DELIVERED'
  | 'CANCELLED';

interface StatusHistoryEntry {
  status: string;
  timestamp: string;
  note?: string;
  notifiedVia?: 'SMS' | 'WHATSAPP' | 'BOTH' | null;
}

interface OrderNotificationTrackerProps {
  orderId: string;
  orderNumber: string;
  customerName: string;
  customerPhone: string;
  status: OrderStatus;
  createdAt: string;
  statusHistory?: StatusHistoryEntry[];
  onSendNotification?: (status: string, channel: 'SMS' | 'WHATSAPP') => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** The ordered progression of non-cancelled statuses. */
const STATUS_STEPS: OrderStatus[] = [
  'CONFIRMED',
  'IN_PROGRESS',
  'READY',
  'DELIVERED',
];

/** Human-readable labels for each status. */
const STATUS_LABELS: Record<OrderStatus, string> = {
  DRAFT: 'Draft',
  CONFIRMED: 'Order Confirmed',
  IN_PROGRESS: 'In Progress',
  READY: 'Ready for Pickup',
  DELIVERED: 'Delivered',
  CANCELLED: 'Cancelled',
};

/** Map each status to the corresponding notification template key. */
const STATUS_TEMPLATE_MAP: Record<string, string> = {
  CONFIRMED: 'ORDER_CONFIRMED',
  READY: 'ORDER_READY',
  DELIVERED: 'ORDER_DELIVERED',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getStatusIcon(status: OrderStatus, size = 'w-4 h-4') {
  switch (status) {
    case 'CONFIRMED':
      return <Check className={size} />;
    case 'IN_PROGRESS':
      return <Clock className={size} />;
    case 'READY':
      return <Package className={size} />;
    case 'DELIVERED':
      return <Truck className={size} />;
    case 'CANCELLED':
      return <X className={size} />;
    default:
      return <Clock className={size} />;
  }
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });
}

type StepState = 'completed' | 'current' | 'pending' | 'cancelled';

function getStepState(
  stepStatus: OrderStatus,
  currentStatus: OrderStatus,
): StepState {
  if (currentStatus === 'CANCELLED') {
    // When cancelled, every step that already happened is completed; the rest are pending.
    // The CANCELLED badge is rendered separately.
    const currentIdx = STATUS_STEPS.indexOf(currentStatus);
    const stepIdx = STATUS_STEPS.indexOf(stepStatus);
    return stepIdx < currentIdx ? 'completed' : 'pending';
  }

  const currentIdx = STATUS_STEPS.indexOf(currentStatus);
  const stepIdx = STATUS_STEPS.indexOf(stepStatus);

  if (stepIdx < currentIdx) return 'completed';
  if (stepIdx === currentIdx) return 'current';
  return 'pending';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function NotificationBadge({ via }: { via: 'SMS' | 'WHATSAPP' | 'BOTH' }) {
  if (via === 'SMS') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-50 border border-green-200 rounded-full px-2 py-0.5">
        <Phone className="w-3 h-3" />
        SMS sent
      </span>
    );
  }
  if (via === 'WHATSAPP') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-50 border border-green-200 rounded-full px-2 py-0.5">
        <MessageSquare className="w-3 h-3" />
        WhatsApp sent
      </span>
    );
  }
  // BOTH
  return (
    <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-50 border border-green-200 rounded-full px-2 py-0.5">
      <Phone className="w-3 h-3" />
      <MessageSquare className="w-3 h-3" />
      Both sent
    </span>
  );
}

interface MessagePreviewProps {
  status: string;
  customerName: string;
  orderNumber: string;
}

function MessagePreview({ status, customerName, orderNumber }: MessagePreviewProps) {
  const templateKey = STATUS_TEMPLATE_MAP[status];
  if (!templateKey) return null;

  const template = NOTIFICATION_TEMPLATES[templateKey];
  if (!template) return null;

  const preview = populateTemplate(template.template, {
    customerName,
    orderNumber,
    storeName: '{storeName}',
    storeAddress: '{storeAddress}',
    amount: '{amount}',
    deliveryDate: '{deliveryDate}',
    trackingLink: '{trackingLink}',
  });

  return (
    <div className="mt-2 bg-gray-50 border border-gray-200 rounded-lg p-3">
      <p className="text-xs font-medium text-gray-500 mb-1">Message Preview</p>
      <p className="text-xs text-gray-700 leading-relaxed">{preview}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function OrderNotificationTracker({
  orderNumber,
  customerName,
  customerPhone,
  status,
  createdAt,
  statusHistory = [],
  onSendNotification,
}: OrderNotificationTrackerProps) {
  const [expandedStep, setExpandedStep] = useState<string | null>(null);

  /** Find the history entry that matches a given status. */
  function findHistoryEntry(stepStatus: string): StatusHistoryEntry | undefined {
    return statusHistory.find((h) => h.status === stepStatus);
  }

  /** Toggle expanded step (for notification preview). */
  function toggleExpand(stepStatus: string) {
    setExpandedStep((prev) => (prev === stepStatus ? null : stepStatus));
  }

  const isCancelled = status === 'CANCELLED';

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <h3 className="font-medium text-gray-900 text-sm">Order Status</h3>
        <span className="text-xs text-gray-500">#{orderNumber}</span>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        {customerName} &middot; {customerPhone}
      </p>

      {/* Timeline */}
      <div className="relative">
        {STATUS_STEPS.map((stepStatus, idx) => {
          const state = getStepState(stepStatus, status);
          const history = findHistoryEntry(stepStatus);
          const isLast = idx === STATUS_STEPS.length - 1;
          const isExpanded = expandedStep === stepStatus;
          const hasTemplate = !!STATUS_TEMPLATE_MAP[stepStatus];
          const showNotifyButtons =
            state === 'current' && hasTemplate && !!onSendNotification;

          // Dot / icon colours
          let dotClasses: string;
          let lineClasses: string;
          let labelClasses: string;

          switch (state) {
            case 'completed':
              dotClasses =
                'bg-green-500 text-white border-green-500';
              lineClasses = 'bg-green-400';
              labelClasses = 'text-gray-900';
              break;
            case 'current':
              dotClasses =
                'bg-blue-500 text-white border-blue-500 ring-4 ring-blue-100';
              lineClasses = 'bg-gray-200';
              labelClasses = 'text-blue-700 font-semibold';
              break;
            default:
              dotClasses =
                'bg-white text-gray-400 border-gray-300';
              lineClasses = 'bg-gray-200';
              labelClasses = 'text-gray-400';
              break;
          }

          return (
            <div key={stepStatus} className="relative flex gap-3">
              {/* Vertical line */}
              {!isLast && (
                <div
                  className={`absolute left-[13px] top-7 w-0.5 ${lineClasses}`}
                  style={{ bottom: '-4px' }}
                />
              )}

              {/* Dot */}
              <div
                className={`relative z-10 flex-shrink-0 w-7 h-7 rounded-full border-2 flex items-center justify-center ${dotClasses}`}
              >
                {getStatusIcon(stepStatus, 'w-3.5 h-3.5')}
              </div>

              {/* Content */}
              <div className="flex-1 pb-6">
                <div className="flex items-center justify-between">
                  <span className={`text-sm ${labelClasses}`}>
                    {STATUS_LABELS[stepStatus]}
                  </span>

                  {/* Expand toggle when there is a notification template */}
                  {(state === 'completed' || state === 'current') &&
                    hasTemplate && (
                      <button
                        type="button"
                        onClick={() => toggleExpand(stepStatus)}
                        className="text-gray-400 hover:text-gray-600 p-0.5"
                        aria-label={
                          isExpanded
                            ? 'Collapse notification details'
                            : 'Expand notification details'
                        }
                      >
                        {isExpanded ? (
                          <ChevronUp className="w-4 h-4" />
                        ) : (
                          <ChevronDown className="w-4 h-4" />
                        )}
                      </button>
                    )}
                </div>

                {/* Timestamp */}
                {history ? (
                  <p className="text-xs text-gray-500 mt-0.5">
                    {formatTimestamp(history.timestamp)}
                  </p>
                ) : state === 'current' ? (
                  <p className="text-xs text-blue-500 mt-0.5">Current step</p>
                ) : null}

                {/* Note */}
                {history?.note && (
                  <p className="text-xs text-gray-600 mt-1 italic">
                    {history.note}
                  </p>
                )}

                {/* Notification badge for completed steps */}
                {state === 'completed' && history?.notifiedVia && (
                  <div className="mt-1.5">
                    <NotificationBadge via={history.notifiedVia} />
                  </div>
                )}

                {/* Notify buttons for current step */}
                {showNotifyButtons && (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {history?.notifiedVia ? (
                      <NotificationBadge via={history.notifiedVia} />
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() =>
                            onSendNotification(stepStatus, 'SMS')
                          }
                          className="inline-flex items-center gap-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-lg px-2.5 py-1 transition-colors"
                        >
                          <Phone className="w-3 h-3" />
                          <Send className="w-3 h-3" />
                          SMS
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            onSendNotification(stepStatus, 'WHATSAPP')
                          }
                          className="inline-flex items-center gap-1 text-xs font-medium text-green-700 bg-green-50 hover:bg-green-100 border border-green-200 rounded-lg px-2.5 py-1 transition-colors"
                        >
                          <MessageSquare className="w-3 h-3" />
                          <Send className="w-3 h-3" />
                          WhatsApp
                        </button>
                      </>
                    )}
                  </div>
                )}

                {/* Expanded message preview */}
                {isExpanded && (
                  <MessagePreview
                    status={stepStatus}
                    customerName={customerName}
                    orderNumber={orderNumber}
                  />
                )}
              </div>
            </div>
          );
        })}

        {/* Cancelled step (rendered separately at the end) */}
        {isCancelled && (
          <div className="relative flex gap-3">
            {/* Dot */}
            <div className="relative z-10 flex-shrink-0 w-7 h-7 rounded-full border-2 bg-red-500 text-white border-red-500 flex items-center justify-center">
              <X className="w-3.5 h-3.5" />
            </div>

            {/* Content */}
            <div className="flex-1 pb-2">
              <span className="text-sm font-semibold text-red-700">
                {STATUS_LABELS.CANCELLED}
              </span>
              {(() => {
                const cancelEntry = findHistoryEntry('CANCELLED');
                return cancelEntry ? (
                  <>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {formatTimestamp(cancelEntry.timestamp)}
                    </p>
                    {cancelEntry.note && (
                      <p className="text-xs text-red-600 mt-1 italic">
                        {cancelEntry.note}
                      </p>
                    )}
                  </>
                ) : null;
              })()}
            </div>
          </div>
        )}
      </div>

      {/* Footer with order created timestamp */}
      <div className="mt-2 pt-3 border-t border-gray-100">
        <p className="text-xs text-gray-400">
          Order created {formatTimestamp(createdAt)}
        </p>
      </div>
    </div>
  );
}

export default OrderNotificationTracker;
