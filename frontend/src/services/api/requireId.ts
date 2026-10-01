// A write must never go to /purchase-invoices/undefined/...: no id is an error.
export function requireInvoiceId(id: unknown): asserts id is string {
  if (!id || typeof id !== 'string' || id === 'undefined' || id === 'null') {
    throw new Error('This invoice has no id, so nothing was sent. Reload the list and try again.');
  }
}
