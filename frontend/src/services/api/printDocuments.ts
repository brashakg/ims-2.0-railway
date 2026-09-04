// ============================================================================
// IMS 2.0 - Print Documents API (Delivery Challan)
// ============================================================================
// Server-side HTML render endpoints for the Rule 55 Delivery Challan, for a
// sales order or an inter-store transfer. The endpoints are JWT-protected and
// return text/html, so we fetch the page with the auth header attached and
// open it in a new tab for printing.
//
// NOTE: import this module DIRECTLY (`from '../../services/api/printDocuments'`).
// The barrel re-export (services/api/index.ts) can fail to resolve for newly
// added modules (TS2614) -- a known gotcha in this repo.

import { getSecureApiUrl } from './client';

async function _openHtml(path: string): Promise<void> {
  const token = localStorage.getItem('ims_token');
  const url = `${getSecureApiUrl()}${path}`;
  const res = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) {
    throw new Error(`Failed to render document (${res.status})`);
  }
  const html = await res.text();
  const win = window.open('', '_blank');
  if (win) {
    win.document.open();
    win.document.write(html);
    win.document.close();
  }
}

/**
 * Open a server-rendered PDF in a new tab.
 *
 * THE STATUTORY INVOICE IS THE SERVER'S DOCUMENT AND NOTHING ELSE. Three
 * client-side "Tax Invoice" renderers existed in this app and every one of them
 * put a number on paper that was not the serial in the books: the POS GSTInvoice
 * modal invented a BV/FY/store number outright, the receipt preview's A4 tab
 * hard-coded 9/9/18 tax labels, and the Orders screen labelled the ORDER number
 * as "Invoice:". All three are retired. A GST invoice serial must be a
 * consecutive series per financial year, minted once by the server and recorded
 * -- so it can only come from the server.
 *
 * This lives here, next to the challan opener, because the same fetch-and-open
 * had already been written twice; a third copy on the Orders screen would be
 * exactly the one-rule-two-implementations class this repo keeps being bitten by.
 */
async function _openPdf(path: string): Promise<void> {
  const { default: api } = await import('./client');
  let res;
  try {
    res = await api.get(path, { responseType: 'blob' });
  } catch (err: any) {
    // An error body on a blob request arrives as a BLOB, so the server's detail
    // is not on err.response.data.detail and every existing copy of this call
    // gave up and showed a fixed "check the store GSTIN" line. That misdirects:
    // this door also refuses a DRAFT order outright, and the shops would have
    // been sent to the settings screen to fix a GSTIN that was never the
    // problem. Read the blob back so the operator gets the real reason.
    const body = err?.response?.data;
    if (body instanceof Blob) {
      try {
        const detail = JSON.parse(await body.text())?.detail;
        if (typeof detail === 'string') throw new Error(detail);
      } catch (parsed: any) {
        if (parsed?.message) throw parsed;
      }
    }
    throw new Error('Could not build the document. Check the store GSTIN in settings.');
  }
  const url = URL.createObjectURL(
    new Blob([res.data as BlobPart], { type: 'application/pdf' }),
  );
  const win = window.open(url, '_blank');
  if (!win) {
    throw new Error('Allow pop-ups for this site to open the printable document.');
  }
  // Keep the blob alive long enough for the new tab to load it.
  window.setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export const printDocumentsApi = {
  /**
   * The GST tax invoice for an order -- the ONE statutory document.
   * Server-rendered from persisted line values (backend invoice_pdf.py), so
   * nothing is laid out, totalled or numbered on the client.
   */
  async openOrderInvoice(orderId: string): Promise<void> {
    await _openPdf(`/orders/${orderId}/invoice.pdf`);
  },

  // Open the delivery challan for a sales order (goods moving to the customer).
  async openOrderChallan(
    orderId: string,
    copy: 'ORIGINAL' | 'DUPLICATE' | 'TRIPLICATE' = 'ORIGINAL',
  ): Promise<void> {
    await _openHtml(
      `/print/delivery-challan/order/${orderId}?copy=${encodeURIComponent(copy)}`,
    );
  },

  // Open the delivery challan for an inter-store stock transfer.
  async openTransferChallan(
    transferId: string,
    copy: 'ORIGINAL' | 'DUPLICATE' | 'TRIPLICATE' = 'ORIGINAL',
  ): Promise<void> {
    await _openHtml(
      `/print/delivery-challan/transfer/${transferId}?copy=${encodeURIComponent(copy)}`,
    );
  },
};

export default printDocumentsApi;
