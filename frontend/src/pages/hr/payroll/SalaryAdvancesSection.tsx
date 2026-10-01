// /hr/payroll/advances -- Advances. Moved byte-identical from the old
// PayrollDashboard 'advances' tab; its state and handlers live in
// PayrollLayout and arrive through <Outlet context>.

import { Loader2, Plus, Check, Clock, X } from 'lucide-react';
import { PayrollAccessNotice } from '../../../components/hr/PayrollAccessNotice';
import { usePayrollContext } from './payrollShared';

export function SalaryAdvancesSection() {
  const {
    selectedEmployee, setSelectedEmployee, salarySheet, advances, isLoading, noAccess,
    showAdvanceForm, setShowAdvanceForm, advanceForm, setAdvanceForm,
    loadAdvances, handleRecordAdvance,
  } = usePayrollContext();

  return (
    <div className="space-y-4">
      {/* Employee selector */}
      <div className="bg-white border border-gray-200 p-4 rounded-lg">
        <div className="flex items-center gap-4">
          <select
            value={selectedEmployee}
            onChange={(e) => {
              setSelectedEmployee(e.target.value);
              if (e.target.value) {
                loadAdvances(e.target.value);
              }
            }}
            title="Select employee for advances"
            className="bg-white border border-gray-300 text-gray-900 px-4 py-2 rounded flex-1"
          >
            <option value="">Select employee...</option>
            {salarySheet.map((salary) => (
              <option key={salary.employee_id} value={salary.employee_id}>
                {salary.employee_name}
              </option>
            ))}
          </select>
          {selectedEmployee && (
            <button
              onClick={() => setShowAdvanceForm(!showAdvanceForm)}
              className="btn-primary flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              Record Advance
            </button>
          )}
        </div>
      </div>

      {/* Advance form */}
      {showAdvanceForm && selectedEmployee && (
        <div className="bg-white border border-gray-200 p-4 rounded-lg space-y-3">
          <input
            type="number"
            placeholder="Advance amount (₹)"
            value={advanceForm.amount}
            onChange={(e) => setAdvanceForm({ ...advanceForm, amount: e.target.value })}
            className="w-full bg-white border border-gray-300 text-gray-900 px-3 py-2 rounded text-sm"
          />
          <textarea
            placeholder="Reason (optional)"
            value={advanceForm.reason}
            onChange={(e) => setAdvanceForm({ ...advanceForm, reason: e.target.value })}
            className="w-full bg-white border border-gray-300 text-gray-900 px-3 py-2 rounded text-sm h-20"
          />
          <div className="flex gap-2">
            <button
              onClick={() => handleRecordAdvance(selectedEmployee)}
              className="btn-primary flex-1"
            >
              Submit
            </button>
            <button
              onClick={() => setShowAdvanceForm(false)}
              className="btn-outline flex-1"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Advances list */}
      {selectedEmployee && (
        isLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
          </div>
        ) : noAccess ? (
          <PayrollAccessNotice message={noAccess} what="salary advances" />
        ) : advances.length === 0 ? (
          <div className="text-center py-8 text-gray-500">No advances recorded</div>
        ) : (
          <div className="space-y-3">
            {advances.map((adv) => (
              <div
                key={adv.advance_id}
                className="bg-white border border-gray-200 p-4 rounded-lg flex items-center justify-between"
              >
                <div>
                  <p className="text-gray-900 font-medium">₹{adv.amount.toLocaleString()}</p>
                  <p className="text-gray-500 text-sm">{adv.date_requested}</p>
                </div>
                <div className="flex items-center gap-2">
                  {adv.status === 'pending' && (
                    <span className="flex items-center gap-1 text-yellow-600 text-sm">
                      <Clock className="w-4 h-4" /> Pending
                    </span>
                  )}
                  {adv.status === 'approved' && (
                    <span className="flex items-center gap-1 text-green-600 text-sm">
                      <Check className="w-4 h-4" /> Approved
                    </span>
                  )}
                  {adv.status === 'settled' && (
                    <span className="flex items-center gap-1 text-blue-600 text-sm">
                      <Check className="w-4 h-4" /> Settled
                    </span>
                  )}
                  {adv.status === 'deducted' && (
                    <span className="flex items-center gap-1 text-gray-500 text-sm">
                      <X className="w-4 h-4" /> Deducted
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  );
}
