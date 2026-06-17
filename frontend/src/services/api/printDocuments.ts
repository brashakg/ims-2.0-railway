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

export const printDocumentsApi = {
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
