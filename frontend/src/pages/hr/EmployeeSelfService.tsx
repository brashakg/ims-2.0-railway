// ============================================================================
// IMS 2.0 — My Work (Employee Self-Service)
// ============================================================================
// A mobile-first, single-column dashboard ANY staff role can open to see THEIR
// OWN: this-month attendance (read-only grid + present/absent/late counts),
// latest payslip (view + download), commission/incentive to-date, and leave
// balance. Designed for floor staff on a phone (clean at <=390px, large tap
// targets) but works fine on desktop too.
//
// Data comes ONLY from the self-read endpoints under /hr/me/* (hrApi.getMy*),
// which are mounted OUTSIDE the HR finance-role gate and pinned to the
// requesting user server-side -- so a Sales Staff / Cashier / Optometrist /
// Workshop role can use this even though they cannot hit the rest of HR/Payroll.
// Every card fails soft: a failed read just shows an empty state.
//
// Light theme only. No mock data.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CalendarCheck, Clock, FileText, TrendingUp, Award, Download,
  ChevronLeft, ChevronRight, Loader2,
} from 'lucide-react';
import { hrApi } from '../../services/api';
import type {
  MyAttendance, MyLeaves, MyPayslip, MyCommission,
} from '../../services/api/hr';
import { useAuth } from '../../context/AuthContext';

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function rupee(n: number | undefined | null): string {
  return `₹${Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

export function EmployeeSelfService() {
  const { user } = useAuth();
  const now = new Date();

  // Month selector shared by the attendance + commission cards.
  const [month, setMonth] = useState(now.getMonth() + 1); // 1-12
  const [year, setYear] = useState(now.getFullYear());

  const [attendance, setAttendance] = useState<MyAttendance | null>(null);
  const [commission, setCommission] = useState<MyCommission | null>(null);
  const [payslip, setPayslip] = useState<MyPayslip | null>(null);
  const [leaves, setLeaves] = useState<MyLeaves | null>(null);
  const [loadingMonth, setLoadingMonth] = useState(false);
  const [loadingStatic, setLoadingStatic] = useState(true);

  // Month-scoped reads (attendance + commission) reload when the month changes.
  const loadMonth = useCallback(async () => {
    setLoadingMonth(true);
    try {
      const [att, comm] = await Promise.all([
        hrApi.getMyAttendance({ month, year }).catch(() => null),
        hrApi.getMyCommission({ month, year }).catch(() => null),
      ]);
      setAttendance(att);
      setCommission(comm);
    } finally {
      setLoadingMonth(false);
    }
  }, [month, year]);

  // Year-scoped / latest reads (payslip + leave balance) load once.
  const loadStatic = useCallback(async () => {
    setLoadingStatic(true);
    try {
      const [slip, lv] = await Promise.all([
        hrApi.getMyPayslip().catch(() => ({ payslip: null })),
        hrApi.getMyLeaves({ year: now.getFullYear() }).catch(() => null),
      ]);
      setPayslip(slip?.payslip ?? null);
      setLeaves(lv);
    } finally {
      setLoadingStatic(false);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { loadMonth(); }, [loadMonth]);
  useEffect(() => { loadStatic(); }, [loadStatic]);

  const shiftMonth = (delta: number) => {
    let m = month + delta;
    let y = year;
    if (m < 1) { m = 12; y -= 1; }
    if (m > 12) { m = 1; y += 1; }
    setMonth(m);
    setYear(y);
  };
  // Can't page into the future.
  const atCurrentMonth =
    year > now.getFullYear() ||
    (year === now.getFullYear() && month >= now.getMonth() + 1);

  const att = attendance?.summary;

  // Build a compact day grid from attendance.days (date -> code).
  const dayCells = useMemo(() => {
    const daysInMonth = new Date(year, month, 0).getDate();
    const map = attendance?.days || {};
    return Array.from({ length: daysInMonth }, (_, i) => {
      const day = i + 1;
      const key = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
      return { day, code: map[key] || '-' };
    });
  }, [attendance, month, year]);

  const downloadPayslip = () => {
    if (!payslip) return;
    const bd = (payslip.breakdown || {}) as Record<string, any>;
    const monthLabel = payslip.month
      ? `${MONTH_NAMES[(payslip.month || 1) - 1]} ${payslip.year}`
      : '';
    const w = window.open('', '_blank', 'noopener,noreferrer');
    if (!w) return;
    w.document.write(`<!doctype html><html><head><meta charset="utf-8">
      <title>Payslip ${monthLabel}</title>
      <style>
        body{font-family:Arial,Helvetica,sans-serif;color:#111;max-width:640px;margin:24px auto;padding:0 16px;}
        h1{font-size:18px;margin:0 0 2px;}
        .sub{color:#666;font-size:13px;margin-bottom:18px;}
        table{width:100%;border-collapse:collapse;font-size:14px;}
        td{padding:6px 0;border-bottom:1px solid #eee;}
        td.r{text-align:right;}
        .net td{font-weight:bold;border-top:2px solid #111;border-bottom:none;font-size:16px;padding-top:10px;}
        @media print{button{display:none;}}
      </style></head><body>
      <h1>${(payslip.employee_name as string) || 'Payslip'}</h1>
      <div class="sub">${monthLabel}${payslip.employee_id ? ' &middot; ' + payslip.employee_id : ''}</div>
      <table>
        <tr><td>Gross Salary</td><td class="r">${rupee(bd.gross_salary)}</td></tr>
        <tr><td>Total Deductions</td><td class="r">${rupee(bd.total_deductions)}</td></tr>
        <tr class="net"><td>Net Pay</td><td class="r">${rupee(bd.net_pay)}</td></tr>
      </table>
      <p style="margin-top:24px"><button onclick="window.print()">Print / Save as PDF</button></p>
      </body></html>`);
    w.document.close();
  };

  const payslipBd = (payslip?.breakdown || {}) as Record<string, any>;

  return (
    // Single column, phone-comfortable width. Centered so it reads well on a
    // tablet/desktop too without spreading edge to edge.
    <div className="inv-body">
      <div className="mx-auto w-full max-w-md space-y-5 pb-8">
        {/* Header */}
        <header className="pt-1">
          <div className="eyebrow" style={{ marginBottom: 4 }}>My Work</div>
          <h1 className="text-2xl font-bold text-gray-900 leading-tight">
            {user?.name ? `Hi, ${user.name.split(' ')[0]}` : 'My Work'}
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Your attendance, salary, leaves and commission.
          </p>
        </header>

        {/* Month switcher (drives attendance + commission) */}
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => shiftMonth(-1)}
            className="btn-secondary inline-flex items-center justify-center w-11 h-11"
            aria-label="Previous month"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div className="flex-1 text-center font-semibold text-gray-900">
            {MONTH_NAMES[month - 1]} {year}
          </div>
          <button
            type="button"
            onClick={() => shiftMonth(1)}
            disabled={atCurrentMonth}
            className="btn-secondary inline-flex items-center justify-center w-11 h-11 disabled:opacity-40"
            aria-label="Next month"
          >
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>

        {/* Attendance card */}
        <section className="card p-4">
          <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3 flex items-center gap-2">
            <CalendarCheck className="w-4 h-4" /> Attendance
          </h2>
          {loadingMonth ? (
            <CardSpinner />
          ) : (
            <>
              {/* Big tappable stat tiles — 2 cols on phone, 4 on tablet+ */}
              <div className="grid grid-cols-2 tablet:grid-cols-4 gap-2.5">
                <Stat label="Present" value={att?.present ?? 0} tone="green" />
                <Stat label="Absent" value={att?.absent ?? 0} tone="red" />
                <Stat label="Late" value={att?.late ?? 0} tone="amber" />
                <Stat label="Half Day" value={att?.half_day ?? 0} tone="yellow" />
              </div>
              <div className="grid grid-cols-2 gap-2.5 mt-2.5">
                <Stat label="On Leave" value={att?.leave ?? 0} tone="blue" />
                <Stat label="Week Off" value={att?.week_off ?? 0} tone="gray" />
              </div>

              {/* Read-only day grid */}
              <div className="mt-4">
                <p className="text-xs text-gray-400 mb-2">Daily record</p>
                <div className="grid grid-cols-7 gap-1.5">
                  {dayCells.map(({ day, code }) => (
                    <div
                      key={day}
                      className={`flex flex-col items-center justify-center rounded-md py-1.5 text-[11px] leading-none ${codeClasses(code)}`}
                      title={`Day ${day}: ${codeLabel(code)}`}
                    >
                      <span className="font-semibold">{day}</span>
                      <span className="mt-0.5 opacity-80">{code === '-' ? '' : code}</span>
                    </div>
                  ))}
                </div>
                <Legend />
              </div>
            </>
          )}
        </section>

        {/* Payslip card */}
        <section className="card p-4">
          <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3 flex items-center gap-2">
            <FileText className="w-4 h-4" /> Latest Payslip
          </h2>
          {loadingStatic ? (
            <CardSpinner />
          ) : payslip ? (
            <div>
              <p className="text-xs text-gray-500">
                {payslip.month ? `${MONTH_NAMES[(payslip.month || 1) - 1]} ${payslip.year}` : 'Most recent'}
              </p>
              <p className="text-3xl font-bold text-green-700 mt-1">
                {rupee(payslipBd.net_pay)}
              </p>
              <p className="text-xs text-gray-400 mt-0.5">Net pay</p>
              <div className="grid grid-cols-2 gap-3 mt-3 text-sm">
                <div className="bg-gray-50 rounded-lg p-3">
                  <p className="text-xs text-gray-500 mb-0.5">Gross</p>
                  <p className="font-semibold text-gray-900">{rupee(payslipBd.gross_salary)}</p>
                </div>
                <div className="bg-gray-50 rounded-lg p-3">
                  <p className="text-xs text-gray-500 mb-0.5">Deductions</p>
                  <p className="font-semibold text-gray-900">{rupee(payslipBd.total_deductions)}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={downloadPayslip}
                className="btn-secondary w-full mt-3 inline-flex items-center justify-center gap-2 h-11"
              >
                <Download className="w-4 h-4" /> View / Download
              </button>
            </div>
          ) : (
            <p className="text-sm text-gray-400">No payslip generated yet.</p>
          )}
        </section>

        {/* Commission card */}
        <section className="card p-4">
          <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3 flex items-center gap-2">
            <Award className="w-4 h-4" /> Commission ({MONTH_NAMES[month - 1]})
          </h2>
          {loadingMonth ? (
            <CardSpinner />
          ) : commission && (commission.sales_count > 0 || commission.commission_amount > 0) ? (
            <div>
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-bv-red-50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-500 mb-1">Sales</p>
                  <p className="text-2xl font-bold text-gray-900">{commission.sales_count}</p>
                </div>
                <div className="bg-bv-red-50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-500 mb-1">Revenue</p>
                  <p className="text-xl font-bold text-gray-900">
                    {Number(commission.revenue).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                  </p>
                </div>
              </div>
              {commission.commission_rate_percent > 0 ? (
                <div className="bg-green-50 rounded-lg p-3 text-center mt-3">
                  <p className="text-xs text-gray-500 mb-1">
                    Commission @ {commission.commission_rate_percent}%
                  </p>
                  <p className="text-3xl font-bold text-green-700">
                    {rupee(commission.commission_amount)}
                  </p>
                </div>
              ) : (
                <p className="text-xs text-gray-400 mt-3">
                  Commission rate not configured for your role.
                </p>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-400">No sales recorded this month yet.</p>
          )}
        </section>

        {/* Leave balance card */}
        <section className="card p-4">
          <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3 flex items-center gap-2">
            <TrendingUp className="w-4 h-4" /> Leaves ({now.getFullYear()})
          </h2>
          {loadingStatic ? (
            <CardSpinner />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-blue-50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-500 mb-1">Taken (approved)</p>
                  <p className="text-3xl font-bold text-blue-700">
                    {leaves?.summary.approved_days ?? 0}
                  </p>
                  <p className="text-[11px] text-gray-400 mt-0.5">days</p>
                </div>
                <div className="bg-amber-50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-500 mb-1">Pending</p>
                  <p className="text-3xl font-bold text-amber-700">
                    {leaves?.summary.pending_days ?? 0}
                  </p>
                  <p className="text-[11px] text-gray-400 mt-0.5">days</p>
                </div>
              </div>
              {leaves && Object.keys(leaves.summary.by_type).length > 0 && (
                <div className="mt-3 space-y-1.5">
                  {Object.entries(leaves.summary.by_type).map(([type, days]) => (
                    <div key={type} className="flex justify-between text-sm">
                      <span className="text-gray-500 capitalize">{type.toLowerCase()}</span>
                      <span className="font-medium text-gray-900">{days} days</span>
                    </div>
                  ))}
                </div>
              )}
              {/* Recent leave rows */}
              {leaves && leaves.leaves.length > 0 && (
                <div className="mt-3 border-t border-gray-100 pt-3 space-y-2">
                  {leaves.leaves.slice(0, 4).map((l) => (
                    <div key={l.leave_id || `${l.from_date}-${l.leave_type}`} className="flex items-center justify-between gap-2 text-sm">
                      <div className="min-w-0">
                        <p className="font-medium text-gray-900 truncate capitalize">
                          {l.leave_type.toLowerCase()}
                        </p>
                        <p className="text-xs text-gray-400">
                          {l.from_date}{l.to_date && l.to_date !== l.from_date ? ` to ${l.to_date}` : ''} &middot; {l.days}d
                        </p>
                      </div>
                      <span className={`shrink-0 px-2 py-0.5 rounded-full text-[11px] font-medium ${leaveStatusClasses(l.status)}`}>
                        {l.status}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {(!leaves || leaves.leaves.length === 0) && (
                <p className="text-sm text-gray-400 mt-3">No leaves recorded this year.</p>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers + small components
// ---------------------------------------------------------------------------

function CardSpinner() {
  return (
    <div className="flex justify-center py-6">
      <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
    </div>
  );
}

const TONES: Record<string, { bg: string; text: string }> = {
  green: { bg: 'bg-green-50', text: 'text-green-700' },
  red: { bg: 'bg-red-50', text: 'text-red-700' },
  amber: { bg: 'bg-amber-50', text: 'text-amber-700' },
  // yellow consolidated onto amber to keep the status palette muted + consistent.
  yellow: { bg: 'bg-amber-50', text: 'text-amber-700' },
  blue: { bg: 'bg-blue-50', text: 'text-blue-700' },
  gray: { bg: 'bg-gray-50', text: 'text-gray-700' },
};

function Stat({ label, value, tone }: { label: string; value: number; tone: keyof typeof TONES }) {
  const t = TONES[tone] || TONES.gray;
  return (
    <div className={`${t.bg} rounded-lg p-3 text-center`}>
      <p className={`text-2xl font-bold ${t.text}`}>{value}</p>
      <p className="text-[11px] text-gray-500 mt-0.5 flex items-center justify-center gap-1">
        {label === 'Late' && <Clock className="w-3 h-3" />}
        {label}
      </p>
    </div>
  );
}

function codeClasses(code: string): string {
  switch (code) {
    case 'P': return 'bg-green-50 text-green-700';
    case 'A': return 'bg-red-50 text-red-700';
    case 'HD': return 'bg-amber-50 text-amber-700';
    case 'L': return 'bg-blue-50 text-blue-700';
    case 'LWP': return 'bg-amber-50 text-amber-700';
    case 'WO': return 'bg-gray-100 text-gray-500';
    default: return 'bg-gray-50 text-gray-300';
  }
}

function codeLabel(code: string): string {
  switch (code) {
    case 'P': return 'Present';
    case 'A': return 'Absent';
    case 'HD': return 'Half Day';
    case 'L': return 'Leave';
    case 'LWP': return 'Leave w/o Pay';
    case 'WO': return 'Week Off / Holiday';
    default: return 'No record';
  }
}

function Legend() {
  const items: { code: string; label: string }[] = [
    { code: 'P', label: 'Present' },
    { code: 'A', label: 'Absent' },
    { code: 'HD', label: 'Half' },
    { code: 'L', label: 'Leave' },
    { code: 'WO', label: 'Off' },
  ];
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2.5">
      {items.map((it) => (
        <span key={it.code} className="inline-flex items-center gap-1 text-[11px] text-gray-500">
          <span className={`inline-block w-3.5 h-3.5 rounded ${codeClasses(it.code)}`} />
          {it.label}
        </span>
      ))}
    </div>
  );
}

function leaveStatusClasses(status: string): string {
  switch ((status || '').toUpperCase()) {
    case 'APPROVED': return 'bg-green-100 text-green-700';
    case 'PENDING': return 'bg-amber-100 text-amber-700';
    case 'REJECTED': return 'bg-red-100 text-red-700';
    default: return 'bg-gray-100 text-gray-600';
  }
}

export default EmployeeSelfService;
