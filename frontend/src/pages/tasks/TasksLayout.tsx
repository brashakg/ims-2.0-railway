// ============================================================================
// IMS 2.0 - Tasks & SOPs module layout
// ============================================================================
// Wave 2 split. Before this there were TWO mega-pages at two addresses doing
// the same job with OPPOSITE permission lists - /tasks (TaskManagementPage,
// 4 tabs) and /tasks/checklists (TasksDashboard, 3 tabs) - seven tabs in
// useState between them and not one bookmarkable section. Both are deleted.
//
// Each section is now a real page with its own URL:
//   /tasks/mine        every operational role - only what is assigned to you
//   /tasks/team        managers and above (taskRoles.TEAM_TASK_ROLES)
//   /tasks/checklists  the daily SOP checklist. URL KEPT - the Hub links it.
//   /tasks/sops        the SOP library
//   /tasks/performance store-wide completion stats
//
// This layout keeps what was always on screen - the editorial header, the
// New task button and the section nav - and each section owns its own data.

import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { ListChecks, Plus, TrendingUp, User, Users, CheckSquare } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { NewTaskModal } from '../../components/tasks/NewTaskModal';
import { canSeeTeamTasks } from './taskRoles';
import { useQueryClient } from '@tanstack/react-query';

interface Section {
  path: string;
  label: string;
  icon: typeof User;
  /** Manager-and-above only, per the single list in taskRoles.ts. */
  managerOnly?: boolean;
}

const SECTIONS: Section[] = [
  { path: '/tasks/mine', label: 'Mine', icon: User },
  { path: '/tasks/team', label: 'Team', icon: Users, managerOnly: true },
  { path: '/tasks/checklists', label: 'Daily checklist', icon: CheckSquare },
  { path: '/tasks/sops', label: 'SOPs', icon: ListChecks, managerOnly: true },
  { path: '/tasks/performance', label: 'Performance', icon: TrendingUp, managerOnly: true },
];

export function TasksLayout() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);

  const canSeeTeam = canSeeTeamTasks(user?.roles);
  const sections = SECTIONS.filter((s) => !s.managerOnly || canSeeTeam);

  // Warm the sibling section chunks once the browser is idle, so the FIRST
  // click on any section renders without the lazy-chunk spinner (Wave 1
  // template). Vite dedupes these against the route-level lazy() imports.
  useEffect(() => {
    const idle: (cb: () => void) => void =
      'requestIdleCallback' in window
        ? (cb) => (window as Window & { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(cb)
        : (cb) => { setTimeout(cb, 1500); };
    idle(() => {
      void import('./TasksMinePage');
      void import('./TasksChecklistPage');
      if (canSeeTeam) {
        void import('./TasksTeamPage');
        void import('./TasksSopPage');
        void import('./TasksPerformancePage');
      }
    });
  }, [canSeeTeam]);

  const onChecklist = pathname.startsWith('/tasks/checklists');

  return (
    <div className="inv-body">
      <div className="inv-head">
        <div>
          <div className="eyebrow mb-1.5">Tasks &amp; SOPs</div>
          <h1>The shift, by priority.</h1>
          <div className="hint">
            P0-P4 priorities with live countdown timers, role-ladder auto-escalation,
            and the daily checklist that proves the shop opened correctly.
          </div>
        </div>
        <div className="row gap-2">
          {!onChecklist && (
            <button onClick={() => setShowCreate(true)} className="btn sm primary">
              <Plus className="w-4 h-4" /> New task
            </button>
          )}
        </div>
      </div>

      {/* Section nav - each entry is a real URL now, so every section is
          linkable and bookmarkable.
          ponytail: still <button>, not <NavLink>, because index.css styles
          `.inv-tabs button` by element - an <a> would render unstyled and
          index.css is outside this PR's blast radius. */}
      <div className="inv-tabs">
        {sections.map(({ path, label, icon: TabIcon }) => (
          <button
            key={path}
            onClick={() => navigate(path)}
            className={pathname === path ? 'on' : ''}
          >
            <TabIcon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      <Outlet />

      <NewTaskModal
        isOpen={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={() => queryClient.invalidateQueries({ queryKey: ['tasks'] })}
      />
    </div>
  );
}

export default TasksLayout;
