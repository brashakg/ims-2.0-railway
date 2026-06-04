// ============================================================================
// IMS 2.0 - getStatusBadge tests
// ============================================================================
// The prod bug: the badge map lacked SENT / ACKNOWLEDGED / PARTIALLY_RECEIVED,
// so once a PO was sent the backend emitted a status the FE could not resolve,
// the old destructure of config[status] threw, and the app-level ErrorBoundary
// unmounted the entire Purchase tab. These tests pin the labels AND the
// graceful fallback for any unmapped/legacy status.

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { getStatusBadge } from '../statusBadge';
import type { POStatus } from '../purchaseTypes';

describe('getStatusBadge', () => {
  it('renders the correct label for SENT', () => {
    render(getStatusBadge('SENT' as POStatus));
    expect(screen.getByText('Sent')).toBeInTheDocument();
  });

  it('renders the correct label for ACKNOWLEDGED', () => {
    render(getStatusBadge('ACKNOWLEDGED' as POStatus));
    expect(screen.getByText('Acknowledged')).toBeInTheDocument();
  });

  it('renders the correct label for PARTIALLY_RECEIVED', () => {
    render(getStatusBadge('PARTIALLY_RECEIVED' as POStatus));
    expect(screen.getByText('Partially Received')).toBeInTheDocument();
  });

  it('also resolves the known DRAFT / RECEIVED / CANCELLED labels', () => {
    const { rerender } = render(getStatusBadge('DRAFT' as POStatus));
    expect(screen.getByText('Draft')).toBeInTheDocument();

    rerender(getStatusBadge('RECEIVED' as POStatus));
    expect(screen.getByText('Received')).toBeInTheDocument();

    rerender(getStatusBadge('CANCELLED' as POStatus));
    expect(screen.getByText('Cancelled')).toBeInTheDocument();
  });

  it('falls back to the raw status text for an unknown status without throwing', () => {
    // The whole point of the fix: an unmapped status must render, not crash.
    expect(() => render(getStatusBadge('SOME_NEW_STATUS' as unknown as POStatus))).not.toThrow();
    expect(screen.getByText('SOME_NEW_STATUS')).toBeInTheDocument();
  });

  it('falls back to "Unknown" when the status is empty/falsy', () => {
    expect(() => render(getStatusBadge('' as unknown as POStatus))).not.toThrow();
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });
});
