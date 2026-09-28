// ============================================================================
// IMS 2.0 - Finance & Accounting Dashboard (compatibility path)
// ============================================================================
// Wave 6 split: the dashboard is now FinanceLayout (header, FY / date bar,
// section nav, the one loader) plus one page per section under
// /finance/dashboard. This path stays so existing importers keep working.
export { default } from './FinanceLayout';
