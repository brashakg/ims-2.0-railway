// ============================================================================
// IMS 2.0 - /tasks/team
// ============================================================================
// Every task in the manager's store reach. Gated to TEAM_TASK_ROLES - the ONE
// role list, in taskRoles.ts (owner ruling 2026-09-03). The gate is on the
// route; this page holds no second opinion about who may open it.
//
// Who sees what is decided on the server (owner ruling 2026-10-12, backend
// services/task_visibility.py): a store manager gets every task of their
// shop(s), an area manager every task of their area's shops, an admin every
// task. Nothing here widens that.

import { TasksSplitView } from './TasksSplitView';

export function TasksTeamPage() {
  return (
    <TasksSplitView
      scope="team"
      emptyLine="No tasks in the shops you look after match these filters."
    />
  );
}

export default TasksTeamPage;
