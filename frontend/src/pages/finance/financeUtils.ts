// ============================================================================
// IMS 2.0 - Finance Dashboard Utilities
// ============================================================================
// generateSampleData (fabricated P&L / GST / receivables / cash-flow) was
// removed — the dashboard now loads real data from finance.py via
// services/api/finance.ts. Only the shared currency formatter remains here.

export const formatCurrency = (amount: number): string => {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
  }).format(amount);
};
