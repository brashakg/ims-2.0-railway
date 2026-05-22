// ============================================================================
// IMS 2.0 - Payroll Run (Phase 3)
// ============================================================================
// Month-end payroll: pick month + entity/store, key LWP per employee, preview
// the computed payslips, then run -> approve -> lock. Plus the salary register
// and a per-employee payslip view.

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  payrollApi,
  grossOf,
  type SalaryConfig,
  type PayrollRow,
  type PayrollTotals,
  type StatutorySummary,
} from '../../services/api/payroll';
import { entitiesApi, type Entity } from '../../services/api/entities';

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const inr = (n?: number) => `₹${Math.round(n || 0).toLocaleString('en-IN')}`;

const STATUS_BADGE: Record<string, string> = {
  DRAFT: 'badge',
  APPROVED: 'badge badge-info',
  PAID: 'badge badge-success',
};

export function PayrollRunPage() {
  const { user } = useAuth();
  const toast = useToast();
  const roles = user?.roles || [];
  const canRun = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'].some((r) => roles.includes(r as never));
  const canLock = roles.includes('SUPERADMIN' as never) || roles.includes('ADMIN' as never);

  const now = new Date();
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [year, setYear] = useState(now.getFullYear());
  const [entityId, setEntityId] = useState('');
  const [entities, setEntities] = useState<Entity[]>([]);

  const [configs, setConfigs] = useState<SalaryConfig[]>([]);
  const [lwp, setLwp] = useState<Record<string, number>>({});
  const [advances, setAdvances] = useState<Record<string, number>>({});

  const [rows, setRows] = useState<PayrollRow[]>([]);
  const [totals, setTotals] = useState<PayrollTotals>({});
  const [busy, setBusy] = useState(false);
  const [payslip, setPayslip] = useState<PayrollRow | null>(null);
  const [summary, setSummary] = useState<StatutorySummary | null>(null);

  const scope = useCallback(
    () => (entityId ? { entity_id: entityId } : {}),
    [entityId],
  );

  const loadConfigs = useCallback(async () => {
    try {
      const r = await payrollApi.listConfigs(entityId ? { entity_id: entityId } : {});
      setConfigs(r.configs || []);
    } catch {
      setConfigs([]);
    }
  }, [entityId]);

  const loadRows = useCallback(async () => {
    try {
      const r = await payrollApi.listRunRows({ month, year, ...scope() });
      setRows(r.rows || []);
      setTotals(r.totals || {});
    } catch {
      setRows([]);
      setTotals({});
    }
    try {
      const s = await payrollApi.getSummary({ month, year, ...scope() });
      setSummary(s.summary);
    } catch {
      setSummary(null);
    }
  }, [month, year, scope]);

  useEffect(() => {
    entitiesApi.list().then((r) => setEntities(r.entities || [])).catch(() => setEntities([]));
  }, []);

  useEffect(() => {
    loadConfigs();
  }, [loadConfigs]);

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  const anyLocked = rows.some((r) => r.status === 'PAID');
  const anyApproved = rows.some((r) => r.status === 'APPROVED');

  const doRun = async (dryRun: boolean) => {
    setBusy(true);
    try {
      const res = await payrollApi.runPayroll({
        month, year, ...scope(), lwp_days: lwp, advances, dry_run: dryRun,
      });
      if (dryRun) {
        setRows(res.rows || []);
        setTotals(res.totals || {});
        toast.info(`Preview: ${res.count} employees, net ${inr(res.totals?.net)}`);
      } else {
        toast.success(`Payroll run saved (${res.count} employees)`);
        await loadRows();
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Payroll run failed');
    } finally {
      setBusy(false);
    }
  };

  const doApprove = async () => {
    setBusy(true);
    try {
      const r = await payrollApi.approveRun({ month, year, ...scope() });
      toast.success(`Approved ${r.approved} payslips`);
      await loadRows();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Approve failed');
    } finally {
      setBusy(false);
    }
  };

  const doLock = async () => {
    setBusy(true);
    try {
      const r = await payrollApi.lockRun({ month, year, ...scope() });
      toast.success(`Locked ${r.locked} payslips as paid`);
      await loadRows();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Lock failed');
    } finally {
      setBusy(false);
    }
  };

  const download = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };
  const mm = String(month).padStart(2, '0');

  const exportTally = async () => {
    try {
      download(await payrollApi.downloadTallyJv({ month, year, ...scope() }), `salary_jv_${year}_${mm}.xml`);
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Tally export failed'); }
  };
  const exportEcr = async () => {
    try {
      download(await payrollApi.downloadPfEcr({ month, year, ...scope() }), `pf_ecr_${year}_${mm}.txt`);
    } catch (e) { toast.error(e instanceof Error ? e.message : 'PF ECR export failed'); }
  };
  const printPayslip = async (emp: string) => {
    try {
      const html = await payrollApi.getPayslipHtml(emp, month, year);
      const w = window.open('', '_blank');
      if (w) { w.document.write(html); w.document.close(); w.focus(); }
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Payslip print failed'); }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Payroll Run</h1>
          <p className="text-sm text-gray-500">
            Key unpaid-leave days, preview the computed payslips, then run &rarr; approve &rarr; lock.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select className="input-field" value={month} onChange={(e) => setMonth(Number(e.target.value))}>
            {MONTHS.map((m, i) => <option key={i} value={i + 1}>{m}</option>)}
          </select>
          <select className="input-field" value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {[year - 1, year, year + 1].map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          <select className="input-field" value={entityId} onChange={(e) => setEntityId(e.target.value)}>
            <option value="">All entities</option>
            {entities.map((en) => <option key={en.entity_id} value={en.entity_id}>{en.name}</option>)}
          </select>
        </div>
      </div>

      {/* Inputs */}
      <div className="card overflow-x-auto">
        <div className="px-4 py-2 text-sm font-medium text-gray-700 border-b border-gray-100">
          1. Inputs — {configs.length} employee{configs.length === 1 ? '' : 's'}
        </div>
        {configs.length === 0 ? (
          <div className="p-6 text-center text-gray-500 text-sm">
            No salary configs in scope. Add them under Salary Setup first.
          </div>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="px-3 py-2 text-left">Employee</th>
                <th className="px-3 py-2 text-right">Gross</th>
                <th className="px-3 py-2 text-right">LWP days</th>
                <th className="px-3 py-2 text-right">Advance recovery</th>
              </tr>
            </thead>
            <tbody>
              {configs.map((c) => (
                <tr key={c.employee_id} className="border-t border-gray-100">
                  <td className="px-3 py-2 font-medium text-gray-900">
                    {c.employee_id}
                    {c.designation && <span className="text-gray-400 font-normal"> · {c.designation}</span>}
                  </td>
                  <td className="px-3 py-2 text-right">{inr(grossOf(c))}</td>
                  <td className="px-3 py-2 text-right">
                    <input type="number" min={0} max={31} disabled={!canRun || anyLocked}
                      className="input-field w-20 text-right"
                      value={lwp[c.employee_id] ?? ''}
                      onChange={(e) => setLwp({ ...lwp, [c.employee_id]: Number(e.target.value || 0) })} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <input type="number" min={0} disabled={!canRun || anyLocked}
                      className="input-field w-28 text-right"
                      value={advances[c.employee_id] ?? ''}
                      onChange={(e) => setAdvances({ ...advances, [c.employee_id]: Number(e.target.value || 0) })} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {canRun && configs.length > 0 && (
          <div className="flex justify-end gap-2 p-3 border-t border-gray-100">
            <button className="btn-secondary" onClick={() => doRun(true)} disabled={busy}>Preview</button>
            <button className="btn-primary" onClick={() => doRun(false)} disabled={busy || anyLocked}>
              {anyLocked ? 'Locked' : 'Run (save draft)'}
            </button>
          </div>
        )}
      </div>

      {/* Statutory summary */}
      {summary && summary.count ? (
        <div className="card p-4">
          <div className="text-sm font-medium text-gray-700 mb-2">Statutory summary — {MONTHS[month - 1]} {year}</div>
          <div className="flex flex-wrap gap-6">
            <Stat label="PF payable" value={summary.pf_total_payable} />
            <Stat label="ESI payable" value={summary.esi_total_payable} />
            <Stat label="Professional Tax" value={summary.professional_tax} />
            <Stat label="TDS" value={summary.tds} />
            <Stat label="Net payout" value={summary.net} />
            <Stat label="Employer cost" value={summary.employer_cost} />
          </div>
        </div>
      ) : null}

      {/* Register */}
      <div className="card overflow-x-auto">
        <div className="flex items-center justify-between px-4 py-2 border-b border-gray-100">
          <span className="text-sm font-medium text-gray-700">
            2. Register — {MONTHS[month - 1]} {year}
          </span>
          {rows.length > 0 && (
            <div className="flex flex-wrap gap-2">
              <button className="btn-secondary" onClick={exportTally} disabled={busy}>Tally JV</button>
              <button className="btn-secondary" onClick={exportEcr} disabled={busy}>PF ECR</button>
              {canRun && (
                <button className="btn-secondary" onClick={doApprove} disabled={busy || !rows.some((r) => r.status === 'DRAFT')}>Approve</button>
              )}
              {canLock && (
                <button className="btn-primary" onClick={doLock} disabled={busy || !anyApproved}>Lock (paid)</button>
              )}
            </div>
          )}
        </div>
        {rows.length === 0 ? (
          <div className="p-6 text-center text-gray-500 text-sm">No payroll rows yet. Run payroll above.</div>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="px-3 py-2 text-left">Employee</th>
                <th className="px-3 py-2 text-right">Gross</th>
                <th className="px-3 py-2 text-right">Deductions</th>
                <th className="px-3 py-2 text-right">Net</th>
                <th className="px-3 py-2 text-center">Status</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.employee_id} className="border-t border-gray-100">
                  <td className="px-3 py-2 font-medium text-gray-900">{r.employee_name || r.employee_id}</td>
                  <td className="px-3 py-2 text-right">{inr(r.breakdown?.earnings?.total_earnings)}</td>
                  <td className="px-3 py-2 text-right text-gray-600">{inr(r.deductions)}</td>
                  <td className="px-3 py-2 text-right font-semibold">{inr(r.net_salary)}</td>
                  <td className="px-3 py-2 text-center">
                    <span className={STATUS_BADGE[r.status || 'DRAFT'] || 'badge'}>{r.status || 'DRAFT'}</span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    {r.breakdown && (
                      <button className="text-bv-red-600 hover:underline" onClick={() => setPayslip(r)}>Payslip</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-gray-200 font-medium">
                <td className="px-3 py-2">Totals</td>
                <td className="px-3 py-2 text-right">{inr(totals.gross)}</td>
                <td className="px-3 py-2 text-right">{inr(totals.deductions)}</td>
                <td className="px-3 py-2 text-right">{inr(totals.net)}</td>
                <td colSpan={2}></td>
              </tr>
            </tfoot>
          </table>
        )}
      </div>

      {/* Payslip modal */}
      {payslip?.breakdown && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setPayslip(null)}>
          <div className="card w-full max-w-lg p-6" onClick={(e) => e.stopPropagation()}>
            <div className="text-center border-b border-gray-100 pb-3 mb-4">
              <h2 className="text-lg font-semibold text-gray-900">{payslip.employee_name || payslip.employee_id}</h2>
              <p className="text-xs text-gray-500">{MONTHS[month - 1]} {year} · {payslip.status}</p>
            </div>
            <div className="grid grid-cols-2 gap-6 text-sm">
              <div>
                <h3 className="text-xs font-semibold text-gray-500 mb-2">EARNINGS</h3>
                <Line label="Basic" value={payslip.breakdown.earnings.basic} />
                <Line label="HRA" value={payslip.breakdown.earnings.hra} />
                <Line label="Conveyance" value={payslip.breakdown.earnings.conveyance} />
                <Line label="Medical" value={payslip.breakdown.earnings.medical} />
                <Line label="Special" value={payslip.breakdown.earnings.special_allowance} />
                <Line label="Incentive" value={payslip.breakdown.earnings.incentive} />
                <Line label="Total" value={payslip.breakdown.earnings.total_earnings} bold />
              </div>
              <div>
                <h3 className="text-xs font-semibold text-gray-500 mb-2">DEDUCTIONS</h3>
                <Line label="PF (employee)" value={payslip.breakdown.deductions.pf_employee} />
                <Line label="ESI" value={payslip.breakdown.deductions.esi_employee} />
                <Line label="Professional Tax" value={payslip.breakdown.deductions.professional_tax} />
                <Line label="TDS" value={payslip.breakdown.deductions.tds} />
                <Line label="Advance" value={payslip.breakdown.deductions.advance_recovery} />
                <Line label="Total" value={payslip.breakdown.deductions.total_deductions} bold />
              </div>
            </div>
            <div className="flex justify-between items-center border-t border-gray-100 mt-4 pt-3">
              <span className="text-sm font-semibold">Net pay</span>
              <span className="text-lg font-bold text-gray-900">{inr(payslip.breakdown.net_pay)}</span>
            </div>
            <p className="text-xs text-gray-400 mt-2">
              Employer cost (CTC): {inr(payslip.breakdown.ctc_cost)} · LWP {payslip.breakdown.lwp_days}d
            </p>
            <div className="flex justify-end gap-2 mt-4">
              <button className="btn-secondary" onClick={() => payslip && printPayslip(payslip.employee_id)}>Print</button>
              <button className="btn-secondary" onClick={() => setPayslip(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Line({ label, value, bold }: { label: string; value: number; bold?: boolean }) {
  return (
    <div className={`flex justify-between py-1 ${bold ? 'border-t border-gray-100 mt-1 pt-2 font-semibold' : ''}`}>
      <span className="text-gray-600">{label}</span>
      <span className="text-gray-900">{inr(value)}</span>
    </div>
  );
}

function Stat({ label, value }: { label: string; value?: number }) {
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="font-semibold text-gray-900">{inr(value)}</div>
    </div>
  );
}

export default PayrollRunPage;
