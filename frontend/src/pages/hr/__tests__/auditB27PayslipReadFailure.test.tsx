/**
 * Batch b27: My Work never shows a failed payslip read as "no payslip".
 * GET /hr/me/payslip answers 503 when the read fails; the card says it could
 * not be loaded and offers Try again, instead of "No payslip generated yet."
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'emp-A', name: 'Sunil Kumar', roles: ['SALES_STAFF'], activeStoreId: 'ZZ-STORE' } }),
}));
const toast = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const getMyPayslip = vi.fn();
vi.mock('../../../services/api', () => ({
  hrApi: {
    getMyAttendance: vi.fn(async () => null),
    getMyCommission: vi.fn(async () => null),
    getMyLeaves: vi.fn(async () => null),
    getMyPayslip: (...a: unknown[]) => getMyPayslip(...a),
  },
}));

import { EmployeeSelfService } from '../EmployeeSelfService';

const SLIP = {
  payslip_id: 'PS-1', employee_id: 'emp-A', employee_name: 'Sunil Kumar', month: 3, year: 2025,
  status: 'APPROVED',
  breakdown: { gross_salary: 20000, total_deductions: 2250, net_pay: 17750 },
};

beforeEach(() => vi.clearAllMocks());

describe('My Work: a payslip read that fails is said plainly', () => {
  it('503: "could not be loaded", never "No payslip generated yet."', async () => {
    getMyPayslip.mockRejectedValue(Object.assign(new Error('503'), { response: { status: 503 } }));
    render(<EmployeeSelfService />);
    expect(await screen.findByText(/Your payslip could not be loaded just now/)).toBeInTheDocument();
    expect(screen.queryByText('No payslip generated yet.')).not.toBeInTheDocument();
  });

  it('Try again reads it again and shows the saved payslip', async () => {
    getMyPayslip.mockRejectedValueOnce(new Error('503')).mockResolvedValueOnce({ payslip: SLIP });
    render(<EmployeeSelfService />);
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('₹17,750')).toBeInTheDocument();
    expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
  });
});
