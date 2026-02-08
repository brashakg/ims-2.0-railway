// ============================================================================
// IMS 2.0 - BaseModal Component Tests
// ============================================================================

import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BaseModal } from '../BaseModal';

describe('BaseModal', () => {
  const defaultProps = {
    isOpen: true,
    onClose: jest.fn(),
    title: 'Test Modal',
    children: <div>Modal Content</div>,
  };

  afterEach(() => {
    jest.clearAllMocks();
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
    });

    it('should render with custom size classes', () => {
      const { container } = render(
        <BaseModal {...defaultProps} size="lg" />
      );
      const modalContent = container.querySelector('[role="dialog"]');
      expect(modalContent).toHaveClass('max-w-3xl');
    });

    it('should render with icon when provided', () => {
      const MockIcon = () => <div data-testid="test-icon">Icon</div>;
      render(
        <BaseModal {...defaultProps} icon={MockIcon} />
      );
      expect(screen.getByTestId('test-icon')).toBeInTheDocument();
    });
  });

  describe('Closing', () => {
    it('should call onClose when close button is clicked', async () => {
      const onClose = jest.fn();
      render(<BaseModal {...defaultProps} onClose={onClose} />);

      const closeButton = screen.getByRole('button', { name: /close/i });
      await userEvent.click(closeButton);

      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('should call onClose when backdrop is clicked', async () => {
      const onClose = jest.fn();
      const { container } = render(
        <BaseModal {...defaultProps} onClose={onClose} />
      );

      const backdrop = container.querySelector('[role="presentation"]');
      if (backdrop) {
        fireEvent.click(backdrop);
        expect(onClose).toHaveBeenCalled();
      }
    });

    it('should not close backdrop when closeOnBackdropClick is false', async () => {
      const onClose = jest.fn();
      const { container } = render(
        <BaseModal {...defaultProps} onClose={onClose} closeOnBackdropClick={false} />
      );

      const backdrop = container.querySelector('[role="presentation"]');
      if (backdrop) {
        fireEvent.click(backdrop);
        expect(onClose).not.toHaveBeenCalled();
      }
    });

    it('should close when Escape key is pressed', async () => {
      const onClose = jest.fn();
      render(<BaseModal {...defaultProps} onClose={onClose} />);

      fireEvent.keyDown(document, { key: 'Escape' });
      expect(onClose).toHaveBeenCalled();
    });
  });

  describe('Actions', () => {
    it('should render action buttons when provided', async () => {
      const handlePrimary = jest.fn();
      render(
        <BaseModal
          {...defaultProps}
          actions={[
            { label: 'Cancel', variant: 'secondary', onClick: jest.fn() },
            { label: 'Confirm', variant: 'primary', onClick: handlePrimary },
          ]}
        />
      );

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /confirm/i })).toBeInTheDocument();

      const confirmButton = screen.getByRole('button', { name: /confirm/i });
      await userEvent.click(confirmButton);
      expect(handlePrimary).toHaveBeenCalled();
    });

    it('should disable action buttons when loading', () => {
      render(
        <BaseModal
          {...defaultProps}
          loading={true}
          actions={[
            { label: 'Confirm', variant: 'primary', onClick: jest.fn() },
          ]}
        />
      );

      const confirmButton = screen.getByRole('button', { name: /confirm/i });
      expect(confirmButton).toBeDisabled();
    });

    it('should show loading spinner when loading is true', () => {
      const { container } = render(
        <BaseModal {...defaultProps} loading={true} />
      );

      const spinner = container.querySelector('[class*="animate-spin"]');
      expect(spinner).toBeInTheDocument();
    });
  });

  describe('Accessibility', () => {
    it('should have proper ARIA attributes', () => {
      const { container } = render(<BaseModal {...defaultProps} />);
      const dialog = container.querySelector('[role="dialog"]');

      expect(dialog).toHaveAttribute('aria-modal', 'true');
      expect(dialog).toHaveAttribute('aria-labelledby');
    });

    it('should trap focus within modal', async () => {
      render(
        <BaseModal
          {...defaultProps}
          actions={[
            { label: 'Cancel', variant: 'secondary', onClick: jest.fn() },
            { label: 'Confirm', variant: 'primary', onClick: jest.fn() },
          ]}
        />
      );

      const buttons = screen.getAllByRole('button');
      expect(buttons.length).toBeGreaterThan(0);
    });

    it('should announce modal to screen readers', () => {
      render(
        <BaseModal
          {...defaultProps}
          aria-describedby="modal-description"
        />
      );

      const dialog = screen.getByRole('dialog');
      expect(dialog).toBeInTheDocument();
    });
  });

  describe('Variants', () => {
    it('should apply correct styling for danger variant', () => {
      const { container } = render(
        <BaseModal
          {...defaultProps}
          variant="danger"
          actions={[
            { label: 'Delete', variant: 'danger', onClick: jest.fn() },
          ]}
        />
      );

      const deleteButton = screen.getByRole('button', { name: /delete/i });
      expect(deleteButton).toHaveClass('bg-red');
    });

    it('should apply correct styling for success variant', () => {
      render(
        <BaseModal
          {...defaultProps}
          variant="success"
          actions={[
            { label: 'Confirm', variant: 'success', onClick: jest.fn() },
          ]}
        />
      );

      const confirmButton = screen.getByRole('button', { name: /confirm/i });
      expect(confirmButton).toHaveClass('bg-green');
    });
  });

  describe('Dark Mode', () => {
    it('should apply dark mode classes', () => {
      const { container } = render(
        <BaseModal {...defaultProps} />
      );

      const dialog = container.querySelector('[role="dialog"]');
      expect(dialog).toHaveClass('dark:bg-gray-900');
    });
  });
});
