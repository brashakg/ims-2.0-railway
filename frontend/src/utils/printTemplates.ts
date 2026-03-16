// ============================================================================
// IMS 2.0 — Print Templates (Barcode, Prescription, Invoice)
// ============================================================================

// BARCODE LABEL — Thermal printer 50x25mm
export function printBarcodeLabel(product: {
  name: string; sku: string; barcode: string; mrp: number;
  offerPrice?: number; location?: string; brand?: string;
}) {
  const w = window.open('', '_blank', 'width=400,height=300');
  if (!w) return;
  w.document.write(`<!DOCTYPE html><html><head><title>Barcode ${product.barcode}</title>
<style>
  @page { size: 50mm 25mm; margin: 0; }
  body { margin: 0; padding: 2mm; font-family: Arial, sans-serif; width: 50mm; height: 25mm; }
  .name { font-size: 7pt; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 46mm; }
  .brand { font-size: 6pt; color: #666; }
  .barcode-img { height: 10mm; margin: 1mm 0; }
  .barcode-num { font-size: 7pt; font-family: monospace; letter-spacing: 1px; }
  .price-row { display: flex; justify-content: space-between; align-items: baseline; }
  .mrp { font-size: 9pt; font-weight: bold; }
  .offer { font-size: 7pt; color: #c00; text-decoration: line-through; }
  .loc { font-size: 5pt; color: #999; }
  @media print { button { display: none; } }
</style></head><body>
  <div class="name">${product.name}</div>
  ${product.brand ? `<div class="brand">${product.brand}</div>` : ''}
  <img class="barcode-img" src="https://barcodeapi.org/api/128/${product.barcode}" alt="${product.barcode}" onerror="this.style.display='none'" />
  <div class="barcode-num">${product.barcode}</div>
  <div class="price-row">
    <span class="mrp">₹${Math.round(product.mrp).toLocaleString('en-IN')}</span>
    ${product.offerPrice && product.offerPrice < product.mrp ? `<span class="offer">₹${Math.round(product.offerPrice)}</span>` : ''}
  </div>
  ${product.location ? `<div class="loc">${product.location}</div>` : ''}
  <button onclick="window.print()" style="margin-top:5px;padding:3px 10px;font-size:10px;cursor:pointer">Print</button>
</body></html>`);
  w.document.close();
}

// PRESCRIPTION PRINT — A4
export function printPrescription(rx: {
  patientName: string; patientPhone?: string; date: string;
  optometrist?: string; source?: string; remarks?: string; validity?: string;
  rightEye: { sphere?: number; cylinder?: number; axis?: number; add?: number; pd?: string; acuity?: string };
  leftEye: { sphere?: number; cylinder?: number; axis?: number; add?: number; pd?: string; acuity?: string };
}, storeInfo?: { name: string; address: string; phone: string }) {
  const store = storeInfo || { name: 'Better Vision Opticals', address: '', phone: '' };
  const fmt = (v?: number) => v !== undefined && v !== null ? (v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2)) : '-';
  const w = window.open('', '_blank', 'width=800,height=600');
  if (!w) return;
  w.document.write(`<!DOCTYPE html><html><head><title>Rx - ${rx.patientName}</title>
<style>
  @page { size: A4; margin: 15mm; }
  body { font-family: 'Segoe UI', Arial, sans-serif; padding: 20px; max-width: 700px; margin: 0 auto; color: #333; }
  .header { text-align: center; border-bottom: 2px solid #c5a55a; padding-bottom: 15px; margin-bottom: 20px; }
  .header h1 { margin: 0; color: #cd201a; font-size: 22px; }
  .header p { margin: 2px 0; font-size: 12px; color: #666; }
  .rx-symbol { font-size: 28px; font-weight: bold; color: #c5a55a; margin: 10px 0; }
  .patient-info { display: flex; justify-content: space-between; margin-bottom: 15px; font-size: 13px; }
  .patient-info strong { color: #000; }
  table { width: 100%; border-collapse: collapse; margin: 15px 0; }
  th, td { border: 1px solid #ddd; padding: 10px 12px; text-align: center; font-size: 13px; }
  th { background: #f8f4ef; color: #333; font-weight: 600; }
  td.eye-label { font-weight: bold; background: #fafafa; text-align: left; width: 40px; }
  .remarks { margin-top: 15px; padding: 10px; background: #f9f9f9; border-radius: 4px; font-size: 12px; }
  .footer { margin-top: 40px; display: flex; justify-content: space-between; font-size: 11px; color: #999; }
  .sig-line { border-top: 1px solid #333; padding-top: 5px; width: 200px; text-align: center; font-size: 12px; }
  @media print { button { display: none; } }
</style></head><body>
  <div class="header">
    <h1>${store.name}</h1>
    <p>${store.address}</p>
    <p>${store.phone}</p>
  </div>
  <div class="rx-symbol">℞</div>
  <div class="patient-info">
    <div><strong>Patient:</strong> ${rx.patientName}</div>
    <div><strong>Phone:</strong> ${rx.patientPhone || '-'}</div>
    <div><strong>Date:</strong> ${new Date(rx.date).toLocaleDateString('en-IN')}</div>
  </div>
  ${rx.source ? `<div style="font-size:12px;color:#666;margin-bottom:10px">Source: ${rx.source === 'FROM_STORE' ? 'Tested at store' : 'External Doctor'}</div>` : ''}
  <table>
    <thead>
      <tr><th></th><th>SPH</th><th>CYL</th><th>AXIS</th><th>ADD</th><th>PD</th><th>VA</th></tr>
    </thead>
    <tbody>
      <tr>
        <td class="eye-label">R</td>
        <td>${fmt(rx.rightEye.sphere)}</td><td>${fmt(rx.rightEye.cylinder)}</td>
        <td>${rx.rightEye.axis ?? '-'}</td><td>${fmt(rx.rightEye.add)}</td>
        <td>${rx.rightEye.pd || '-'}</td><td>${rx.rightEye.acuity || '-'}</td>
      </tr>
      <tr>
        <td class="eye-label">L</td>
        <td>${fmt(rx.leftEye.sphere)}</td><td>${fmt(rx.leftEye.cylinder)}</td>
        <td>${rx.leftEye.axis ?? '-'}</td><td>${fmt(rx.leftEye.add)}</td>
        <td>${rx.leftEye.pd || '-'}</td><td>${rx.leftEye.acuity || '-'}</td>
      </tr>
    </tbody>
  </table>
  ${rx.remarks ? `<div class="remarks"><strong>Remarks:</strong> ${rx.remarks}</div>` : ''}
  ${rx.validity ? `<div style="margin-top:10px;font-size:12px;color:#666">Validity: ${rx.validity}</div>` : ''}
  <div style="margin-top:50px;display:flex;justify-content:flex-end">
    <div class="sig-line">${rx.optometrist || 'Optometrist'}</div>
  </div>
  <div class="footer">
    <span>Printed: ${new Date().toLocaleString('en-IN')}</span>
    <span>${store.name}</span>
  </div>
  <button onclick="window.print()" style="display:block;margin:20px auto;padding:10px 30px;background:#c5a55a;color:white;border:none;border-radius:8px;cursor:pointer">Print</button>
</body></html>`);
  w.document.close();
}

// GST INVOICE — Full A4 with HSN, CGST/SGST split
export function printGSTInvoice(order: {
  orderNumber: string; createdAt: string;
  customerName: string; customerPhone?: string; customerAddress?: string;
  customerGST?: string;
  items: Array<{
    name: string; sku?: string; hsn?: string; qty: number;
    rate: number; discount?: number; gstRate: number; total: number;
  }>;
  subtotal: number; totalDiscount: number; taxAmount: number;
  grandTotal: number; amountPaid: number; balanceDue: number;
  payments: Array<{ method: string; amount: number; reference?: string }>;
}, store: {
  name: string; address: string; phone: string; gst: string; state: string; stateCode: string;
}) {
  const items = order.items.map((item, i) => {
    const taxable = Math.round((item.total - (item.discount || 0)));
    const cgst = Math.round(taxable * (item.gstRate / 200) * 100) / 100;
    const sgst = cgst;
    return `<tr>
      <td>${i + 1}</td><td>${item.name}${item.sku ? `<br><small style="color:#999">${item.sku}</small>` : ''}</td>
      <td>${item.hsn || '-'}</td><td>${item.qty}</td>
      <td style="text-align:right">₹${Math.round(item.rate).toLocaleString('en-IN')}</td>
      <td style="text-align:right">${item.discount ? `₹${Math.round(item.discount).toLocaleString('en-IN')}` : '-'}</td>
      <td style="text-align:right">₹${taxable.toLocaleString('en-IN')}</td>
      <td style="text-align:center">${item.gstRate / 2}%</td><td style="text-align:right">₹${cgst.toLocaleString('en-IN')}</td>
      <td style="text-align:center">${item.gstRate / 2}%</td><td style="text-align:right">₹${sgst.toLocaleString('en-IN')}</td>
      <td style="text-align:right;font-weight:bold">₹${Math.round(taxable + cgst + sgst).toLocaleString('en-IN')}</td>
    </tr>`;
  }).join('');

  const payments = order.payments.map(p => 
    `<span>${p.method}: ₹${Math.round(p.amount).toLocaleString('en-IN')}${p.reference ? ` (${p.reference})` : ''}</span>`
  ).join(' &nbsp;|&nbsp; ');

  const w = window.open('', '_blank', 'width=900,height=700');
  if (!w) return;
  w.document.write(`<!DOCTYPE html><html><head><title>Invoice ${order.orderNumber}</title>
<style>
  @page { size: A4; margin: 10mm; }
  body { font-family: Arial, sans-serif; padding: 10px; font-size: 11px; color: #333; max-width: 800px; margin: 0 auto; }
  .inv-header { display: flex; justify-content: space-between; border-bottom: 2px solid #cd201a; padding-bottom: 10px; margin-bottom: 10px; }
  .inv-header h2 { margin: 0; color: #cd201a; }
  .inv-header .right { text-align: right; }
  .inv-header .right p { margin: 2px 0; }
  .party-row { display: flex; justify-content: space-between; margin-bottom: 10px; }
  .party-box { width: 48%; padding: 8px; background: #f9f9f9; border-radius: 4px; }
  .party-box h4 { margin: 0 0 5px; font-size: 11px; color: #666; text-transform: uppercase; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 10px; }
  th, td { border: 1px solid #ddd; padding: 5px 6px; }
  th { background: #f5f0e8; font-weight: 600; text-align: center; }
  .summary { float: right; width: 280px; margin-top: 10px; }
  .summary div { display: flex; justify-content: space-between; padding: 3px 0; }
  .summary .grand { font-size: 14px; font-weight: bold; border-top: 2px solid #333; padding-top: 5px; margin-top: 5px; }
  .amt-words { margin: 10px 0; padding: 5px; background: #f9f9f9; font-style: italic; font-size: 10px; }
  .footer-row { display: flex; justify-content: space-between; margin-top: 30px; font-size: 10px; }
  .sig { border-top: 1px solid #333; padding-top: 3px; width: 180px; text-align: center; }
  @media print { button { display: none; } }
</style></head><body>
  <div class="inv-header">
    <div>
      <h2>${store.name}</h2>
      <p>${store.address}</p>
      <p>Ph: ${store.phone}</p>
      <p><strong>GSTIN:</strong> ${store.gst}</p>
      <p><strong>State:</strong> ${store.state} (${store.stateCode})</p>
    </div>
    <div class="right">
      <h3 style="margin:0;color:#333">TAX INVOICE</h3>
      <p><strong>Invoice No:</strong> ${order.orderNumber}</p>
      <p><strong>Date:</strong> ${new Date(order.createdAt).toLocaleDateString('en-IN')}</p>
    </div>
  </div>
  <div class="party-row">
    <div class="party-box">
      <h4>Bill To</h4>
      <p><strong>${order.customerName}</strong></p>
      <p>${order.customerPhone || ''}</p>
      <p>${order.customerAddress || ''}</p>
      ${order.customerGST ? `<p><strong>GSTIN:</strong> ${order.customerGST}</p>` : ''}
    </div>
  </div>
  <table>
    <thead>
      <tr><th>#</th><th>Item</th><th>HSN</th><th>Qty</th><th>Rate</th><th>Disc</th><th>Taxable</th><th colspan="2">CGST</th><th colspan="2">SGST</th><th>Total</th></tr>
    </thead>
    <tbody>${items}</tbody>
  </table>
  <div style="overflow:hidden">
    <div class="summary">
      <div><span>Subtotal:</span><span>₹${Math.round(order.subtotal).toLocaleString('en-IN')}</span></div>
      ${order.totalDiscount > 0 ? `<div><span>Discount:</span><span>-₹${Math.round(order.totalDiscount).toLocaleString('en-IN')}</span></div>` : ''}
      <div><span>Tax (CGST+SGST):</span><span>₹${Math.round(order.taxAmount).toLocaleString('en-IN')}</span></div>
      <div class="grand"><span>Grand Total:</span><span>₹${Math.round(order.grandTotal).toLocaleString('en-IN')}</span></div>
      <div><span>Paid:</span><span>₹${Math.round(order.amountPaid).toLocaleString('en-IN')}</span></div>
      ${order.balanceDue > 0 ? `<div style="color:red"><span>Balance Due:</span><span>₹${Math.round(order.balanceDue).toLocaleString('en-IN')}</span></div>` : ''}
    </div>
  </div>
  <div style="clear:both"></div>
  ${payments ? `<div style="margin-top:10px;font-size:10px"><strong>Payment:</strong> ${payments}</div>` : ''}
  <div class="footer-row">
    <div style="font-size:10px;color:#666">Thank you for shopping with ${store.name}</div>
    <div class="sig">Authorized Signatory</div>
  </div>
  <button onclick="window.print()" style="display:block;margin:20px auto;padding:10px 30px;background:#c5a55a;color:white;border:none;border-radius:8px;cursor:pointer;font-size:12px">Print Invoice</button>
</body></html>`);
  w.document.close();
}
