import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: { roles: ['SUPERADMIN'] } }) }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('../../../services/api', () => ({
  adminBrandApi: { getBrands: vi.fn(async () => ({ brands: [] })) },
  adminDiscountApi: {},
}));
import { BrandSection } from '../SettingsStore';

describe('brand website switches', () => {
  it('unticks and disables sync on block; unblocking leaves sync off until chosen', async () => {
    render(<MemoryRouter><BrandSection /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Add Brand' }));
    const sync = screen.getByRole('checkbox', { name: 'Sync to Shopify by default' });
    const block = screen.getByRole('checkbox', { name: 'Never sell online' });
    fireEvent.click(sync);
    expect(sync).toBeChecked();
    fireEvent.click(block);
    expect(sync).not.toBeChecked();
    expect(sync).toBeDisabled();
    expect(screen.getByText('Website sync is off because Never sell online is on.')).toBeInTheDocument();
    fireEvent.click(block);
    expect(sync).toBeEnabled();
    expect(sync).not.toBeChecked();
    fireEvent.click(sync);
    expect(sync).toBeChecked();
  });
});
