// ============================================================================
// IMS 2.0 - BaseModal Component Tests (migrated jest -> vitest)
// ============================================================================
// Asserts the REAL BaseModal API in src/components/common/BaseModal.tsx:
//   props: isOpen / onClose / title / icon / onSubmit / submitLabel /
//          submitVariant / isLoading / size / closeOnBackdropClick / hideCancel.
// The close button carries aria-label="Close modal"; the backdrop is the
// outermost div and closes on a click that lands on itself (target===currentTarget).

import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import { BaseModal } from '../BaseModal';

describe('BaseModal', () => {
  const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
    title: 'Test Modal',
    children: <div>Modal Content</div>,
  };

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('Rendering', () => {
    it('should render modal when isOpen is true', () => {
      render(<BaseModal {...defaultProps} />);
      expect(screen.getByText('Test Modal')).toBeInTheDocument();
      expect(screen.getByText('Modal Content')).toBeInTheDocument();
    });

    it('should not render modal when isOpen is false', () => {
      render(<BaseModal {...defaultProps} isOpen={false} />);
      expect(screen.queryByText('Test Modal')).not.toBeInTheDocument();
      expect(screen.queryByText('Modal Content')).not.toBeInTheDocument();
    });

    it('should apply the size class for the given size prop', () => {
      // MODAL_SIZES: sm->max-w-sm, md->max-w-md, lg->max-w-2xl, xl->max-w-4xl
      const { container } = render(<BaseModal {...defaultProps} size="lg" />);
      const panel = container.querySelector('.max-w-2xl');
      expect(panel).toBeInTheDocument();
    });

    it('should default to the md size class', () => {
      const { container } = render(<BaseModal {...defaultProps} />);
      expect(container.querySelector('.max-w-md')).toBeInTheDocument();
    });

    it('should render the icon when provided', () => {
      const MockIcon = ({ className }: { className?: string }) => (
        <div data-testid="test-icon" className={className}>Icon</div>
      );
      render(<BaseModal {...defaultProps} icon={MockIcon} />);
      expect(screen.getByTestId('test-icon')).toBeInTheDocument();
    });
  });

  describe('Closing', () => {
    it('should call onClose when the header close button is clicked', async () => {
      const onClose = vi.fn();
      render(<BaseModal {...defaultProps} onClose={onClose} />);

      // The header close button is labelled "Close modal".
      const closeButton = screen.getByRole('button', { name: /close modal/i });
      await userEvent.click(closeButton);

      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('should call onClose when the backdrop itself is clicked', () => {
      const onClose = vi.fn();
      const { container } = render(<BaseModal {...defaultProps} onClose={onClose} />);

      // Outermost div is the backdrop; clicking it (target===currentTarget) closes.
      const backdrop = container.firstChild as HTMLElement;
      fireEvent.click(backdrop);
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('should NOT call onClose when closeOnBackdropClick is false', () => {
      const onClose = vi.fn();
      const { container } = render(
        <BaseModal {...defaultProps} onClose={onClose} closeOnBackdropClick={false} />
      );

      const backdrop = container.firstChild as HTMLElement;
      fireEvent.click(backdrop);
      expect(onClose).not.toHaveBeenCalled();
    });

    it('should NOT call onClose when a click bubbles from inside the panel', () => {
      const onClose = vi.fn();
      render(<BaseModal {...defaultProps} onClose={onClose} />);

      // Clicking content does not satisfy target===currentTarget on the backdrop
      // (the inner panel stops propagation), so onClose must not fire.
      fireEvent.click(screen.getByText('Modal Content'));
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  describe('Footer / submit', () => {
    it('should render the submit button with its label and fire onSubmit', async () => {
      const onSubmit = vi.fn();
      render(
        <BaseModal {...defaultProps} onSubmit={onSubmit} submitLabel="Confirm" />
      );

      const submit = screen.getByRole('button', { name: /confirm/i });
      expect(submit).toBeInTheDocument();
      await userEvent.click(submit);
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    it('should default the submit label to "Save"', () => {
      render(<BaseModal {...defaultProps} onSubmit={vi.fn()} />);
      expect(screen.getByRole('button', { name: /^save$/i })).toBeInTheDocument();
    });

    it('should not render a submit button when onSubmit is omitted', () => {
      render(<BaseModal {...defaultProps} />);
      // Only the close + cancel buttons exist, no submit.
      expect(screen.queryByRole('button', { name: /save/i })).not.toBeInTheDocument();
    });

    it('should render the Cancel button by default and call onClose on click', async () => {
      const onClose = vi.fn();
      render(<BaseModal {...defaultProps} onClose={onClose} />);
      const cancel = screen.getByRole('button', { name: /^cancel$/i });
      await userEvent.click(cancel);
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('should hide the Cancel button when hideCancel is true', () => {
      render(<BaseModal {...defaultProps} hideCancel />);
      expect(screen.queryByRole('button', { name: /^cancel$/i })).not.toBeInTheDocument();
    });

    it('should hide the footer entirely when showFooter is false', () => {
      render(<BaseModal {...defaultProps} onSubmit={vi.fn()} showFooter={false} />);
      expect(screen.queryByRole('button', { name: /^cancel$/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /save/i })).not.toBeInTheDocument();
    });
  });

  describe('Loading state', () => {
    it('should disable submit and cancel buttons when isLoading is true', () => {
      render(
        <BaseModal {...defaultProps} onSubmit={vi.fn()} submitLabel="Confirm" isLoading />
      );
      expect(screen.getByRole('button', { name: /confirm/i })).toBeDisabled();
      expect(screen.getByRole('button', { name: /^cancel$/i })).toBeDisabled();
    });

    it('should render a spinner inside the submit button when loading', () => {
      const { container } = render(
        <BaseModal {...defaultProps} onSubmit={vi.fn()} isLoading />
      );
      expect(container.querySelector('.animate-spin')).toBeInTheDocument();
    });

    it('should not render a spinner when not loading', () => {
      const { container } = render(<BaseModal {...defaultProps} onSubmit={vi.fn()} />);
      expect(container.querySelector('.animate-spin')).not.toBeInTheDocument();
    });
  });

  describe('Submit variants', () => {
    it('should apply danger styling to the submit button for submitVariant="danger"', () => {
      render(
        <BaseModal {...defaultProps} onSubmit={vi.fn()} submitVariant="danger" submitLabel="Delete" />
      );
      const deleteBtn = screen.getByRole('button', { name: /delete/i });
      // SUBMIT_COLORS.danger = 'bg-red-600 hover:bg-red-700 text-white'
      expect(deleteBtn.className).toContain('bg-red-600');
    });

    it('should apply success styling to the submit button for submitVariant="success"', () => {
      render(
        <BaseModal {...defaultProps} onSubmit={vi.fn()} submitVariant="success" submitLabel="Confirm" />
      );
      const confirmBtn = screen.getByRole('button', { name: /confirm/i });
      // SUBMIT_COLORS.success = 'bg-green-600 hover:bg-green-700 text-white'
      expect(confirmBtn.className).toContain('bg-green-600');
    });

    it('should default the submit button to primary (blue) styling', () => {
      render(<BaseModal {...defaultProps} onSubmit={vi.fn()} submitLabel="Confirm" />);
      const confirmBtn = screen.getByRole('button', { name: /confirm/i });
      expect(confirmBtn.className).toContain('bg-blue-600');
    });
  });
});
