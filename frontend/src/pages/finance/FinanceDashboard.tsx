// ============================================================================
// IMS 2.0 - Finance & Accounting Dashboard
// ============================================================================
// Comprehensive financial management for Indian optical retail accounting
// Supports GST management, P&L reporting, cash flow, reconciliation, budgeting

import { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

import type { TabType } from './financeTypes';
import type {
  RevenueData,
  ProfitLossStatement,
  GSTSummaryData,
  OutstandingReceivable,
  CashFlowData,
  BudgetData,
  VendorPaymentData,
  ReconciliationData,
} from './financeTypes';
import { generateSampleData } from './financeUtils';

import FinanceFilters from './FinanceFilters';
import FinanceSummary from './FinanceSummary';
import GSTPanel from './GSTPanel';
import OutstandingPanel from './OutstandingPanel';
import CashFlowPanel from './CashFlowPanel';
import PeriodManagement from './PeriodManagement';
import BudgetPanel from './BudgetPanel';
import VendorPayments from './VendorPayments';
import ReconciliationPanel from './ReconciliationPanel';

export default function FinanceDashboard() {
  const { user } = useAuth();
  const toast = useToast();

  // Tab management
  const [activeTab, setActiveTab] = useState<TabType>('revenue-pl');

  // Date filters
  const [dateFrom, setDateFrom] = useState(
    new Date(new Date().getFullYear(), 3, 1).toISOString().split('T')[0] // Financial year start: April 1
  );
  const [dateTo, setDateTo] = useState(new Date().toISOString().split('T')[0]);

  // Data states
  const [revenueData, setRevenueData] = useState<RevenueData[]>([]);
  const [plStatement, setPLStatement] = useState<ProfitLossStatement | null>(null);
  const [gstSummary, setGSTSummary] = useState<GSTSummaryData | null>(null);
  const [outstanding, setOutstanding] = useState<OutstandingReceivable[]>([]);
  const [cashFlow, setCashFlow] = useState<CashFlowData[]>([]);
  const [budgets, setBudgets] = useState<BudgetData[]>([]);
  const [vendorPayments, setVendorPayments] = useState<VendorPaymentData[]>([]);
  const [reconciliation, setReconciliation] = useState<ReconciliationData[]>([]);

  // UI states
  const [isLoading, setIsLoading] = useState(true);
  const [selectedYear, setSelectedYear] = useState('2025-2026');
  const [periodLocked, setPeriodLocked] = useState(false);

  useEffect(() => {
    loadFinanceData();
  }, [activeTab, dateFrom, dateTo, selectedYear]);

  const loadFinanceData = async () => {
    setIsLoading(true);
    try {
      // Mock data initialization - in production, fetch from API
      setTimeout(() => {
        const data = generateSampleData(dateFrom, dateTo);
        setRevenueData(data.revenueData);
        setPLStatement(data.plStatement);
        setGSTSummary(data.gstSummary);
        setOutstanding(data.outstanding);
        setCashFlow(data.cashFlow);
        setBudgets(data.budgets);
        setVendorPayments(data.vendorPayments);
        setReconciliation(data.reconciliation);
        setIsLoading(false);
      }, 500);
    } catch (error) {
      toast.error('Failed to load financial data');
      setIsLoading(false);
    }
  };

  const handleLockPeriod = () => {
    if (user?.activeRole !== 'ACCOUNTANT' && user?.activeRole !== 'ADMIN') {
      toast.error('Only accountants and admins can lock periods');
      return;
    }
    setPeriodLocked(true);
    toast.success('Financial period locked successfully');
  };

  const handleUnlockPeriod = () => {
    if (user?.activeRole !== 'ADMIN' && user?.activeRole !== 'SUPERADMIN') {
      toast.error('Only admins can unlock periods');
      return;
    }
    setPeriodLocked(false);
    toast.success('Financial period unlocked successfully');
  };

  const handleAllocateBudget = (category: string, amount: string) => {
    if (!category || !amount) {
      toast.error('Please fill all budget fields');
      return;
    }
    toast.success(`Budget allocated for ${category}`);
  };

  const handleReconcile = (_itemId: string) => {
    toast.success('Reconciliation item marked as matched');
  };

  const handlePayVendor = (vendorName: string) => {
    toast.success(`Payment recorded for ${vendorName}`);
  };

  const handleImportStatement = () => {
    toast.info('Upload bank statement to auto-reconcile');
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
        <div className="text-center">
          <Loader2 className="w-12 h-12 text-blue-500 animate-spin mx-auto mb-4" />
          <p className="text-gray-500">Loading financial data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 p-4 md:p-6">
      <div className="max-w-7xl mx-auto">
        <FinanceFilters
          selectedYear={selectedYear}
          onYearChange={setSelectedYear}
          dateFrom={dateFrom}
          dateTo={dateTo}
          onDateFromChange={setDateFrom}
          onDateToChange={setDateTo}
          activeTab={activeTab}
          onTabChange={setActiveTab}
        />

        {/* Tab Content */}
        <div className="animate-fadeIn">
          {activeTab === 'revenue-pl' && (
            <FinanceSummary revenueData={revenueData} plStatement={plStatement} />
          )}
          {activeTab === 'gst' && <GSTPanel gstSummary={gstSummary} />}
          {activeTab === 'outstanding' && (
            <OutstandingPanel outstanding={outstanding} vendorPayments={vendorPayments} />
          )}
          {activeTab === 'cash-flow' && (
            <CashFlowPanel
              cashFlow={cashFlow}
              reconciliation={reconciliation}
              onReconcile={handleReconcile}
            />
          )}
          {activeTab === 'period' && (
            <PeriodManagement
              periodLocked={periodLocked}
              onLockPeriod={handleLockPeriod}
              onUnlockPeriod={handleUnlockPeriod}
            />
          )}
          {activeTab === 'budgets' && (
            <BudgetPanel
              budgets={budgets}
              selectedYear={selectedYear}
              onAllocateBudget={handleAllocateBudget}
            />
          )}
          {activeTab === 'vendor-payments' && (
            <VendorPayments vendorPayments={vendorPayments} onPayVendor={handlePayVendor} />
          )}
          {activeTab === 'reconciliation' && (
            <ReconciliationPanel
              reconciliation={reconciliation}
              onReconcile={handleReconcile}
              onImportStatement={handleImportStatement}
            />
          )}
        </div>
      </div>
    </div>
  );
}
