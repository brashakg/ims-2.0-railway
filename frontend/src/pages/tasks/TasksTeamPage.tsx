// ============================================================================
// IMS 2.0 - /tasks/team
// ============================================================================
// Every task in the manager's store reach. Gated to TEAM_TASK_ROLES - the ONE
// role list, in taskRoles.ts (owner ruling 2026-09-03). The gate is on the
// route; this page holds no second opinion about who may open it.
//
// Store scope is decided SERVER-side: GET /tasks constrains a store-scoped
// caller to their own stores plus global tasks, and lets cross-store roles
// see everything. Nothing here widens that.

import { TasksSplitView } from './TasksSplitView';

export function TasksTeamPage() {
  return (
    <TasksSplitView
      scope="team"
      emptyLine="No tasks in this store match these filters."
    />
  );
}

export default TasksTeamPage;
