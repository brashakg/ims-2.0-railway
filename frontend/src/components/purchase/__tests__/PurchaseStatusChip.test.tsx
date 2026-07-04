// ============================================================================
// IMS 2.0 - PurchaseStatusChip tests (owner-approved 5-word vocabulary)
// ============================================================================
// Pins the council-ruled status words (Phase 1) AND the graceful fallback for
// unmapped statuses (same lesson as statusBadge: never throw on a new status).

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PurchaseStatusChip, purchaseStatusVocab } from '../PurchaseStatusChip';

describe('PurchaseStatusChip — PO statuses', () => {
  it('maps DRAFT (pre-send) to "Ordered"', () => {
    render(<PurchaseStatusChip status="DRAFT" />);
    expect(screen.getByText('Ordered')).toBeInTheDocument();
  });

  it('maps SENT and ACKNOWLEDGED to "Sent"', () => {
    expect(purchaseStatusVocab('SENT', 'po')?.label).toBe('Sent');
    expect(purchaseStatusVocab('ACKNOWLEDGED', 'po')?.label).toBe('Sent');
  });

  it('maps PARTIAL / PARTIALLY_RECEIVED to "Box received"', () => {
    expect(purchaseStatusVocab('PARTIAL', 'po')?.label).toBe('Box received');
    expect(purchaseStatusVocab('PARTIALLY_RECEIVED', 'po')?.label).toBe('Box received');
  });

  it('maps RECEIVED to "On shelf"', () => {
    render(<PurchaseStatusChip status="RECEIVED" />);
    expect(screen.getByText('On shelf')).toBeInTheDocument();
  });
});

describe('PurchaseStatusChip — GRN + invoice statuses', () => {
  it('maps GRN PENDING to "Box received" and ACCEPTED to "On shelf"', () => {
    expect(purchaseStatusVocab('PENDING', 'grn')?.label).toBe('Box received');
    expect(purchaseStatusVocab('ACCEPTED', 'grn')?.label).toBe('On shelf');
  });

  it('maps booked/settled invoices to "Bill settled"', () => {
    expect(purchaseStatusVocab('BOOKED', 'invoice')?.label).toBe('Bill settled');
    expect(purchaseStatusVocab('SETTLED', 'invoice')?.label).toBe('Bill settled');
  });
});

describe('PurchaseStatusChip — fallback', () => {
  it('renders an unmapped status muted, without throwing', () => {
    expect(() => render(<PurchaseStatusChip status="CANCELLED" />)).not.toThrow();
    expect(screen.getByText('CANCELLED')).toBeInTheDocument();
  });

  it('renders "Unknown" for an empty status', () => {
    render(<PurchaseStatusChip status="" />);
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });
});
