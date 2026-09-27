// ============================================================================
// IMS 2.0 - Expense Tracking & Approval System (re-export shim)
// ============================================================================
// Wave 6 split: the page's nine role-gated tabs are now one URL each under
// pages/finance/expenses/ (ExpensesLayout + one section file per tab). This
// path stays so every importer keeps working; it IS the layout.
export { ExpensesLayout as default } from './expenses/ExpensesLayout';
