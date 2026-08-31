// ============================================================================
// IMS 2.0 - POS sale-completion screen (Wave 4, owner spec 12)
// ============================================================================
// Shown straight after Complete-sale. At SALE time the customer document is an
// "ORDER RECEIPT" -- the words "final tax invoice" belong to delivery only
// (owner ruling), so the wording lives in ONE stage table below and the
// delivery twin (DeliveryCompleteScreen.tsx) reuses this same component with
// stage="DELIVERY". Two copies of a completion screen would drift; there is
// exactly one implementation here.
//
// This screen owns NO money logic: every rupee shown is read back from
// GET /orders/{id} (the server's grand_total / amount_paid / balance_due /
// bill_type), never recomputed locally.
//
// MOUNT (the parent wires it; do not edit the parent from here):
//
//   import SaleCompleteScreen from './SaleCompleteScreen';
//   ...
//   <SaleCompleteScreen
//     orderId={res.orderId!}
//     orderNumber={res.orderNumber}
//     jobId={res.fittingJobId}            // omit when no workshop job spawned
//     salespersonId={store.salesperson_id}
//     salespersonName={store.salesperson_name}
//     onDone={() => store.resetTransaction()}
//   />
//
// It fills its flex/grid cell (h-full) and scrolls INTERNALLY -- the POS page
// itself must never scroll (spec 11b).

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  PackageCheck,
  Printer,
  X,
} from 'lucide-react';
import api from '../../../services/api/client';
import { orderApi, workshopApi } from '../../../services/api/sales';
import { marketingApi } from '../../../services/api/marketing';
import { incentiveApi } from '../../../services/api/incentive';
import { walkoutsApi } from '../../../services/api/walkouts';
import {
  resolveStoreIdentity,
  type StoreIdentity,
} from '../../../components/print/storeIdentity';
import { WorkshopJobCardPrint } from '../../../components/print/WorkshopJobCardPrint';
import { inr } from '../../../components/print/legalPrimitives';
import { istDayString } from '../../../utils/datetime';
import { useAuth } from '../../../context/AuthContext';
import type { MTDStaffEntry, PerStaffCard, PointsLog } from '../../../types';

// ---------------------------------------------------------------------------
// Server shapes (GET /orders/{id} is camelCase via order_to_frontend; the axios
// interceptor additively aliases anything the map misses, e.g. bill_type).
// ---------------------------------------------------------------------------

interface OrderLine {
  productName?: string;
  quantity?: number;
  unitPrice?: number;
  finalPrice?: number;
  discountPercent?: number;
}

interface OrderView {
  id?: string;
  orderNumber?: string;
  storeId?: string;
  customerId?: string;
  customerName?: string;
  customerPhone?: string;
  salespersonId?: string;
  salespersonName?: string;
  orderStatus?: string;
  billType?: string;
  invoiceNumber?: string;
  subtotal?: number;
  totalDiscount?: number;
  taxAmount?: number;
  grandTotal?: number;
  amountPaid?: number;
  balanceDue?: number;
  items?: OrderLine[];
}

interface JobView {
  jobNumber?: string;
  orderNumber?: string;
  customerName?: string;
  customerPhone?: string;
  frameName?: string;
  lensType?: string;
  priority?: string;
  promisedDate?: string;
  expectedDate?: string;
  assignedTo?: string;
  assignedToName?: string;
  status?: string;
  createdAt?: string;
}

export type CompletionStage = 'SALE' | 'DELIVERY';

type SendKey = 'wa_doc' | 'wa_thanks' | 'sms_doc' | 'sms_thanks';
type SendState = 'idle' | 'sending' | 'sent' | 'failed';

// ---------------------------------------------------------------------------
// Stage table -- the ONLY place the two screens differ.
//
// `autoSent` is a statement of fact about the SERVER, not a nicety: the deliver
// door queues ORDER_DELIVERED itself (orders.py, "auto-WhatsApp on completion"),
// so at delivery the manual button must admit the message already went and
// offer a resend. Nothing is auto-queued at SALE time today, so the sale screen
// must NOT claim it was -- flip this to true the day an auto order-receipt send
// is added server-side.
// ---------------------------------------------------------------------------

const STAGE = {
  SALE: {
    title: 'Sale complete',
    docName: 'Order receipt',
    printDocLabel: 'Order receipt (A4)',
    secondPrintLabel: 'Workshop job card',
    docTemplate: 'ORDER_CONFIRMED',
    doneLabel: 'Start next bill',
    autoSent: false,
  },
  DELIVERY: {
    title: 'Delivered',
    docName: 'Final tax invoice',
    printDocLabel: 'Final tax invoice (A4)',
    secondPrintLabel: 'Care & warranty card',
    docTemplate: 'ORDER_DELIVERED',
    doneLabel: 'Next customer',
    autoSent: true,
  },
} as const;

// ---------------------------------------------------------------------------
// Buttons -- 44px minimum, bordered (owner spec 11: no ghost buttons on iPad).
// ---------------------------------------------------------------------------

function PrintButton({
  label,
  primary,
  disabled,
  title,
  onClick,
}: {
  label: string;
  primary?: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={
        'flex-1 min-h-[44px] px-3 rounded-lg border text-sm font-medium flex items-center justify-center gap-2 disabled:opacity-40 ' +
        (primary
          ? 'bg-gray-900 text-white border-gray-900'
          : 'bg-white text-gray-800 border-gray-300 hover:bg-gray-50')
      }
    >
      <Printer className="w-4 h-4 shrink-0" />
      {label}
    </button>
  );
}

function SendButton({
  label,
  note,
  state,
  disabled,
  title,
  onClick,
}: {
  label: string;
  note?: string;
  state: SendState;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
}) {
  const right =
    state === 'sending'
      ? 'Sending...'
      : state === 'sent'
        ? 'Sent - Resend'
        : state === 'failed'
          ? 'Failed - Retry'
          : note || 'Send';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || state === 'sending'}
      title={title}
      className="w-full min-h-[44px] px-3 rounded-lg border border-gray-300 bg-white text-left flex items-center justify-between gap-2 hover:bg-gray-50 disabled:opacity-40"
    >
      <span className="text-sm text-gray-900 truncate">{label}</span>
      <span
        className={
          'text-[11px] shrink-0 ' +
          (state === 'failed'
            ? 'text-red-600'
            : state === 'sent'
              ? 'text-green-700'
              : 'text-gray-500')
        }
      >
        {right}
      </span>
    </button>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-xs text-gray-500">{label}</span>
      <span className="text-sm font-medium text-gray-900 tabular-nums">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// "My day" -- the salesperson scorecard (owner spec 12, tap-through to
// /incentive).
//
// Sourced ONLY from doors that already exist and that any signed-in user may
// read: the daily points log, the MTD points rollup, and the walkout/walk-in
// per-staff cards. Deliberately MISSING: sales-vs-target, bill count and
// average bill. Every per-salesperson sales aggregate in the backend
// (analytics_v2 /staff-leaderboard, reports /sales/by-salesperson,
// reports /staff/ranking) buckets on `sales_staff_id` / `sales_person_id`,
// while orders are written with `salesperson_id` -- so all three return a
// single "Unknown" row. Showing a zero there would be inventing a number, so
// those rows are omitted until a correctly-keyed feed exists.
// ---------------------------------------------------------------------------

function MyDayPanel({
  storeId,
  staffId,
  staffName,
  saleAmount,
}: {
  storeId: string;
  staffId: string;
  staffName?: string;
  saleAmount?: number;
}) {
  const navigate = useNavigate();
  const [today, setToday] = useState<PointsLog | null>(null);
  const [mtd, setMtd] = useState<MTDStaffEntry | null>(null);
  const [walk, setWalk] = useState<PerStaffCard | null>(null);

  useEffect(() => {
    if (!staffId) return;
    let dead = false;
    const day = istDayString(new Date()) || undefined;
    // Fail-soft: a scorecard must never break the completion screen, so each
    // read settles independently and a rejection simply hides its rows.
    void Promise.allSettled([
      incentiveApi.listDaily(day, storeId || undefined),
      incentiveApi.getMtd(undefined, undefined, storeId || undefined),
      walkoutsApi.dashboardPerStaff(storeId || undefined),
    ]).then(([daily, month, perStaff]) => {
      if (dead) return;
      if (daily.status === 'fulfilled') {
        setToday(daily.value.items.find((r) => r.staff_id === staffId) || null);
      }
      if (month.status === 'fulfilled') {
        setMtd(month.value.items.find((r) => r.staff_id === staffId) || null);
      }
      if (perStaff.status === 'fulfilled') {
        setWalk(
          perStaff.value.items.find((r) => r.sales_person_id === staffId) || null,
        );
      }
    });
    return () => {
      dead = true;
    };
  }, [storeId, staffId]);

  return (
    <div className="rounded-xl border border-gray-200 bg-white">
      <button
        type="button"
        onClick={() => navigate('/incentive')}
        title="Open the full incentive scorecard"
        className="w-full min-h-[44px] px-3 flex items-center justify-between gap-2 border-b border-gray-200 hover:bg-gray-50"
      >
        <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
          My day{staffName ? ` - ${staffName}` : ''}
        </span>
        <ChevronRight className="w-4 h-4 text-gray-400 shrink-0" />
      </button>

      <div className="px-3 py-2">
        {!staffId ? (
          <p className="text-xs text-gray-500 py-1">
            No salesperson on this bill, so there is no scorecard to show.
          </p>
        ) : (
          <>
            {typeof saleAmount === 'number' && (
              <Stat label="This sale" value={inr(saleAmount, { withPaise: false })} />
            )}

            {today && (
              <>
                <Stat label="Points today" value={String(today.total)} />
                <Stat label="Conversion points today" value={String(today.conversion)} />
                <Stat label="Eligibility today" value={String(today.eligibility)} />
              </>
            )}

            {mtd && (
              <>
                <Stat
                  label="MTD points per day"
                  value={mtd.avg.total.toFixed(1)}
                />
                <Stat label="Days logged MTD" value={String(mtd.days_logged)} />
                {typeof mtd.rank === 'number' && (
                  <Stat
                    label="Rank in store"
                    value={`#${mtd.rank}${mtd.tier_label ? ` - ${mtd.tier_label}` : ''}`}
                  />
                )}
                {mtd.title_earned && <Stat label="Title" value={mtd.title_earned} />}
              </>
            )}

            {walk && (
              <>
                <Stat label="Walk-ins today" value={String(walk.walk_ins_today)} />
                <Stat label="Walkouts logged today" value={String(walk.walkouts_today)} />
                <Stat label="Walkouts MTD" value={String(walk.walkouts_mtd)} />
                <Stat label="Recovered MTD" value={String(walk.converted_mtd)} />
                <Stat
                  label="Conversion MTD"
                  value={`${Math.round(walk.conversion_pct_mtd)}%`}
                />
                {walk.fu_due_today > 0 && (
                  <Stat label="Follow-ups due today" value={String(walk.fu_due_today)} />
                )}
              </>
            )}

            {!today && !mtd && !walk && (
              <p className="text-xs text-gray-500 py-1">
                Nothing logged for you yet today.
              </p>
            )}

            <p className="mt-2 pt-2 border-t border-gray-100 text-[11px] leading-snug text-gray-400">
              Sales vs target, bill count and average bill are not shown: no
              backend feed groups sales by salesperson yet.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The shared screen. SaleCompleteScreen and DeliveryCompleteScreen are thin
// wrappers around it so the layout, the send doors and the scorecard exist
// exactly once.
// ---------------------------------------------------------------------------

export interface CompletionScreenProps {
  /** Order the sale/handover just wrote. */
  orderId: string;
  /** Optional -- only saves a flash of the id before the order loads. */
  orderNumber?: string;
  stage: CompletionStage;
  /** Workshop job spawned by this order; enables the job-card print. */
  jobId?: string;
  salespersonId?: string;
  salespersonName?: string;
  /** Clear the till and go again. Omit to hide the button. */
  onDone?: () => void;
}

export function CompletionScreen({
  orderId,
  orderNumber,
  stage,
  jobId,
  salespersonId,
  salespersonName,
  onDone,
}: CompletionScreenProps) {
  const { user } = useAuth();
  const cfg = STAGE[stage];

  const [order, setOrder] = useState<OrderView | null>(null);
  const [identity, setIdentity] = useState<StoreIdentity | null>(null);
  const [job, setJob] = useState<JobView | null>(null);
  const [jobCardOpen, setJobCardOpen] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [sends, setSends] = useState<Record<SendKey, SendState>>({
    wa_doc: 'idle',
    wa_thanks: 'idle',
    sms_doc: 'idle',
    sms_thanks: 'idle',
  });

  useEffect(() => {
    let dead = false;
    orderApi
      .getOrder(orderId)
      .then((doc: OrderView) => {
        if (dead) return;
        setOrder(doc);
        // ONE identity resolver for the whole screen: the job card needs the
        // issuing store's letterhead and the message templates need its name.
        const sid = doc.storeId || user?.activeStoreId || '';
        if (sid) {
          void resolveStoreIdentity(sid).then((id) => {
            if (!dead) setIdentity(id);
          });
        }
      })
      .catch(() => {
        if (!dead) setErrorMsg('Could not load the order summary. The sale itself is saved.');
      });
    return () => {
      dead = true;
    };
  }, [orderId, user?.activeStoreId]);

  useEffect(() => {
    if (!jobId) return;
    let dead = false;
    workshopApi
      .getJob(jobId)
      .then((doc: JobView) => {
        if (!dead) setJob(doc);
      })
      .catch(() => {
        if (!dead) setJob(null);
      });
    return () => {
      dead = true;
    };
  }, [jobId]);

  const storeId = order?.storeId || user?.activeStoreId || '';
  const storeName =
    identity?.store.storeName || identity?.store.storeCode || 'our store';
  const number = order?.orderNumber || orderNumber || orderId;
  const phone = order?.customerPhone || '';
  const balance = Number(order?.balanceDue || 0);

  const openDocumentPdf = async () => {
    setErrorMsg(null);
    try {
      // Server-rendered A4 (GET /orders/{id}/invoice.pdf) -- the same assembly
      // the JSON invoice door uses, so nothing is laid out or totalled here.
      const res = await api.get(`/orders/${orderId}/invoice.pdf`, {
        responseType: 'blob',
      });
      const url = URL.createObjectURL(
        new Blob([res.data as BlobPart], { type: 'application/pdf' }),
      );
      const win = window.open(url, '_blank');
      if (!win) setErrorMsg('Allow pop-ups for this site to open the printable document.');
      // Keep the blob alive long enough for the new tab to load it.
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      // An error body on a blob request is itself a Blob, so there is no
      // readable server detail here -- keep the message generic.
      setErrorMsg('Could not build the document. Check the store GSTIN in settings.');
    }
  };

  const runSend = async (key: SendKey, send: () => Promise<unknown>) => {
    setErrorMsg(null);
    setSends((s) => ({ ...s, [key]: 'sending' }));
    try {
      await send();
      setSends((s) => ({ ...s, [key]: 'sent' }));
    } catch (err: any) {
      setSends((s) => ({ ...s, [key]: 'failed' }));
      // The server owns every refusal here (role gate, marketing opt-out,
      // DLT quiet hours, rate limit) -- surface it verbatim.
      setErrorMsg(err?.response?.data?.detail || 'Could not send that message.');
    }
  };

  const sendDocument = (channel: 'WHATSAPP' | 'SMS') =>
    runSend(channel === 'WHATSAPP' ? 'wa_doc' : 'sms_doc', () =>
      marketingApi.sendNotification({
        customer_id: order?.customerId || '',
        customer_phone: phone,
        customer_name: order?.customerName || 'Customer',
        template_id: cfg.docTemplate,
        channel,
        category: 'SERVICE',
        variables: { order_number: number, store_name: storeName },
      }),
    );

  const sendThankYou = () =>
    runSend('wa_thanks', () => marketingApi.sendReviewRequest(orderId));

  return (
    <div className="min-h-full lg:h-full lg:min-h-0 flex flex-col overflow-y-auto lg:overflow-hidden bg-gray-50">
      {errorMsg && (
        <div className="mx-3.5 mt-2 rounded-lg p-2.5 flex items-center gap-2 text-sm bg-red-50 border border-red-200 text-red-700">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span className="flex-1">{errorMsg}</span>
          <button onClick={() => setErrorMsg(null)} aria-label="Dismiss" title="Dismiss">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      <div className="flex-1 lg:min-h-0 flex flex-col lg:flex-row gap-3.5 p-3.5">
        {/* LEFT: what just happened */}
        <div className="flex-1 min-w-0 lg:min-h-0 flex flex-col gap-3">
          <div className="shrink-0 rounded-xl border border-green-200 bg-green-50 p-4 flex items-start gap-3">
            {stage === 'SALE' ? (
              <CheckCircle2 className="w-6 h-6 text-green-700 shrink-0 mt-0.5" />
            ) : (
              <PackageCheck className="w-6 h-6 text-green-700 shrink-0 mt-0.5" />
            )}
            <div className="min-w-0">
              <div className="text-lg font-semibold text-green-900">{cfg.title}</div>
              <div className="text-sm text-green-800 truncate">
                {number}
                {order?.customerName ? ` - ${order.customerName}` : ''}
                {phone ? ` - ${phone}` : ''}
              </div>
            </div>
          </div>

          <div className="lg:flex-1 lg:min-h-0 lg:overflow-y-auto rounded-xl border border-gray-200 bg-white p-3">
            <div className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
              {cfg.docName}
            </div>
            <ul className="mt-2 divide-y divide-gray-100">
              {(order?.items || []).map((it, i) => (
                <li key={i} className="py-1.5 flex items-baseline justify-between gap-3">
                  <span className="text-sm text-gray-800 truncate">
                    {it.quantity ?? 1} x {it.productName || 'Item'}
                  </span>
                  <span className="text-sm text-gray-900 tabular-nums shrink-0">
                    {inr(Number(it.finalPrice ?? it.unitPrice ?? 0), { withPaise: false })}
                  </span>
                </li>
              ))}
            </ul>

            {/* Every figure below is the SERVER's -- no local arithmetic. */}
            <div className="mt-3 pt-3 border-t border-gray-200">
              {typeof order?.totalDiscount === 'number' && order.totalDiscount > 0 && (
                <Stat label="Discount" value={inr(order.totalDiscount, { withPaise: false })} />
              )}
              <Stat
                label="Total"
                value={inr(Number(order?.grandTotal || 0), { withPaise: false })}
              />
              <Stat
                label="Paid"
                value={inr(Number(order?.amountPaid || 0), { withPaise: false })}
              />
              {balance > 0 && (
                <div className="flex items-baseline justify-between gap-3 py-1">
                  <span className="text-xs text-gray-500">Balance due</span>
                  <span className="text-sm font-semibold text-red-600 tabular-nums">
                    {inr(balance, { withPaise: false })}
                  </span>
                </div>
              )}
              {order?.billType && (
                <Stat label="Bill type" value={String(order.billType).replace(/_/g, ' ')} />
              )}
            </div>
          </div>
        </div>

        {/* RIGHT: print, send, scorecard */}
        <div className="w-full lg:w-[430px] shrink-0 lg:min-h-0 flex flex-col gap-3">
          <div className="shrink-0 rounded-xl border border-gray-200 bg-white p-3">
            <div className="text-[10px] font-medium uppercase tracking-widest text-gray-500 mb-2">
              Print
            </div>
            <div className="flex gap-2">
              <PrintButton label={cfg.printDocLabel} primary onClick={openDocumentPdf} />
              {stage === 'SALE' ? (
                // The job card only exists when the order spawned a workshop job.
                jobId ? (
                  <PrintButton
                    label={cfg.secondPrintLabel}
                    disabled={!job}
                    title={job ? undefined : 'Loading the workshop job...'}
                    onClick={() => setJobCardOpen(true)}
                  />
                ) : null
              ) : (
                <PrintButton
                  label={cfg.secondPrintLabel}
                  disabled
                  title="No care & warranty card template exists yet - needs to be built before this can print."
                  onClick={() => undefined}
                />
              )}
            </div>
          </div>

          <div className="shrink-0 rounded-xl border border-gray-200 bg-white p-3 space-y-2">
            <div className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
              Send
            </div>
            <SendButton
              label={`WhatsApp ${cfg.docName.toLowerCase()}`}
              // Owner rule: never pretend a message was not already sent.
              note={cfg.autoSent ? 'Sent automatically - Resend' : undefined}
              state={sends.wa_doc}
              disabled={!phone}
              title={phone ? undefined : 'This customer has no phone number on file.'}
              onClick={() => void sendDocument('WHATSAPP')}
            />
            <SendButton
              label="WhatsApp thank-you + review link"
              state={sends.wa_thanks}
              disabled={!phone}
              title={phone ? undefined : 'This customer has no phone number on file.'}
              onClick={() => void sendThankYou()}
            />
            <SendButton
              label={`SMS ${cfg.docName.toLowerCase()}`}
              state={sends.sms_doc}
              disabled={!phone}
              title={phone ? undefined : 'This customer has no phone number on file.'}
              onClick={() => void sendDocument('SMS')}
            />
            <SendButton
              label="SMS thank-you"
              state={sends.sms_thanks}
              disabled
              title="No SMS thank-you template is registered - the review link only exists in the WhatsApp flow."
              onClick={() => undefined}
            />
            <p className="text-[11px] leading-snug text-gray-400">
              Sends are queued; nothing leaves the building until dispatch is
              armed.
            </p>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto">
            <MyDayPanel
              storeId={storeId}
              staffId={salespersonId || order?.salespersonId || user?.id || ''}
              staffName={salespersonName || order?.salespersonName}
              saleAmount={order?.grandTotal}
            />
          </div>

          {onDone && (
            <button
              type="button"
              onClick={onDone}
              className="h-12 shrink-0 rounded-xl bg-gray-900 text-white font-semibold text-base border border-gray-900"
            >
              {cfg.doneLabel}
            </button>
          )}
        </div>
      </div>

      {jobCardOpen && job && identity && (
        <WorkshopJobCardPrint
          job={{
            jobNumber: job.jobNumber || '',
            orderNumber: job.orderNumber || number,
            customerName: job.customerName || order?.customerName || '',
            customerPhone: job.customerPhone || phone,
            frameBrand: (job.frameName || '').split(' ')[0],
            frameModel: (job.frameName || '').replace(/^[^ ]+ /, ''),
            frameColor: '',
            lensType: job.lensType || '',
            priority: job.priority || 'NORMAL',
            dueDate: job.promisedDate || job.expectedDate || '',
            assignedTechnician: job.assignedToName || job.assignedTo,
            status: job.status || '',
            createdDate: job.createdAt || '',
          }}
          store={{
            storeName: identity.store.storeName,
            storeCode: identity.store.storeCode,
            brand: identity.store.brand,
            address: identity.store.address,
            city: identity.store.city,
            state: identity.store.state,
            stateCode: identity.store.stateCode,
            pincode: identity.store.pincode,
          }}
          entity={identity.entity}
          onClose={() => setJobCardOpen(false)}
        />
      )}
    </div>
  );
}

export function SaleCompleteScreen(
  props: Omit<CompletionScreenProps, 'stage'>,
) {
  return <CompletionScreen {...props} stage="SALE" />;
}

export default SaleCompleteScreen;
