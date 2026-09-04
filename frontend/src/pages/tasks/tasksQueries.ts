// ============================================================================
// IMS 2.0 - Tasks module data via React Query
// ============================================================================
// Wave 2 split of the two rival task mega-pages. Copies the Wave 1 template
// (pages/reports/reportsQueries.ts): ONE cache shared by the layout and every
// section page (5-min staleTime from the app QueryClient), so switching
// sections renders from cache instead of refetching.
//
// ---------------------------------------------------------------------------
// THE 50-TASK BUG, AND WHY THE FETCH CHANGED IN THIS PR
// ---------------------------------------------------------------------------
// Both old pages called GET /tasks with NO params. The backend defaults to
// `limit=50` (backend/api/routers/tasks.py:439), so they received the first 50
// rows of the store and then narrowed them IN THE BROWSER:
//
//   TasksDashboard.tsx:260   tasks.filter(t => t.assigned_to === user?.id)
//   TaskManagementPage.tsx:352 tasks.filter(t => t.assignedTo === user?.id || ...)
//
// Past 50 tasks in a shop, a person could open "Mine" and legitimately see
// NONE of their own work - including an open P0 - because none of their tasks
// happened to be in the store's first 50. Splitting the page without moving
// that filter would have made it strictly worse: each page would have filtered
// a different slice of the same 50.
//
// So the assignee filter is now a SERVER filter (`assigned_to`, an existing
// query param on the existing endpoint - no backend change), and the list
// pages through with useInfiniteQuery instead of trusting one page of 50.
// This is a behaviour change and it is declared as one.
// ---------------------------------------------------------------------------

import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { tasksApi } from '../../services/api';
// Direct module import, not the barrel (TS2614 trap noted in project memory).
import { adminStoreApi } from '../../services/api/stores';
import type { SopChecklist } from '../../services/api/hr';

/** Backend canonical priority. The whole module speaks P0-P4 now; the legacy
 *  URGENT/HIGH/MEDIUM/LOW ladder that sat on top of it is deleted. */
export type PCode = 'P0' | 'P1' | 'P2' | 'P3' | 'P4';

/** Backend canonical status (task_sla.canon_status). */
export type TaskStatus = 'OPEN' | 'IN_PROGRESS' | 'COMPLETED' | 'ESCALATED' | 'CANCELLED';

export const PRIORITY_META: Record<PCode, { label: string; sub: string }> = {
  P0: { label: 'P0 · Now', sub: 'Immediate' },
  P1: { label: 'P1 · < 30m', sub: 'Escalating' },
  P2: { label: 'P2 · Today', sub: 'Shift close' },
  P3: { label: 'P3 · Week', sub: 'Plannable' },
  P4: { label: 'P4 · Backlog', sub: 'Nice-to-have' },
};

export const STATUS_LABEL: Record<TaskStatus, string> = {
  OPEN: 'Open',
  IN_PROGRESS: 'In progress',
  COMPLETED: 'Completed',
  ESCALATED: 'Escalated',
  CANCELLED: 'Cancelled',
};

export interface Task {
  id: string;
  title: string;
  description: string;
  assignedTo: string;
  assignedToName: string;
  assignedBy: string;
  assignedByName: string;
  status: TaskStatus;
  pCode: PCode;
  sopId?: string;
  /** Minutes until due. Negative = overdue. Computed at map time. */
  dueInMin: number;
  dueDate: string;
  createdDate: string;
  completedDate?: string;
  storeId: string;
  category: string;
  completionNotes?: string;
  attachment?: { file_id?: string; filename?: string; mime_type?: string } | null;
}

// --- normalisation ---------------------------------------------------------
// ONE mapper. The two dead pages each had their own (with different status
// spellings and a different priority ladder), which is exactly the defect
// class that bites here: a rule written twice drifts and CI cannot see it.

function statusFor(raw: unknown): TaskStatus {
  const s = String(raw ?? '').trim().toUpperCase().replace(/[ -]/g, '_');
  if (s === 'OPEN' || s === 'PENDING') return 'OPEN';
  if (s === 'IN_PROGRESS' || s === 'INPROGRESS') return 'IN_PROGRESS';
  if (s === 'COMPLETED' || s === 'COMPLETE' || s === 'DONE') return 'COMPLETED';
  if (s === 'ESCALATED' || s === 'OVERDUE') return 'ESCALATED';
  if (s === 'CANCELLED' || s === 'CANCELED') return 'CANCELLED';
  return 'OPEN';
}

function pCodeFor(raw: unknown): PCode {
  const p = String(raw ?? '').trim().toUpperCase();
  if (p === 'P0') return 'P0';
  if (p === 'P1' || p === 'URGENT') return 'P1';
  if (p === 'P2' || p === 'HIGH') return 'P2';
  if (p === 'P3' || p === 'MEDIUM') return 'P3';
  return 'P4';
}

function minutesUntil(iso?: string): number {
  if (!iso) return Number.POSITIVE_INFINITY;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return Number.POSITIVE_INFINITY;
  return Math.round((d.getTime() - Date.now()) / 60000);
}

export function normaliseTask(t: Record<string, any>): Task {
  return {
    // The tasks API emits `task_id`; the single-task GET renames it to `id`.
    // Accept both (order_dto_rename_trap.md) - a bare `t.id` left the id empty
    // and Reassign 404'd.
    id: t.task_id || t.id || '',
    title: t.title || '',
    description: t.description || '',
    assignedTo: t.assigned_to || '',
    assignedToName: t.assigned_to_name || t.assigned_to || '',
    assignedBy: t.assigned_by || '',
    assignedByName: t.assigned_by_name || t.assigned_by || '',
    status: statusFor(t.status),
    pCode: pCodeFor(t.priority),
    sopId: t.sop_id || t.source?.sop_id,
    dueInMin: minutesUntil(t.due_at || t.due_date),
    dueDate: t.due_at ? String(t.due_at).split('T')[0] : '',
    createdDate: t.created_at ? String(t.created_at).split('T')[0] : '',
    completedDate: t.completed_at ? String(t.completed_at).split('T')[0] : undefined,
    storeId: t.store_id || '',
    category: t.category || 'ADHOC',
    completionNotes: t.completion_notes,
    attachment: t.attachment || null,
  };
}

// --- queries ---------------------------------------------------------------

/** Backend max (tasks.py: `limit: int = Query(50, ge=1, le=100)`). */
export const PAGE_SIZE = 100;

export interface TaskListParams {
  /** Server-side assignee filter. THE fix: "Mine" is a server query now. */
  assigned_to?: string;
  status?: TaskStatus;
  priority?: PCode;
  store_id?: string;
}

export interface TaskPage {
  tasks: Task[];
  /** Server-side count for the SAME filters - honest, not a page length. */
  total: number;
  skip: number;
}

/**
 * Paged task list. `data.pages` flattens to every row fetched so far; the
 * caller shows a Load more while `hasNextPage`.
 *
 * ponytail: useInfiniteQuery, not hand-rolled paging - React Query is already
 * a dependency and does the cursor bookkeeping.
 */
export function useTaskList(params: TaskListParams, enabled = true) {
  return useInfiniteQuery({
    queryKey: ['tasks', 'list', params] as const,
    enabled,
    initialPageParam: 0,
    queryFn: async ({ pageParam }): Promise<TaskPage> => {
      const res = await tasksApi.getTasks({ ...params, skip: pageParam, limit: PAGE_SIZE });
      return {
        tasks: (res?.tasks || []).map(normaliseTask),
        total: Number(res?.total ?? 0),
        skip: pageParam,
      };
    },
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + p.tasks.length, 0);
      // A short page means the server ran out, whatever `total` claims.
      if (last.tasks.length < PAGE_SIZE || loaded >= last.total) return undefined;
      return loaded;
    },
  });
}

/** Flatten the pages a `useTaskList` result has loaded. */
export function flattenTasks(data: { pages: TaskPage[] } | undefined): Task[] {
  return data?.pages.flatMap((p) => p.tasks) ?? [];
}

/** Store-wide status counts. Store-scoped on the server, so this is only ever
 *  shown to the roles allowed to see the whole store (see taskRoles.ts). */
export function useTaskSummary(storeId: string | undefined) {
  return useQuery({
    queryKey: ['tasks', 'summary', storeId ?? 'none'] as const,
    queryFn: () => tasksApi.getTaskSummary(storeId),
  });
}

export interface SopTemplate {
  id: string;
  title: string;
  description: string;
  category: string;
  frequency: string;
  estimatedTime: number;
  assignedRoles: string[];
  /** User ids of NAMED people this SOP is assigned to (owner 2026-09-03:
   *  "assign it to individuals like sameer, rupesh", not just job titles). */
  assignedUsers: string[];
  createdDate: string;
  lastUpdated: string;
  steps: { id: string; stepNumber: number; instruction: string; warning?: string }[];
}

/**
 * SOP templates as configured by the business.
 *
 * OWNER RULING 2026-09-03: when this comes back empty it comes back EMPTY.
 * The old TaskManagementPage answered an empty list by rendering four
 * hard-coded SOPs that looked exactly like real ones - among them a cash
 * reconciliation telling staff to verify a Rs 5,000 opening float and retain
 * Rs 5,000 overnight. In a live shop nobody can tell that from policy the
 * owner wrote. Deleted; the page shows an honest empty state instead.
 */
export function useSopTemplates(storeId: string | undefined) {
  return useQuery({
    queryKey: ['tasks', 'sop-templates', storeId ?? 'none'] as const,
    queryFn: async (): Promise<SopTemplate[]> => {
      const res = await tasksApi.getSopTemplates({ storeId, activeOnly: true });
      return (res?.templates || []).map((t) => ({
        id: t.template_id,
        title: t.title,
        description: t.description,
        category: t.category,
        frequency: t.frequency,
        estimatedTime: t.estimated_time,
        assignedRoles: t.assigned_roles || [],
        // Dropping this here is what made the SOP editor WIPE per-person
        // assignments on every edit-save (toForm hardcoded []).
        assignedUsers: t.assigned_users || [],
        createdDate: t.created_at?.slice(0, 10) || '',
        lastUpdated: t.updated_at?.slice(0, 10) || '',
        steps: (t.steps || []).map((s) => ({
          id: String(s.step_number),
          stepNumber: s.step_number,
          instruction: s.instruction,
          warning: s.warning,
        })),
      }));
    },
  });
}

/** One selectable person on the active store's staff. */
export interface StaffMember {
  userId: string;
  name: string;
  role?: string;
}

/**
 * Active staff of ONE store, so work can be assigned to people BY NAME.
 * REUSES adminStoreApi.getStoreUsers - the same client NewTaskModal,
 * SalespersonPicker and WalkoutIntake already call - never a second
 * staff-listing endpoint (one rule, two implementations).
 */
export function useStoreStaff(storeId: string | undefined) {
  return useQuery({
    queryKey: ['store-staff', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: async (): Promise<StaffMember[]> => {
      const resp: any = await adminStoreApi.getStoreUsers(storeId as string, { activeOnly: true });
      const list = resp?.users || resp || [];
      return (Array.isArray(list) ? list : [])
        .map((u: any): StaffMember => ({
          userId: u.user_id || u.id || '',
          name: u.name || u.full_name || u.username || u.user_id || '',
          role: Array.isArray(u.roles) ? u.roles[0] : u.role,
        }))
        .filter((s) => s.userId);
    },
  });
}

/** Today's completion state for one daily checklist. */
export function useSopChecklist(templateId: string | undefined) {
  return useQuery({
    queryKey: ['tasks', 'sop-checklist', templateId ?? 'none'] as const,
    enabled: !!templateId,
    queryFn: (): Promise<SopChecklist> => tasksApi.getSopChecklist(templateId as string),
  });
}
