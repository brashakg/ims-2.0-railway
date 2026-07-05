// ============================================================================
// IMS 2.0 - PO Lifecycle Drawer (owner 5-word vocabulary timeline)
// ============================================================================
// Right-side slide-in drawer (same scrim/panel pattern as SendToFloorDrawer)
// opened from the PO number in the purchase-orders list. Shows the full PO
// lifecycle from GET /vendors/purchase-orders/{po_id}/timeline (PR #869):
//   header  : PO number + vendor + PurchaseStatusChip
//   timeline: chronological events in the owner vocabulary
//             (Ordered / Sent / Box received / On shelf / Bill settled)
//   lists   : raw linked GRNs + purchase invoices with their statuses
//   footer  : ONE derived next-step action --
//             DRAFT                      -> "Send to vendor" (parent callback;
//                                           PurchaseTable routes it to the PO
//                                           detail modal where send lives)
//             receivable w/ residuals    -> "Receive" deep-link (role-gated to
//                                           the /purchase/receive route roles)
//             accepted GRN, no invoice   -> "Book invoice" (AP roles only)
// Fail-soft: fetch errors render an inline error line + Retry, never a blank
// panel. Esc and scrim-click both close.

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  X,
  FileText,
  Send,
  Package,
  CheckCircle2,
  Receipt,
  XCircle,
  Circle,
  Truck,
  RefreshCw,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { vendorsApi } from '../../services/api/inventory';
import { useAuth } from '../../context/AuthContext';
import { PurchaseStatusChip } from './PurchaseStatusChip';
import { RECEIVABLE_PO_STATUSES } from '../../pages/purchase/purchaseTypes';
import type { POStatus } from '../../pages/purchase/purchaseTypes';

// ---------------------------------------------------------------------------
// Timeline payload types (mirror backend response, PR #869)
// ---------------------------------------------------------------------------

export type POTimelineEventKind =
  | 'ordered'
  | 'sent'
  | 'cancelled'
  | 'box_received'
  | 'on_shelf'
  | 'bill_settled';

export interface POTimelineEvent {
  kind: POTimelineEventKind | string;
  label: string;
  at?: string | null;
  ref?: string | null;
  detail?: string | null;
}

export interface POTimelineGRN {
  grn_id: string;
  grn_number: string;
  status: string;
  created_at?: string | null;
  accepted_at?: string | null;
  total_accepted?: number | null;
}

export interface POTimelineInvoice {
  bill_id: string;
  invoice_number: string;
  status: string;
  total?: number | null;
  created_at?: string | null;
}

export interface POTimeline {
  po_id: string;
  po_number: string;
  status: string;
  vendor_id: string;
  vendor_name: string;
  delivery_store_id?: string | null;
  events: POTimelineEvent[];
  grns: POTimelineGRN[];
  invoices: POTimelineInvoice[];
}

export interface POLifecycleDrawerProps {
  poId: string;
  /** Shown in the header while the timeline is loading / if the fetch fails. */
  poNumber?: string;
  onClose: () => void;
  /** DRAFT next-step. The parent owns the send path (PurchaseTable routes it
   *  to the PO detail modal where the send action lives). Omitted -> the
   *  "Send to vendor" button is not offered. */
  onSendToVendor?: () => void;
}

// ---------------------------------------------------------------------------
// Presentation helpers (module-level -- no components inside components)
// ---------------------------------------------------------------------------

const EVENT_ICONS: Record<string, typeof FileText> = {
  ordered: FileText,
  sent: Send,
  cancelled: XCircle,
  box_received: Package,
  on_shelf: CheckCircle2,
  bill_settled: Receipt,
};

const EVENT_ICON_CLASSES: Record<string, string> = {
  ordered: 'bg-gray-100 text-gray-600',
  sent: 'bg-indigo-50 text-indigo-600',
  cancelled: 'bg-red-50 text-red-600',
  box_received: 'bg-amber-50 text-amber-600',
  on_shelf: 'bg-green-50 text-green-600',
  bill_settled: 'bg-teal-50 text-teal-600',
};

/** Humanised date+time, e.g. "16 Jun 2026, 2:45 pm". Fail-soft on bad input. */
function fmtDateTime(at: string | null | undefined): string {
  if (!at) return '';
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return String(at);
  return d.toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/** Mirrors the /purchase/receive ProtectedRoute gate in App.tsx -- never hand
 *  a role a button that lands on /unauthorized. */
const RECEIVE_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] as const;

/** AP-capable roles (mirrors the /purchase/recon-console gate -- the invoice
 *  booking surface is an accountant function). */
const AP_ROLES = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] as const;

/** GRN statuses that mean accepted stock is on the shelf and billable. */
const ACCEPTED_GRN_STATUSES = new Set(['ACCEPTED', 'PARTIALLY_ACCEPTED']);

/** Invoice statuses that do NOT count as a live bill (a voided bill still
 *  needs re-booking). */
const DEAD_INVOICE_STATUSES = new Set(['CANCELLED', 'VOID', 'VOIDED']);

type NextStep =
  | { action: 'send'; label: string }
  | { action: 'receive'; label: string; to: string }
  | { action: 'book'; label: string; to: string };

/** Derive the ONE next-step action from the timeline payload + role gates.
 *  Precedence: send (DRAFT) > receive (receivable residuals) > book invoice
 *  (accepted GRN without a live bill). CANCELLED POs get no action. */
export function deriveNextStep(
  tl: POTimeline,
  opts: { canSend: boolean; canReceive: boolean; canBookInvoice: boolean },
): NextStep | null {
  const status = String(tl.status || '').toUpperCase();
  if (status === 'CANCELLED') return null;

  if (status === 'DRAFT' && opts.canSend) {
    return { action: 'send', label: 'Send to vendor' };
  }

  // Receivable statuses inherently carry pending residuals -- the backend
  // flips the PO to RECEIVED once every line is fully received.
  if (RECEIVABLE_PO_STATUSES.includes(status as POStatus) && opts.canReceive) {
    return {
      action: 'receive',
      label: 'Receive',
      to: `/purchase/receive?vendor_id=${encodeURIComponent(tl.vendor_id)}&po_id=${encodeURIComponent(tl.po_id)}`,
    };
  }

  const acceptedGrn = (tl.grns ?? []).find((g) =>
    ACCEPTED_GRN_STATUSES.has(String(g.status || '').toUpperCase()),
  );
  const hasLiveInvoice = (tl.invoices ?? []).some(
    (inv) => !DEAD_INVOICE_STATUSES.has(String(inv.status || '').toUpperCase()),
  );
  if (acceptedGrn && !hasLiveInvoice && opts.canBookInvoice) {
    return {
      action: 'book',
      label: 'Book invoice',
      to: `/purchase/invoices/book?grn_id=${encodeURIComponent(acceptedGrn.grn_id)}`,
    };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Drawer
// ---------------------------------------------------------------------------

export function POLifecycleDrawer({ poId, poNumber, onClose, onSendToVendor }: POLifecycleDrawerProps) {
  const navigate = useNavigate();
  const { hasRole } = useAuth();
  const closeRef = useRef<HTMLButtonElement | null>(null);

  const [timeline, setTimeline] = useState<POTimeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const data = (await vendorsApi.getPOTimeline(poId)) as POTimeline;
      setTimeline(data);
    } catch {
      // Fail-soft: keep the drawer up with an inline error line (+ Retry).
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [poId]);

  useEffect(() => {
    load();
  }, [load]);

  // Esc closes; focus lands on the close button so keyboard users are inside
  // the dialog immediately.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    closeRef.current?.focus();
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const nextStep = timeline
    ? deriveNextStep(timeline, {
        canSend: Boolean(onSendToVendor),
        canReceive: hasRole([...RECEIVE_ROLES]),
        canBookInvoice: hasRole([...AP_ROLES]),
      })
    : null;

  const handleNextStep = () => {
    if (!nextStep) return;
    if (nextStep.action === 'send') {
      onSendToVendor?.();
      return;
    }
    onClose();
    navigate(nextStep.to);
  };

  const events = timeline?.events ?? [];
  const grns = timeline?.grns ?? [];
  const invoices = timeline?.invoices ?? [];

  return (
    <div className="fixed inset-0 z-[60] flex justify-end" role="dialog" aria-modal="true" aria-label={`PO timeline ${timeline?.po_number ?? poNumber ?? ''}`}>
      {/* Scrim */}
      <button
        type="button"
        aria-label="Close"
        className="absolute inset-0 bg-black/30"
        onClick={onClose}
      />

      {/* Drawer panel */}
      <div className="relative h-full w-full max-w-[440px] bg-white shadow-xl flex flex-col">
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-gray-200">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-semibold text-gray-900 truncate">
                {timeline?.po_number ?? poNumber ?? 'Purchase order'}
              </h2>
              {timeline && <PurchaseStatusChip status={timeline.status} />}
            </div>
            {timeline?.vendor_name && (
              <p className="text-xs text-gray-500 mt-0.5 truncate">{timeline.vendor_name}</p>
            )}
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-gray-700 transition-colors flex-shrink-0"
            aria-label="Close drawer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
          {loading && (
            <div className="space-y-3 animate-pulse" data-testid="po-timeline-skeleton">
              <div className="h-4 bg-gray-100 rounded w-2/3" />
              <div className="h-4 bg-gray-100 rounded w-1/2" />
              <div className="h-4 bg-gray-100 rounded w-3/5" />
              <div className="h-4 bg-gray-100 rounded w-2/5" />
            </div>
          )}

          {!loading && loadError && (
            <div className="flex items-center gap-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2.5">
              <span className="flex-1">Couldn&apos;t load this PO&apos;s timeline.</span>
              <button
                type="button"
                onClick={load}
                className="inline-flex items-center gap-1 text-xs font-medium text-red-700 hover:text-red-900 underline"
              >
                <RefreshCw className="w-3.5 h-3.5" /> Retry
              </button>
            </div>
          )}

          {!loading && !loadError && timeline && (
            <>
              {/* Timeline */}
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-3">
                  Timeline
                </p>
                {events.length === 0 ? (
                  <p className="text-sm text-gray-500">No events recorded yet.</p>
                ) : (
                  <ol className="relative ml-3.5 border-l border-gray-200 space-y-5">
                    {events.map((ev, idx) => {
                      const kind = String(ev.kind || '').toLowerCase();
                      const Icon = EVENT_ICONS[kind] ?? Circle;
                      const iconCls = EVENT_ICON_CLASSES[kind] ?? 'bg-gray-100 text-gray-500';
                      return (
                        <li key={idx} className="relative pl-6" data-testid="po-timeline-event">
                          <span
                            className={`absolute -left-3.5 top-0 w-7 h-7 rounded-full flex items-center justify-center ring-4 ring-white ${iconCls}`}
                          >
                            <Icon className="w-3.5 h-3.5" />
                          </span>
                          <div className="flex items-baseline justify-between gap-2">
                            <p className="text-sm font-medium text-gray-900">{ev.label}</p>
                            {ev.at && (
                              <p className="text-xs text-gray-500 whitespace-nowrap">{fmtDateTime(ev.at)}</p>
                            )}
                          </div>
                          {(ev.ref || ev.detail) && (
                            <p className="text-xs text-gray-500 mt-0.5">
                              {[ev.ref, ev.detail].filter(Boolean).join(' — ')}
                            </p>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                )}
              </div>

              {/* GRNs */}
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-2">
                  Goods receipts ({grns.length})
                </p>
                {grns.length === 0 ? (
                  <p className="text-sm text-gray-500">No goods received yet.</p>
                ) : (
                  <div className="space-y-1.5">
                    {grns.map((g) => (
                      <div
                        key={g.grn_id}
                        className="flex items-center justify-between gap-2 border border-gray-200 rounded-lg px-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">{g.grn_number}</p>
                          <p className="text-xs text-gray-500">
                            {fmtDateTime(g.accepted_at ?? g.created_at)}
                            {typeof g.total_accepted === 'number' && ` · ${g.total_accepted} accepted`}
                          </p>
                        </div>
                        <PurchaseStatusChip status={g.status} kind="grn" />
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Invoices */}
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-2">
                  Purchase invoices ({invoices.length})
                </p>
                {invoices.length === 0 ? (
                  <p className="text-sm text-gray-500">No invoice booked yet.</p>
                ) : (
                  <div className="space-y-1.5">
                    {invoices.map((inv) => (
                      <div
                        key={inv.bill_id}
                        className="flex items-center justify-between gap-2 border border-gray-200 rounded-lg px-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">{inv.invoice_number}</p>
                          <p className="text-xs text-gray-500">
                            {fmtDateTime(inv.created_at)}
                            {typeof inv.total === 'number' && ` · ₹${inv.total.toLocaleString()}`}
                          </p>
                        </div>
                        <PurchaseStatusChip status={inv.status} kind="invoice" />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Footer: the ONE derived next-step */}
        {nextStep && (
          <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end">
            <button
              type="button"
              onClick={handleNextStep}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors"
            >
              {nextStep.action === 'send' && <Send className="w-4 h-4" />}
              {nextStep.action === 'receive' && <Truck className="w-4 h-4" />}
              {nextStep.action === 'book' && <Receipt className="w-4 h-4" />}
              {nextStep.label}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default POLifecycleDrawer;
