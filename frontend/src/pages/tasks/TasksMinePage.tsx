// ============================================================================
// IMS 2.0 - /tasks/mine
// ============================================================================
// Only what is assigned to the signed-in person. Open to every operational
// role (taskRoles.TASK_MODULE_ROLES) - sales staff, cashiers, optometrists and
// workshop staff do most of the tasks and had no Tasks link at all before this
// split.
//
// "Mine" is a SERVER query (assigned_to). It used to be a browser filter over
// the store's first 50 tasks, which meant that past 50 tasks a person could
// open this page and see none of their own work, an open P0 included.
// See tasksQueries.ts.

import { TasksSplitView } from './TasksSplitView';

export function TasksMinePage() {
  return (
    <TasksSplitView
      scope="mine"
      emptyLine="Nothing is assigned to you with these filters."
    />
  );
}

export default TasksMinePage;
