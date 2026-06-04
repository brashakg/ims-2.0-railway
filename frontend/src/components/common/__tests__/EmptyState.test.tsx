import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Inbox } from 'lucide-react';
import { EmptyState } from '../EmptyState';

describe('EmptyState', () => {
  it('renders the title and description text', () => {
    render(<EmptyState title="No customers yet" description="Add your first customer to begin." />);
    expect(screen.getByRole('heading', { name: 'No customers yet' })).toBeInTheDocument();
    expect(screen.getByText('Add your first customer to begin.')).toBeInTheDocument();
  });

  it('does not render an action button when no action is given', () => {
    render(<EmptyState title="Empty" description="Nothing here." />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders the action button and fires onClick', () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        icon={Inbox}
        title="Empty"
        description="Nothing here."
        action={{ label: 'Add item', onClick }}
      />,
    );
    const btn = screen.getByRole('button', { name: /add item/i });
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
