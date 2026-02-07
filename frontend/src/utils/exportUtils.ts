// ============================================================================
// IMS 2.0 - Export Utilities
// ============================================================================
// CSV/Excel export and print helpers for reports and data tables

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
      const str = String(val).replace(/"/g, '""');
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

/**
 * Format currency for export (plain number, no symbol)
 */
export function formatCurrencyForExport(amount: number): string {
  return amount.toFixed(2);
}

/**
 * Print a specific element by ID
 */
export function printElement(elementId: string): void {
  const element = document.getElementById(elementId);
  if (!element) return;

  const printWindow = window.open('', '_blank');
  if (!printWindow) return;

  printWindow.document.write(`
    <!DOCTYPE html>
    <html>
    <head>
      <title>Print</title>
      <style>
        body {
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          padding: 20px;
          color: #111;
        }
        table {
          width: 100%;
          border-collapse: collapse;
          margin: 10px 0;
        }
        th, td {
          border: 1px solid #ddd;
          padding: 8px;
          text-align: left;
          font-size: 12px;
        }
        th {
          background: #f5f5f5;
          font-weight: 600;
        }
        .text-right { text-align: right; }
        .font-bold { font-weight: 700; }
        h1, h2, h3 { margin: 0 0 10px 0; }
        @media print {
          body { padding: 0; }
        }
      </style>
    </head>
    <body>
      ${element.innerHTML}
    </body>
    </html>
  `);
  printWindow.document.close();
  printWindow.focus();
  printWindow.print();
  printWindow.close();
}

/**
 * Sales report export columns
 */
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
