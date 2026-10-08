// CostCell: per-unit product cost is shown to the managers and never to counter
// staff (owner rulings 2026-09-28 / D7). The role list itself is held equal to
// the backend's cost_mask "product" context by a backend test.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { UserRole } from '../../../types';
import { CostCell } from '../CostCell';

let role: UserRole = 'ADMIN';
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    hasRole: (r: UserRole | UserRole[]) => (Array.isArray(r) ? r : [r]).includes(role),
  }),
}));

describe('CostCell', () => {
  beforeEach(() => {
    role = 'ADMIN';
  });

  it.each<UserRole>(['STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER', 'ACCOUNTANT', 'ADMIN'])(
    'shows the cost to %s',
    (r) => {
      role = r;
      render(<CostCell value={3173} />);
      expect(screen.getByText('₹3,173')).toBeTruthy();
    },
  );

  it.each<UserRole>(['SALES_STAFF', 'CASHIER', 'SALES_CASHIER', 'OPTOMETRIST', 'WORKSHOP_STAFF'])(
    'hides the cost from %s',
    (r) => {
      role = r;
      render(<CostCell value={3173} />);
      expect(screen.queryByText(/3,173/)).toBeNull();
      expect(screen.getByLabelText('not authorised')).toBeTruthy();
    },
  );
});
