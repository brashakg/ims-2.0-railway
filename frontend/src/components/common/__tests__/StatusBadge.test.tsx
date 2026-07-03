import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StatusBadge, StatusBadgeGroup, NumericBadge } from '../StatusBadge';

describe('StatusBadge', () => {
  it('renders the status text, capitalized with dashes turned to spaces by default', () => {
    render(<StatusBadge status="out-of-stock" />);
    expect(screen.getByText('Out of stock')).toBeInTheDocument();
  });

  it('renders raw text when capitalize is disabled', () => {
    render(<StatusBadge status="at-risk" capitalize={false} />);
    expect(screen.getByText('at-risk')).toBeInTheDocument();
  });

  it('exposes an accessible label but no live-region role by default', () => {
    const { container } = render(<StatusBadge status="active" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge).toHaveAttribute('aria-label', 'Status: active');
    // A reusable badge must NOT be an ARIA live region (role="status"), or
    // screen readers would announce every badge on the page. The aria-label
    // alone conveys the status. See StatusBadge.tsx for the rationale.
    expect(badge).not.toHaveAttribute('role');
  });

  it('applies the predefined subtle color for a known status', () => {
    const { container } = render(<StatusBadge status="active" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge.className).toContain('bg-green-100');
    expect(badge.className).toContain('text-green-700');
  });

  it('honors customColor over the automatic mapping', () => {
    const { container } = render(<StatusBadge status="active" customColor="bg-pink-500 text-white" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge.className).toContain('bg-pink-500');
    expect(badge.className).not.toContain('bg-green-100');
  });

  it('becomes a button and fires onClick when interactive', () => {
    const onClick = vi.fn();
    render(<StatusBadge status="pending" interactive onClick={onClick} />);
    const badge = screen.getByRole('button');
    fireEvent.click(badge);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe('StatusBadgeGroup', () => {
  it('renders one badge per status', () => {
    render(<StatusBadgeGroup statuses={['active', 'pending', 'shipped']} />);
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('Pending')).toBeInTheDocument();
    expect(screen.getByText('Shipped')).toBeInTheDocument();
  });
});

describe('NumericBadge', () => {
  it('shows the raw value and a label', () => {
    render(<NumericBadge value={7} label="orders" />);
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.getByText('orders')).toBeInTheDocument();
  });

  it('uses formatValue when provided', () => {
    render(<NumericBadge value={12000} formatValue={(v) => `₹${(v / 1000).toFixed(0)}k`} />);
    expect(screen.getByText('₹12k')).toBeInTheDocument();
  });
});
