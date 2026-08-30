// ============================================================================
// IMS 2.0 - Export Utilities
// ============================================================================
// CSV/Excel export and print helpers for reports and data tables

/**
 * Neutralize CSV / spreadsheet formula injection.
 *
 * Excel / Google Sheets treat a cell whose first character is `=`, `+`, `-`,
 * `@` (or a leading TAB / CR) as a formula. A malicious value such as
 * `=HYPERLINK(...)` or `=cmd|...` exported from user-entered fields (customer
 * name, notes, product name) then executes when the file is opened. Prefixing
 * the value with a single quote forces the spreadsheet to treat it as text.
 * Reference: OWASP "CSV Injection".
 */
export function neutralizeFormula(str: string): string {
  if (str.length > 0 && /^[=+\-@\t\r]/.test(str)) {
    return `'${str}`;
  }
  return str;
}

/**
 * Convert array of objects to CSV string
 */
export function toCSV(data: Record<string, any>[], columns?: { key: string; label: string }[]): string {
  if (data.length === 0) return '';

  const cols = columns || Object.keys(data[0]).map(key => ({ key, label: key }));
  const header = cols.map(c => `"${c.label}"`).join(',');
  const rows = data.map(row =>
    cols.map(c => {
      const val = row[c.key];
      if (val === null || val === undefined) return '""';
      // Neutralize formula injection BEFORE quote-escaping so the leading
      // single quote is preserved inside the quoted field.
      const str = neutralizeFormula(String(val)).replace(/"/g, '""');
      return `"${str}"`;
    }).join(',')
  );

  return [header, ...rows].join('\n');
}

/**
 * Download CSV string as a file
 */
export function downloadCSV(csvContent: string, filename: string): void {
  const BOM = '\uFEFF'; // UTF-8 BOM for Excel compatibility
  const blob = new Blob([BOM + csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${filename}_${new Date().toISOString().split('T')[0]}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Export data array as CSV file
 */
export function exportToCSV(
  data: Record<string, any>[],
  filename: string,
  columns?: { key: string; label: string }[]
): void {
  const csv = toCSV(data, columns);
  if (!csv) return;
  downloadCSV(csv, filename);
}

export const SALES_REPORT_COLUMNS = [
  { key: 'date', label: 'Date' },
  { key: 'orderNumber', label: 'Order Number' },
  { key: 'customerName', label: 'Customer' },
  { key: 'items', label: 'Items' },
  { key: 'subtotal', label: 'Subtotal (₹)' },
  { key: 'discount', label: 'Discount (₹)' },
  { key: 'tax', label: 'GST (₹)' },
  { key: 'total', label: 'Total (₹)' },
  { key: 'paymentMode', label: 'Payment Mode' },
  { key: 'status', label: 'Status' },
];

/**
 * Inventory report export columns
 */
export const INVENTORY_REPORT_COLUMNS = [
  { key: 'sku', label: 'SKU' },
  { key: 'name', label: 'Product Name' },
  { key: 'category', label: 'Category' },
  { key: 'brand', label: 'Brand' },
  { key: 'stock', label: 'Stock' },
  { key: 'reserved', label: 'Reserved' },
  { key: 'available', label: 'Available' },
  { key: 'mrp', label: 'MRP (₹)' },
  { key: 'costPrice', label: 'Cost Price (₹)' },
  { key: 'stockValue', label: 'Stock Value (₹)' },
];

/**
 * Customer report export columns
 */
export const CUSTOMER_REPORT_COLUMNS = [
  { key: 'name', label: 'Customer Name' },
  { key: 'phone', label: 'Phone' },
  { key: 'email', label: 'Email' },
  { key: 'totalPurchases', label: 'Total Purchases (₹)' },
  { key: 'orderCount', label: 'Order Count' },
  { key: 'lastVisit', label: 'Last Visit' },
  { key: 'segment', label: 'Segment' },
];

/**
 * GST report export columns
 */
export const GST_REPORT_COLUMNS = [
  { key: 'invoiceNumber', label: 'Invoice Number' },
  { key: 'invoiceDate', label: 'Invoice Date' },
  { key: 'customerName', label: 'Customer Name' },
  { key: 'gstin', label: 'GSTIN' },
  { key: 'hsnCode', label: 'HSN Code' },
  { key: 'taxableValue', label: 'Taxable Value (₹)' },
  { key: 'cgst', label: 'CGST (₹)' },
  { key: 'sgst', label: 'SGST (₹)' },
  { key: 'igst', label: 'IGST (₹)' },
  { key: 'totalTax', label: 'Total Tax (₹)' },
  { key: 'invoiceValue', label: 'Invoice Value (₹)' },
];
