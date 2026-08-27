// ============================================================================
// IMS 2.0 - Print previews must open ABOVE the popup that launched them
// ============================================================================
// The shared print folder's seven previews sit at z-[70] so they clear the
// z-50/z-60 detail popups that open them (owner report: "the preview comes
// behind the section and is not useable"). Three previews live OUTSIDE that
// folder and were missed in the sweep: the thermal LabelPreviewModal, the
// clinical PrescriptionPrint card, and the POS ReceiptPreview. These pin each
// one's overlay to the same shared layer.
//
// (POSInvoice is deliberately NOT covered here -- POS changes are owner-gated.)

import { render } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

vi.mock('../../services/api/labels', () => ({
  labelsApi: {
    getJobLabel: vi.fn().mockResolvedValue(null),
    getProductLabel: vi.fn().mockResolvedValue({ barcode_value: 'X0001' }),
  },
}));

import { LabelPreviewModal } from '../labels/LabelPreviewModal';
import { PrescriptionPrint } from '../clinical/PrescriptionPrint';
import { ReceiptPreview } from '../pos/ReceiptPreview';

/** The numeric z-index carried by a Tailwind class: `z-50` or `z-[70]`. */
function zLayer(el: HTMLElement): number {
  const hit = /(?:^|\s)z-(?:\[(\d+)\]|(\d+))(?:\s|$)/.exec(el.className);
  expect(hit, `no z-index class on: ${el.className}`).not.toBeNull();
  return Number(hit![1] ?? hit![2]);
}

function overlay(): HTMLElement {
  const el = document.querySelector<HTMLElement>('div.fixed.inset-0');
  expect(el, 'preview overlay not on screen').toBeTruthy();
  return el!;
}

// The detail popups these previews open on top of sit at z-50 (some at z-60).
const POPUP_LAYER = 60;

describe('print previews sit on the shared z-[70] layer', () => {
  it('LabelPreviewModal (workshop thermal label)', () => {
    render(
      <LabelPreviewModal
        spec={{ kind: 'product', productId: 'P1' }}
        onClose={vi.fn()}
      />,
    );
    expect(zLayer(overlay())).toBeGreaterThan(POPUP_LAYER);
  });

  it('PrescriptionPrint (clinical Rx card)', () => {
    render(
      <PrescriptionPrint
        prescription={{
          id: 'RX1',
          patientName: 'Test Patient',
          customerPhone: '9999999999',
          prescribedAt: '2026-08-25T10:00:00',
          rightEye: { sphere: -1, cylinder: null, axis: null, add: null },
          leftEye: { sphere: -1, cylinder: null, axis: null, add: null },
        }}
        store={{
          storeName: 'Better Vision',
          address: 'Main Road',
          city: 'Bokaro',
          state: 'Jharkhand',
          pincode: '827001',
        }}
        onClose={vi.fn()}
      />,
    );
    expect(zLayer(overlay())).toBeGreaterThan(POPUP_LAYER);
  });

  it('ReceiptPreview (POS receipt preview modal)', () => {
    render(
      <ReceiptPreview
        billData={{
          bill_number: 'B1',
          total_amount: 100,
          subtotal: 100,
          total_gst: 0,
          item_discount: 0,
          order_discount_amount: 0,
        }}
        selectedCustomer={{ name: 'Test Customer', phone: '9999999999' }}
        cartItems={[]}
        onClose={vi.fn()}
      />,
    );
    expect(zLayer(overlay())).toBeGreaterThan(POPUP_LAYER);
  });
});
