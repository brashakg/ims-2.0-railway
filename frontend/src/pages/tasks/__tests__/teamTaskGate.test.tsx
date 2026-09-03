// ============================================================================
// WHO SEES TEAM TASKS - one answer, checked against every role
// ============================================================================
// Owner ruling 2026-09-03: STORE_MANAGER, AREA_MANAGER, ADMIN and SUPERADMIN.
// Everyone else sees only what is assigned to them.
//
// Three copies of this answer used to disagree (the /tasks route gate admitted
// ACCOUNTANT; the TasksDashboard tab admitted only the two managers;
// TaskManagementPage let ADMIN/SUPERADMIN see every task as "Mine"). They are
// deleted. This walks EVERY role in the app past the nav and asserts the one
// list decides - so a fourth opinion added anywhere shows up here as a role
// that sees the wrong thing.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { UserRole } from '../../../types';

let currentRoles: string[] = [];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'USR-1', activeStoreId: 'BV-PUN-01', roles: currentRoles } }),
}));
vi.mock('../../../components/tasks/NewTaskModal', () => ({ NewTaskModal: () => null }));

// TasksLayout warms the section chunks on idle (`void import('./TasksMinePage')`
// and friends) so switching sections is instant. That is a real feature and is
// left alone -- but in jsdom there is no requestIdleCallback, so the fallback
// timer fires those dynamic imports AFTER this test has finished and vitest has
// torn the environment down. The imports then fail to load and vitest reports
// unhandled errors, which fail the whole run with every test still passing.
//
// Stubbing the five modules makes each import() resolve from the mock registry
// instead of the loader, so nothing is in flight at teardown. Nothing is lost:
// this file asserts on the NAV the layout renders, and with no <Routes> around
// it the section pages never render anyway.
vi.mock('../TasksMinePage', () => ({ TasksMinePage: () => null }));
vi.mock('../TasksChecklistPage', () => ({ TasksChecklistPage: () => null }));
vi.mock('../TasksTeamPage', () => ({ TasksTeamPage: () => null }));
vi.mock('../TasksSopPage', () => ({ TasksSopPage: () => null }));
vi.mock('../TasksPerformancePage', () => ({ TasksPerformancePage: () => null }));

import { TasksLayout } from '../TasksLayout';
import { TEAM_TASK_ROLES, TASK_MODULE_ROLES, canSeeTeamTasks } from '../taskRoles';

function renderAs(role: string) {
  currentRoles = [role];
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/tasks/mine']}>
        <TasksLayout />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the one team-task role list', () => {
  it('is exactly the four the owner named', () => {
    expect([...TEAM_TASK_ROLES].sort()).toEqual(
      ['ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SUPERADMIN'],
    );
  });

  it('does not include ACCOUNTANT, who the old /tasks route gate let in', () => {
    expect(canSeeTeamTasks(['ACCOUNTANT'])).toBe(false);
  });

  it('covers the four floor roles for Mine and the daily checklist', () => {
    for (const role of ['SALES_STAFF', 'CASHIER', 'OPTOMETRIST', 'WORKSHOP_STAFF'] as UserRole[]) {
      expect(TASK_MODULE_ROLES).toContain(role);
      expect(canSeeTeamTasks([role])).toBe(false);
    }
  });

  it.each(TASK_MODULE_ROLES)('shows %s exactly the sections its role allows', (role) => {
    const expected = (TEAM_TASK_ROLES as readonly string[]).includes(role);
    renderAs(role);

    // Everyone who can open the module gets these two.
    expect(screen.getByRole('button', { name: /Mine/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Daily checklist/ })).toBeInTheDocument();

    for (const managerOnly of [/Team/, /SOPs/, /Performance/]) {
      const found = screen.queryByRole('button', { name: managerOnly });
      if (expected) expect(found).toBeInTheDocument();
      else expect(found).not.toBeInTheDocument();
    }
  });
});
