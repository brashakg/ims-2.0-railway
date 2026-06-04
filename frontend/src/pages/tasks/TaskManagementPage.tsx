// ============================================================================
// IMS 2.0 - Task & SOP Management System
// ============================================================================
// Coordinate 40 employees with tasks, SOPs, and accountability tracking

import { useEffect, useMemo, useState } from 'react';
import {
  CheckSquare,
  Plus,
  Search,
  User,
  Users,
  AlertTriangle,
  ListChecks,
  TrendingUp,
  Loader2,
  Eye,
  Edit,
  Zap,
  ArrowLeft,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { tasksApi } from '../../services/api';
import { SopEditorModal, type SopTemplateForm } from '../../components/tasks/SopEditorModal';

type TabType = 'my-tasks' | 'team-tasks' | 'sop' | 'analytics';
type TaskStatus = 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'OVERDUE';
type TaskPriority = 'LOW' | 'MEDIUM' | 'HIGH' | 'URGENT';
/** Tasks v2 (May-2026 redesign per docs/design/DELTAS.md) — backend
 * canonical priority is P0-P4. Mapped on load. */
type PCode = 'P0' | 'P1' | 'P2' | 'P3' | 'P4';

const PRIORITY_META: Record<PCode, { label: string; sub: string }> = {
  P0: { label: 'P0 · Now',     sub: 'Immediate' },
  P1: { label: 'P1 · < 30m',   sub: 'Escalating' },
  P2: { label: 'P2 · Today',   sub: 'Shift close' },
  P3: { label: 'P3 · Week',    sub: 'Plannable' },
  P4: { label: 'P4 · Backlog', sub: 'Nice-to-have' },
};

interface Task {
  id: string;
  title: string;
  description: string;
  assignedTo: string;
  assignedToName: string;
  assignedBy: string;
  assignedByName: string;
  status: TaskStatus;
  priority: TaskPriority;
  /** Canonical P0-P4 (preserves the backend semantics). */
  pCode: PCode;
  /** Linked SOP id (e.g. SOP-FIN-02). Optional. */
  sopId?: string;
  dueDate: string;
  /** Minutes until due. Negative = overdue. Computed on load. */
  dueInMin: number;
  createdDate: string;
  completedDate?: string;
  storeId: string;
  storeName: string;
  category: 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'ADHOC' | 'SOP';
  checklistItems?: ChecklistItem[];
  completionNotes?: string;
}

interface ChecklistItem {
  id: string;
  text: string;
  completed: boolean;
}

interface SOP {
  id: string;
  title: string;
  description: string;
  category: string;
  steps: SOPStep[];
  frequency: 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'ONDEMAND';
  estimatedTime: number; // minutes
  assignedRoles: string[];
  createdDate: string;
  lastUpdated: string;
}

interface SOPStep {
  id: string;
  stepNumber: number;
  instruction: string;
  warning?: string;
  image?: string;
}

/** Per-employee performance row aggregated from the live tasks array. */
interface EmployeePerformance {
  employeeId: string;
  employeeName: string;
  tasksAssigned: number;
  tasksCompleted: number;
  tasksOverdue: number;
  completionRate: number; // 0-100
}

export function TaskManagementPage() {
  const { user } = useAuth();
  const toast = useToast();

  const [activeTab, setActiveTab] = useState<TabType>('my-tasks');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<TaskStatus | 'ALL'>('ALL');
  const [priorityFilter, setPriorityFilter] = useState<TaskPriority | 'ALL'>('ALL');
  const [isLoading, setIsLoading] = useState(true);

  const [tasks, setTasks] = useState<Task[]>([]);
  const [sops, setSops] = useState<SOP[]>([]);
  // Phase 6.14 — wire the previously-broken "New Task" / "New SOP" button
  // (state getter was discarded with `const [, setShowCreateTask]`).
  const [showCreateTask, setShowCreateTask] = useState(false);
  // SOP editor state — open with null to create, with a template to edit.
  const [sopEditorOpen, setSopEditorOpen] = useState(false);
  const [editingSop, setEditingSop] = useState<SopTemplateForm | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
    // user?.activeStoreId in deps so tasks re-fetch when the topbar
    // store-switcher changes the active store.
  }, [activeTab, user?.activeStoreId]);

  const loadData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Fetch real tasks from API
      const response = await tasksApi.getTasks();
      // Backend ships lowercase status ('open', 'in_progress',
      // 'completed', 'escalated'); the UI uses uppercase enums. The
      // case mismatch caused a hard crash: getStatusBadge tried to
      // destructure config['open'] which was undefined → ErrorBoundary
      // showed "Something went wrong" on /tasks. Normalise to upper +
      // map the two distinct names (open→PENDING, escalated→OVERDUE).
      const statusFor = (raw: any): TaskStatus => {
        const s = String(raw ?? '').toUpperCase();
        if (s === 'OPEN') return 'PENDING';
        if (s === 'ESCALATED') return 'OVERDUE';
        if (s === 'IN_PROGRESS') return 'IN_PROGRESS';
        if (s === 'COMPLETED') return 'COMPLETED';
        if (s === 'PENDING' || s === 'OVERDUE') return s as TaskStatus;
        return 'PENDING';  // safe fallback for unknown values
      };
      const priorityFor = (raw: any): TaskPriority => {
        const p = String(raw ?? '').toUpperCase();
        if (p === 'P0' || p === 'P1' || p === 'URGENT') return 'URGENT';
        if (p === 'P2' || p === 'HIGH') return 'HIGH';
        if (p === 'P3' || p === 'MEDIUM') return 'MEDIUM';
        return 'LOW';
      };
      // Tasks v2: keep backend's canonical P0-P4 alongside the legacy
      // URGENT/HIGH/MEDIUM/LOW (analytics block still uses those).
      const pCodeFor = (raw: any): PCode => {
        const p = String(raw ?? '').toUpperCase();
        if (p === 'P0') return 'P0';
        if (p === 'P1' || p === 'URGENT') return 'P1';
        if (p === 'P2' || p === 'HIGH') return 'P2';
        if (p === 'P3' || p === 'MEDIUM') return 'P3';
        return 'P4';
      };
      const minutesUntil = (iso?: string): number => {
        if (!iso) return Number.POSITIVE_INFINITY;
        const d = new Date(iso);
        if (isNaN(d.getTime())) return Number.POSITIVE_INFINITY;
        return Math.round((d.getTime() - Date.now()) / 60000);
      };
      const apiTasks: Task[] = (response?.tasks || []).map((t: any) => ({
        // The tasks API emits `task_id`; falling back keeps Reassign / row keys
        // working (a bare `t.id` left the id empty -> Reassign 404'd).
        id: t.task_id || t.id || '',
        title: t.title || '',
        description: t.description || '',
        assignedTo: t.assigned_to || '',
        assignedToName: t.assigned_to_name || t.assigned_to || '',
        assignedBy: t.assigned_by || '',
        assignedByName: t.assigned_by_name || t.assigned_by || '',
        status: statusFor(t.status),
        priority: priorityFor(t.priority),
        pCode: pCodeFor(t.priority),
        sopId: t.sop_id || t.source?.sop_id,
        dueInMin: minutesUntil(t.due_at),
        dueDate: t.due_at ? t.due_at.split('T')[0] : '',
        createdDate: t.created_at ? t.created_at.split('T')[0] : '',
        completedDate: t.completed_at ? t.completed_at.split('T')[0] : undefined,
        storeId: t.store_id || '',
        storeName: t.store_name || '',
        category: t.category || 'ADHOC',
        checklistItems: t.checklist_items,
        completionNotes: t.completion_notes,
      }));
      setTasks(apiTasks);

      // Phase 6.14 — SOPs from the persistent sop_templates collection.
      // Falls back to mock seed below if the API is unavailable, so the
      // page is never empty on a fresh deploy. SUPERADMIN edits via the
      // SopEditorModal; creates persist through /tasks/sop-templates.
      try {
        const sopResp = await tasksApi.getSopTemplates({
          storeId: user?.activeStoreId,
          activeOnly: true,
        });
        if (sopResp.templates.length > 0) {
          setSops(sopResp.templates.map(t => ({
            id: t.template_id,
            title: t.title,
            description: t.description,
            category: t.category as any,
            frequency: t.frequency as any,
            estimatedTime: t.estimated_time,
            assignedRoles: t.assigned_roles,
            createdDate: t.created_at?.slice(0, 10) || '',
            lastUpdated: t.updated_at?.slice(0, 10) || '',
            steps: t.steps.map(s => ({
              id: String(s.step_number),
              stepNumber: s.step_number,
              instruction: s.instruction,
              warning: s.warning,
            })),
          })));
          // Skip the mock-seed block below.
          // (Empty list triggers seed fallback so new stores have something.)
        } else {
          setSops([
        {
          id: '1',
          title: 'Store Opening Procedure',
          description: 'Standard operating procedure for opening the store each morning',
          category: 'Operations',
          frequency: 'DAILY',
          estimatedTime: 20,
          assignedRoles: ['Store Manager', 'Sales Associate'],
          createdDate: '2024-01-01',
          lastUpdated: '2024-01-15',
          steps: [
            { id: '1', stepNumber: 1, instruction: 'Arrive 15 minutes before opening time' },
            { id: '2', stepNumber: 2, instruction: 'Disarm security system using code', warning: 'Never share security code' },
            { id: '3', stepNumber: 3, instruction: 'Turn on all lights, AC, and music system' },
            { id: '4', stepNumber: 4, instruction: 'Check cash register - verify starting float is ₹5,000', warning: 'Report any discrepancies immediately' },
            { id: '5', stepNumber: 5, instruction: 'Clean all display cases and mirrors' },
            { id: '6', stepNumber: 6, instruction: 'Boot up POS system and verify network connectivity' },
            { id: '7', stepNumber: 7, instruction: 'Check stock levels for top 10 SKUs' },
            { id: '8', stepNumber: 8, instruction: 'Review WhatsApp messages and customer appointments' },
            { id: '9', stepNumber: 9, instruction: 'Unlock entrance door exactly at opening time' },
          ],
        },
        {
          id: '2',
          title: 'End of Day Cash Reconciliation',
          description: 'Daily cash reconciliation and reporting procedure',
          category: 'Finance',
          frequency: 'DAILY',
          estimatedTime: 30,
          assignedRoles: ['Store Manager', 'Cashier'],
          createdDate: '2024-01-01',
          lastUpdated: '2024-01-20',
          steps: [
            { id: '1', stepNumber: 1, instruction: 'Print day-end sales report from POS' },
            { id: '2', stepNumber: 2, instruction: 'Count all cash in register - separate by denomination' },
            { id: '3', stepNumber: 3, instruction: 'Count all payment method totals (Cash, Card, UPI, etc.)' },
            { id: '4', stepNumber: 4, instruction: 'Match physical cash with system cash sales', warning: 'Any variance over ₹100 must be reported' },
            { id: '5', stepNumber: 5, instruction: 'Prepare bank deposit bag for excess cash' },
            { id: '6', stepNumber: 6, instruction: 'Update daily sales Excel sheet' },
            { id: '7', stepNumber: 7, instruction: 'Send WhatsApp report to owner with photo of cash count sheet' },
            { id: '8', stepNumber: 8, instruction: 'Lock cash in safe, retain ₹5,000 for tomorrow' },
          ],
        },
        {
          id: '3',
          title: 'Customer Order Processing - Prescription Eyewear',
          description: 'Step-by-step process for taking prescription eyewear orders',
          category: 'Sales',
          frequency: 'ONDEMAND',
          estimatedTime: 25,
          assignedRoles: ['Sales Associate', 'Optometrist'],
          createdDate: '2024-01-01',
          lastUpdated: '2024-02-01',
          steps: [
            { id: '1', stepNumber: 1, instruction: 'Greet customer and understand their requirement' },
            { id: '2', stepNumber: 2, instruction: 'Verify if customer has recent prescription (less than 1 year old)', warning: 'Do NOT proceed without valid prescription' },
            { id: '3', stepNumber: 3, instruction: 'If no prescription, schedule eye test with optometrist' },
            { id: '4', stepNumber: 4, instruction: 'Help customer select frame - note frame code and measurements' },
            { id: '5', stepNumber: 5, instruction: 'Input prescription details into system carefully', warning: 'Double-check all prescription values - errors are costly' },
            { id: '6', stepNumber: 6, instruction: 'Recommend lens type (single vision, bifocal, progressive)' },
            { id: '7', stepNumber: 7, instruction: 'Offer lens coatings (anti-glare, blue-cut, photochromic)' },
            { id: '8', stepNumber: 8, instruction: 'Calculate total price and provide estimate' },
            { id: '9', stepNumber: 9, instruction: 'Collect advance payment (minimum 50%)' },
            { id: '10', stepNumber: 10, instruction: 'Create order in system and give customer receipt with delivery date' },
            { id: '11', stepNumber: 11, instruction: 'Send confirmation WhatsApp with order details to customer' },
          ],
        },
        {
          id: '4',
          title: 'Inventory Receiving and Verification',
          description: 'Procedure for receiving stock from suppliers',
          category: 'Inventory',
          frequency: 'WEEKLY',
          estimatedTime: 45,
          assignedRoles: ['Store Manager', 'Inventory Manager'],
          createdDate: '2024-01-01',
          lastUpdated: '2024-01-25',
          steps: [
            { id: '1', stepNumber: 1, instruction: 'Verify supplier name and invoice number matches PO' },
            { id: '2', stepNumber: 2, instruction: 'Check all boxes for damage before accepting', warning: 'Refuse delivery if packaging is damaged' },
            { id: '3', stepNumber: 3, instruction: 'Count total number of boxes/cartons' },
            { id: '4', stepNumber: 4, instruction: 'Open each box and verify contents against packing slip' },
            { id: '5', stepNumber: 5, instruction: 'Check each item for defects or damage' },
            { id: '6', stepNumber: 6, instruction: 'Scan or manually enter items into inventory system' },
            { id: '7', stepNumber: 7, instruction: 'Verify quantities match invoice', warning: 'Report discrepancies to supplier within 24 hours' },
            { id: '8', stepNumber: 8, instruction: 'Organize stock in designated storage areas with proper labels' },
            { id: '9', stepNumber: 9, instruction: 'Update inventory spreadsheet with new stock' },
            { id: '10', stepNumber: 10, instruction: 'Forward invoice to accounts team for payment processing' },
          ],
        },
        ]);  // end mock-seed fallback
        }  // end empty-templates else-branch
      } catch (sopErr) {
        // API unavailable — keep current sops state (may be previous page load).
        // eslint-disable-next-line no-console
        console.warn('[TaskManagement] SOP load failed:', sopErr);
      }

      // Performance metrics are derived from the live `tasks` array
      // via a useMemo — no mock seed (the previous Rajesh / Priya /
      // Amit / Sneha placeholders were misleading because they showed
      // even when there were zero real tasks).

    } catch (err: any) {
      setError('Failed to load task data. Please try again.');
      toast.error('Failed to load task data');
    } finally {
      setIsLoading(false);
    }
  };

  // Tasks v2 (May-2026) renders priority via P0-P4 pills + countdown
  // pills inline; the legacy badge helpers are no longer needed.

  const myTasks = tasks.filter(task => task.assignedTo === user?.id || user?.roles?.includes('ADMIN') || user?.roles?.includes('SUPERADMIN'));
  // Audit Run #4: /tasks crashed at the error boundary on initial render.
  // The most common cause for this kind of crash is a row with a null/
  // undefined string field hitting `.toLowerCase()`. We guard every
  // string-y access so a malformed task or SOP doc renders blank
  // instead of taking out the whole page.
  const _q = (searchQuery || '').toLowerCase();
  const _hits = (s: unknown) => String(s ?? '').toLowerCase().includes(_q);
  const filteredTasks = (activeTab === 'my-tasks' ? myTasks : tasks).filter(task => {
    const matchesSearch = _hits(task?.title) || _hits(task?.description);
    const matchesStatus = statusFilter === 'ALL' || task?.status === statusFilter;
    const matchesPriority = priorityFilter === 'ALL' || task?.priority === priorityFilter;
    return matchesSearch && matchesStatus && matchesPriority;
  });

  const filteredSOPs = sops.filter(sop =>
    _hits(sop?.title) || _hits(sop?.description) || _hits(sop?.category)
  );

  const myOpen = myTasks.filter(t => t.status !== 'COMPLETED').length;

  // Tasks v2 — selected task for split-panel detail. Defaults to the
  // first overdue/escalating task on tab change so the detail card
  // is always populated when there's something urgent.
  const [selectedTaskV2, setSelectedTaskV2] = useState<Task | null>(null);
  useEffect(() => {
    if (selectedTaskV2 && filteredTasks.find(t => t.id === selectedTaskV2.id)) return;
    const next = filteredTasks.find(t => t.pCode === 'P0' || t.pCode === 'P1')
              || filteredTasks[0]
              || null;
    setSelectedTaskV2(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredTasks.length, activeTab]);

  // Priority strip counts (open tasks only; completed don't count).
  const priorityCounts: Record<PCode, number> = useMemo(() => {
    const c: Record<PCode, number> = { P0: 0, P1: 0, P2: 0, P3: 0, P4: 0 };
    for (const t of filteredTasks) {
      if (t.status === 'COMPLETED') continue;
      c[t.pCode] += 1;
    }
    return c;
  }, [filteredTasks]);

  /**
   * Performance analytics derived from the live `tasks` array.
   * Replaces the previous Rajesh / Priya / Amit / Sneha mock seed
   * which kept rendering even when the backend returned zero tasks.
   * If `tasks` is empty, the analytics tab renders an honest empty
   * state instead of fake numbers.
   */
  const analytics = useMemo(() => {
    const total = tasks.length;
    const completed = tasks.filter((t) => t.status === 'COMPLETED').length;
    const overdue = tasks.filter((t) => t.status === 'OVERDUE').length;
    const completionRate = total > 0 ? (completed / total) * 100 : 0;
    const activeEmployees = new Set(
      tasks.map((t) => t.assignedTo).filter((id): id is string => !!id),
    ).size;

    // Per-employee aggregation
    const byEmp = new Map<string, EmployeePerformance>();
    for (const t of tasks) {
      const id = t.assignedTo || '__unassigned__';
      const name = t.assignedToName || (id === '__unassigned__' ? 'Unassigned' : id);
      const cur = byEmp.get(id) ?? {
        employeeId: id,
        employeeName: name,
        tasksAssigned: 0,
        tasksCompleted: 0,
        tasksOverdue: 0,
        completionRate: 0,
      };
      cur.tasksAssigned += 1;
      if (t.status === 'COMPLETED') cur.tasksCompleted += 1;
      if (t.status === 'OVERDUE') cur.tasksOverdue += 1;
      byEmp.set(id, cur);
    }
    const employees = Array.from(byEmp.values())
      .map((e) => ({
        ...e,
        completionRate:
          e.tasksAssigned > 0 ? (e.tasksCompleted / e.tasksAssigned) * 100 : 0,
      }))
      .sort((a, b) => b.completionRate - a.completionRate);

    return {
      total,
      completed,
      overdue,
      completionRate,
      activeEmployees,
      employees,
    };
  }, [tasks]);

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow mb-1.5">Tasks &amp; SOPs</div>
          <h1>The shift, by priority.</h1>
          <div className="hint">P0–P4 priorities with countdown timers and auto-escalation tied to SOPs. 40-person ops coordination.</div>
        </div>
        <div className="row gap-2">
          <button
            onClick={() => {
              if (activeTab === 'sop') {
                // Phase 6.14 — open the SOP editor in "create new" mode
                setEditingSop(null);
                setSopEditorOpen(true);
              } else {
                // Task tab — legacy behaviour (wiring now functional after
                // setShowCreateTask state getter was fixed)
                setShowCreateTask(true);
              }
            }}
            className="btn sm primary"
          >
            <Plus className="w-4 h-4" /> {activeTab === 'sop' ? 'New SOP' : 'New task'}
          </button>
        </div>
      </div>

      {/* Tabs — shared underline style */}
      <div className="inv-tabs">
        <button
          onClick={() => setActiveTab('my-tasks')}
          className={activeTab === 'my-tasks' ? 'on' : ''}
        >
          <User className="w-4 h-4" />
          Mine <span className="count">· {myOpen}</span>
        </button>
        <button
          onClick={() => setActiveTab('team-tasks')}
          className={activeTab === 'team-tasks' ? 'on' : ''}
        >
          <Users className="w-4 h-4" /> Team
        </button>
        <button
          onClick={() => setActiveTab('sop')}
          className={activeTab === 'sop' ? 'on' : ''}
        >
          <ListChecks className="w-4 h-4" /> SOPs
        </button>
        <button
          onClick={() => setActiveTab('analytics')}
          className={activeTab === 'analytics' ? 'on' : ''}
        >
          <TrendingUp className="w-4 h-4" /> Performance
        </button>
      </div>

      {/* Search & Filters */}
      {activeTab !== 'analytics' && (
        <div className="flex items-center gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
            <input
              type="text"
              placeholder={activeTab === 'sop' ? 'Search SOPs...' : 'Search tasks...'}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="input-field pl-10"
            />
          </div>
          {activeTab !== 'sop' && (
            <>
              <select
                title="Filter by status"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as any)}
                className="input-field w-auto"
              >
                <option value="ALL">All Status</option>
                <option value="PENDING">Pending</option>
                <option value="IN_PROGRESS">In Progress</option>
                <option value="COMPLETED">Completed</option>
                <option value="OVERDUE">Overdue</option>
              </select>
              <select
                title="Filter by priority"
                value={priorityFilter}
                onChange={(e) => setPriorityFilter(e.target.value as any)}
                className="input-field w-auto"
              >
                <option value="ALL">All Priority</option>
                <option value="URGENT">Urgent</option>
                <option value="HIGH">High</option>
                <option value="MEDIUM">Medium</option>
                <option value="LOW">Low</option>
              </select>
            </>
          )}
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="s-section flex items-center gap-2 mt-3.5" style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)' }}>
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>{error}</span>
          <button onClick={loadData} className="btn sm ml-auto">Retry</button>
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="flex items-center justify-center h-96">
          <Loader2 className="w-8 h-8 animate-spin text-green-600" />
        </div>
      ) : activeTab === 'sop' ? (
        /* SOPs List */
        <div className="grid grid-cols-1 desktop:grid-cols-2 gap-4">
          {filteredSOPs.map((sop) => (
            <div key={sop.id} className="card hover:shadow-lg transition-shadow">
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <h3 className="text-lg font-semibold text-gray-900">{sop.title}</h3>
                    <span className="px-2 py-1 bg-purple-100 text-purple-700 text-xs rounded">
                      {sop.category}
                    </span>
                  </div>
                  <p className="text-sm text-gray-600 mb-3">{sop.description}</p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 mb-4 p-3 bg-gray-50 rounded-lg">
                <div>
                  <p className="text-xs text-gray-600">Frequency</p>
                  <p className="text-sm font-medium text-gray-900">{sop.frequency}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Est. Time</p>
                  <p className="text-sm font-medium text-gray-900">{sop.estimatedTime} mins</p>
                </div>
              </div>

              <div className="mb-3">
                <p className="text-xs text-gray-600 mb-2">Steps ({sop.steps.length}):</p>
                <div className="space-y-1">
                  {sop.steps.slice(0, 3).map((step) => (
                    <div key={step.id} className="flex items-start gap-2 text-sm">
                      <span className="text-purple-600 font-medium flex-shrink-0">{step.stepNumber}.</span>
                      <span className="text-gray-700">{step.instruction}</span>
                    </div>
                  ))}
                  {sop.steps.length > 3 && (
                    <p className="text-xs text-gray-500 ml-5">+ {sop.steps.length - 3} more steps...</p>
                  )}
                </div>
              </div>

              <div className="flex items-center justify-between pt-3 border-t border-gray-200">
                <div className="text-xs text-gray-500">
                  Updated: {new Date(sop.lastUpdated).toLocaleDateString()}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      // Phase 6.14 — View opens the editor in read-aware mode
                      // (all fields still editable since staff with permission
                      // land here; tightening view-only can come later).
                      setEditingSop({
                        template_id: sop.id,
                        title: sop.title,
                        description: sop.description,
                        category: sop.category as any,
                        frequency: sop.frequency as any,
                        estimated_time: sop.estimatedTime,
                        steps: sop.steps.map(s => ({
                          step_number: s.stepNumber,
                          instruction: s.instruction,
                          warning: (s as any).warning,
                        })),
                        assigned_roles: sop.assignedRoles || [],
                        assigned_users: [],
                      });
                      setSopEditorOpen(true);
                    }}
                    className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                    aria-label="View SOP"
                    title="View SOP"
                  >
                    <Eye className="w-4 h-4 text-gray-600" />
                  </button>
                  <button
                    onClick={() => {
                      // Phase 6.14 — Edit opens editor pre-filled with this SOP
                      setEditingSop({
                        template_id: sop.id,
                        title: sop.title,
                        description: sop.description,
                        category: sop.category as any,
                        frequency: sop.frequency as any,
                        estimated_time: sop.estimatedTime,
                        steps: sop.steps.map(s => ({
                          step_number: s.stepNumber,
                          instruction: s.instruction,
                          warning: (s as any).warning,
                        })),
                        assigned_roles: sop.assignedRoles || [],
                        assigned_users: [],
                      });
                      setSopEditorOpen(true);
                    }}
                    className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                    aria-label="Edit SOP"
                    title="Edit SOP"
                  >
                    <Edit className="w-4 h-4 text-gray-600" />
                  </button>
                </div>
              </div>
            </div>
          ))}

          {filteredSOPs.length === 0 && (
            <div className="col-span-2 text-center py-12">
              <ListChecks className="w-12 h-12 text-gray-500 mx-auto mb-3" />
              <p className="text-gray-500">No SOPs found</p>
            </div>
          )}
        </div>
      ) : activeTab === 'analytics' ? (
        /* Team Performance Analytics — fully derived from the live tasks
           array. Empty state surfaces honestly when no tasks exist. */
        <div className="space-y-6">
          <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
                  <Users className="w-5 h-5 text-green-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Active Owners</p>
                  <p className="text-2xl font-bold text-gray-900">{analytics.activeEmployees}</p>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                  <CheckSquare className="w-5 h-5 text-blue-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Tasks Completed</p>
                  <p className="text-2xl font-bold text-gray-900">
                    {analytics.completed}/{analytics.total}
                  </p>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
                  <TrendingUp className="w-5 h-5 text-purple-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Completion Rate</p>
                  <p className="text-2xl font-bold text-gray-900">
                    {analytics.total > 0 ? `${analytics.completionRate.toFixed(1)}%` : '—'}
                  </p>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
                  <AlertTriangle className="w-5 h-5 text-red-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Overdue Tasks</p>
                  <p className={`text-2xl font-bold ${analytics.overdue > 0 ? 'text-red-900' : 'text-gray-900'}`}>
                    {analytics.overdue}
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Owner Performance Ranking — derived from real tasks */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              Owner Performance Ranking
            </h3>
            {analytics.employees.length === 0 ? (
              <div className="text-center py-12">
                <Users className="w-12 h-12 text-gray-400 mx-auto mb-3" />
                <p className="text-gray-700 font-medium">No tasks yet</p>
                <p className="text-sm text-gray-500 mt-1">
                  Owner-level performance will populate as tasks are created
                  and assigned. Stats here are computed from the live tasks
                  collection — no placeholder data is shown.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {analytics.employees.map((emp, index) => {
                  const rateBand =
                    emp.completionRate >= 90
                      ? 'border-green-200 bg-green-50'
                      : emp.completionRate >= 70
                      ? 'border-blue-200 bg-blue-50'
                      : 'border-red-200 bg-red-50';
                  return (
                    <div
                      key={emp.employeeId}
                      className={`p-4 rounded-lg border-2 ${rateBand}`}
                    >
                      <div className="flex items-center gap-4">
                        <div
                          className={`w-10 h-10 rounded-full flex items-center justify-center font-bold text-lg ${
                            index === 0
                              ? 'bg-yellow-100 text-yellow-800'
                              : index === 1
                              ? 'bg-gray-200 text-gray-700'
                              : index === 2
                              ? 'bg-orange-100 text-orange-700'
                              : 'bg-gray-100 text-gray-600'
                          }`}
                        >
                          {index + 1}
                        </div>
                        <div className="flex-1">
                          <h4 className="font-semibold text-gray-900">
                            {emp.employeeName}
                          </h4>
                          <p className="text-xs text-gray-500 font-mono mt-0.5">
                            {emp.employeeId}
                          </p>
                        </div>
                        <div className="grid grid-cols-3 gap-6 text-center">
                          <div>
                            <p className="text-xs text-gray-600">Assigned</p>
                            <p className="text-lg font-semibold text-gray-900">
                              {emp.tasksAssigned}
                            </p>
                          </div>
                          <div>
                            <p className="text-xs text-gray-600">Completed</p>
                            <p className="text-lg font-semibold text-green-600">
                              {emp.tasksCompleted}
                            </p>
                          </div>
                          <div>
                            <p className="text-xs text-gray-600">Overdue</p>
                            <p
                              className={`text-lg font-semibold ${
                                emp.tasksOverdue > 0 ? 'text-red-600' : 'text-gray-900'
                              }`}
                            >
                              {emp.tasksOverdue}
                            </p>
                          </div>
                        </div>
                        <div className="text-right">
                          <p className="text-2xl font-bold text-gray-900">
                            {emp.completionRate.toFixed(1)}%
                          </p>
                          <p className="text-xs text-gray-600">Completion</p>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      ) : (
        /* Tasks v2 — priority strip + split-panel (per docs/design/tasks.html) */
        <TasksV2View
          tasks={filteredTasks}
          counts={priorityCounts}
          selected={selectedTaskV2}
          onSelect={setSelectedTaskV2}
        />
      )}

      {/* Phase 6.14 — SOP create/edit modal. Single surface for both
          flows; `editingSop=null` means create, object means edit. */}
      <SopEditorModal
        isOpen={sopEditorOpen}
        onClose={() => {
          setSopEditorOpen(false);
          setEditingSop(null);
        }}
        initial={editingSop}
        onSaved={async () => {
          // Reload SOPs so the list reflects the write
          try {
            const resp = await tasksApi.getSopTemplates({
              storeId: user?.activeStoreId,
              activeOnly: true,
            });
            setSops(resp.templates.map(t => ({
              id: t.template_id,
              title: t.title,
              description: t.description,
              category: t.category as any,
              frequency: t.frequency as any,
              estimatedTime: t.estimated_time,
              assignedRoles: t.assigned_roles,
              createdDate: t.created_at?.slice(0, 10) || '',
              lastUpdated: t.updated_at?.slice(0, 10) || '',
              steps: t.steps.map(s => ({
                id: String(s.step_number),
                stepNumber: s.step_number,
                instruction: s.instruction,
                warning: s.warning,
              })),
            })));
          } catch (e) {
            // eslint-disable-next-line no-console
            console.warn('[TaskManagement] SOP reload failed:', e);
          }
        }}
      />

      {/* Phase 6.14 — if user clicked "New task" on a non-SOP tab,
          show it was acknowledged (full task-creation UI is on TasksPage). */}
      {showCreateTask && activeTab !== 'sop' && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setShowCreateTask(false)}>
          <div className="bg-white rounded-xl w-full max-w-md p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-semibold text-gray-900">Create a task</h3>
            <p className="text-sm text-gray-600">
              The full task-creation form (assignee, priority, due date, store)
              lives on the Tasks Dashboard. Open it to create a task.
            </p>
            <div className="flex items-center justify-end gap-2">
              <button onClick={() => setShowCreateTask(false)} className="btn sm">Cancel</button>
              {/* QA F14: was href="/tasks" (this very page) -> a circular
                  dead-end. The working create form is on TasksDashboard. */}
              <a href="/tasks/dashboard" className="btn sm primary">Open Tasks Dashboard</a>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Tasks v2 split-panel view (priority strip + task list + detail/ladder)
// Ported from docs/design/tasks.html. Owner-avatar circles intentionally
// omitted per direction; rendered as a compact mono chip instead.
// ============================================================================

const PRI_VAR: Record<PCode, string> = {
  P0: 'var(--p0)',
  P1: 'var(--p1)',
  P2: 'var(--p2)',
  P3: 'var(--p3)',
  P4: 'var(--p4)',
};

interface TasksV2ViewProps {
  tasks: Task[];
  counts: Record<PCode, number>;
  selected: Task | null;
  onSelect: (t: Task | null) => void;
}

function TasksV2View({ tasks, counts, selected, onSelect }: TasksV2ViewProps) {
  const detailOpen = !!selected;

  return (
    <div className={'t-body' + (detailOpen ? ' detail-open' : '')}>
      {/* Left rail — priority strip + task list */}
      <div className="t-list">
        <div className="pri-strip">
          {(Object.keys(PRIORITY_META) as PCode[]).map((p) => (
            <div key={p}>
              <div className="bar" style={{ background: PRI_VAR[p] }} />
              <div className="l">{PRIORITY_META[p].label}</div>
              <div className="v">{counts[p] || 0}</div>
              <div className="d">{PRIORITY_META[p].sub}</div>
            </div>
          ))}
        </div>

        {tasks.length === 0 && (
          <div className="text-center py-12">
            <CheckSquare className="w-12 h-12 text-gray-400 mx-auto mb-3" />
            <p className="text-gray-500">No tasks match the current filter.</p>
          </div>
        )}

        {tasks.map((t, i) => {
          const isSel = selected?.id === t.id;
          const isOverdue = t.dueInMin < 0
            || (t.pCode === 'P1' && t.dueInMin >= 0 && t.dueInMin < 10);
          const ownerLabel = (t.assignedToName || t.assignedTo || '—')
            .split(' ')
            .map((s) => s[0])
            .filter(Boolean)
            .slice(0, 2)
            .join('')
            .toUpperCase() || '—';

          return (
            <div
              key={t.id || `task-${i}`}
              className={
                't-item' +
                (isSel ? ' sel' : '') +
                (isOverdue ? ' overdue' : '')
              }
              onClick={() => onSelect(t)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelect(t);
                }
              }}
            >
              <div className="pri-wrap">
                <span className={'pill-' + t.pCode}>{t.pCode}</span>
                <Countdown minutes={t.dueInMin} />
              </div>
              <div>
                <div className="ttl">{t.title}</div>
                <div className="meta">
                  <span className="mono">{t.id}</span>
                  {t.sopId && (
                    <>
                      <span>·</span>
                      <span className="mono">{t.sopId}</span>
                    </>
                  )}
                  <span>·</span>
                  <span>
                    Stage: <strong>{t.status.replace('_', ' ').toLowerCase()}</strong>
                  </span>
                </div>
                {(t.pCode === 'P0' || t.pCode === 'P1') && t.dueInMin >= 0 && (
                  <div className="esc">
                    <Zap className="w-3 h-3" />
                    Escalates in {t.dueInMin}m if not closed
                  </div>
                )}
              </div>
              <div className="own" title={'Owner: ' + (t.assignedToName || '—')}>
                {ownerLabel}
              </div>
            </div>
          );
        })}
      </div>

      {/* Detail panel */}
      <aside className="t-detail">
        {selected ? (
          <TaskDetailPanel task={selected} onClose={() => onSelect(null)} />
        ) : (
          <div className="d-body">
            <p className="text-sm text-gray-500">
              Pick a task on the left to see its escalation ladder and SOP.
            </p>
          </div>
        )}
      </aside>
    </div>
  );
}

// Render a minute count as a human-friendly time-left string. Raw "1892319m"
// (~3.6 years) is useless to a store manager; "3y" or "44mo" is readable.
// Uses round-number breakpoints so the ladder still looks like a ladder
// (`+30m` -> `+1h` -> `+1d` -> `+1mo`). 2026-05-27 QA P2.
function humanizeMinutes(min: number): string {
  if (min < 60) return `${min}m`;
  if (min < 60 * 24) return `${Math.round(min / 60)}h`;
  if (min < 60 * 24 * 30) return `${Math.round(min / 60 / 24)}d`;
  if (min < 60 * 24 * 365) return `${Math.round(min / 60 / 24 / 30)}mo`;
  return `${Math.round(min / 60 / 24 / 365)}y`;
}

// QA F15 guard: a due_at more than ~120 days out (or missing/unparseable, where
// minutesUntil returns +/-Infinity) is corrupt data, not a real SLA window — the
// widest SLA grace is 7 days. Treat those as "no countdown" so the UI shows a
// neutral dash instead of a nonsense "4y left".
const MAX_SANE_DUE_MIN = 120 * 24 * 60;
function isSaneDue(min: number): boolean {
  return Number.isFinite(min) && Math.abs(min) <= MAX_SANE_DUE_MIN;
}

function TaskDetailPanel({ task, onClose }: { task: Task; onClose: () => void }) {
  const toast = useToast();
  // Interim reassign: prompt for the new owner's user id + an optional reason,
  // then POST through the existing tasksApi.reassignTask seam. A full owner-
  // picker modal is a follow-up; this unblocks the previously-dead button.
  const handleReassign = async () => {
    const newAssignee = window.prompt('Reassign to (user id):', task.assignedTo || '');
    if (!newAssignee || !newAssignee.trim()) return;
    const reason = window.prompt('Reason (optional):', '') || undefined;
    try {
      await tasksApi.reassignTask(task.id, newAssignee.trim(), reason);
      toast.success('Task reassigned');
      onClose();
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(typeof msg === 'string' ? msg : 'Failed to reassign task');
    }
  };
  const dueSane = isSaneDue(task.dueInMin);
  const minLeft = dueSane ? Math.max(0, task.dueInMin) : 0;
  const minLeftLabel = dueSane ? humanizeMinutes(minLeft) : '—';
  const showTimer = task.pCode === 'P0' || task.pCode === 'P1';
  const created = task.createdDate
    ? new Date(task.createdDate).toLocaleString('en-IN', {
        hour: '2-digit',
        minute: '2-digit',
        day: '2-digit',
        month: 'short',
      })
    : '—';

  return (
    <>
      <div className="d-head">
        <div className="row flex items-center gap-2 mb-2.5">
          <span className={'pill-' + task.pCode}>{task.pCode}</span>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-4)' }}>{task.id}</span>
          <span className="flex-1" />
          <button
            type="button"
            className="btn sm"
            onClick={onClose}
            aria-label="Close detail"
          >
            <ArrowLeft className="w-3 h-3" /> Back
          </button>
          <button type="button" className="btn sm" onClick={handleReassign}>
            <User className="w-3 h-3" /> Reassign
          </button>
        </div>
        <h3>{task.title}</h3>
        <div className="hint" style={{ fontSize: 11.5, color: 'var(--ink-4)', marginTop: 4 }}>
          Created {created} · by {task.assignedByName || task.assignedBy || 'system'}
        </div>
      </div>

      <div className="d-body">
        {showTimer && (
          <div className="timer-big">
            <div>
              <div className="l">Auto-escalates in</div>
              <div className="v">
                {minLeftLabel}
              </div>
            </div>
            <div
              style={{
                marginLeft: 'auto',
                textAlign: 'right',
                color: 'var(--err)',
                fontSize: 11.5,
                maxWidth: 160,
              }}
            >
              Next: <strong>ASM</strong>
              <br />
              Then: <strong>Ops Head</strong> in +30m
            </div>
          </div>
        )}

        {task.description && (
          <div className="d-sec">
            <h4>Brief</h4>
            <p style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--ink-2)' }}>
              {task.description}
            </p>
          </div>
        )}

        <div className="d-sec">
          <h4>Escalation ladder</h4>
          <div className="ladder">
            <div className="ladder-step done">
              <div className="rung">1</div>
              <div className="who">{task.assignedByName || 'Originator'}</div>
              <div className="when">Assigned</div>
            </div>
            <div className="ladder-step cur">
              <div className="rung">2</div>
              <div className="who">{task.assignedToName || 'Owner'} (current)</div>
              <div className="when">
                {!dueSane
                  ? '—'
                  : task.dueInMin < 0
                    ? `${humanizeMinutes(Math.abs(task.dueInMin))} overdue`
                    : `${minLeftLabel} left`}
              </div>
            </div>
            <div className="ladder-step">
              <div className="rung">3</div>
              <div className="who">ASM</div>
              <div className="when">+{humanizeMinutes(minLeft || 30)}</div>
            </div>
            <div className="ladder-step">
              <div className="rung">4</div>
              <div className="who">Ops Head</div>
              <div className="when">+{humanizeMinutes((minLeft || 30) + 30)}</div>
            </div>
          </div>
        </div>

        {task.sopId && (
          <div className="d-sec">
            <h4>Attached SOP</h4>
            <div className="sop-box">
              <div className="sid">{task.sopId}</div>
              Open the SOP for the full step-by-step trigger / owner / approver
              breakdown and checkpoints.
              <div className="mt-2.5">
                <button type="button" className="btn sm">Open full SOP</button>
              </div>
            </div>
          </div>
        )}

        {task.checklistItems && task.checklistItems.length > 0 && (
          <div className="d-sec">
            <h4>Checklist</h4>
            <ul style={{ paddingLeft: 18, margin: 0, fontSize: 12.5, color: 'var(--ink-2)' }}>
              {task.checklistItems.map((c) => (
                <li key={c.id} style={{ marginBottom: 4 }}>
                  <span style={{ textDecoration: c.completed ? 'line-through' : 'none' }}>
                    {c.text}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </>
  );
}

/**
 * Live-ticking countdown pill. Shows "Nm" / "<1m" / "Xm late".
 *  • hot   if 0 ≤ minutes < 10 or already late
 *  • warm  if 10 ≤ minutes < 30
 *  • plain otherwise
 */
function Countdown({ minutes }: { minutes: number }) {
  const [m, setM] = useState<number>(minutes);
  useEffect(() => {
    setM(minutes);
    const id = window.setInterval(() => {
      setM((prev) => prev - 1);
    }, 60_000);
    return () => window.clearInterval(id);
  }, [minutes]);

  // QA F15: a missing/corrupt due_at (minutesUntil -> +/-Infinity, or a value
  // hundreds of days out) is not a real countdown — show a neutral dash rather
  // than "4y left". Guard on the prop (source of truth), not the ticking state.
  if (!isSaneDue(minutes)) {
    return <span className="count-pill">—</span>;
  }

  const late = m < 0;
  const hot = !late && m < 10;
  const warm = !late && m >= 10 && m < 30;
  const cls = 'count-pill' + (late || hot ? ' hot' : warm ? ' warm' : '');
  // Humanize (QA F15): raw minutes like "1890631m" (~3.6y, when a task's
  // due_at is set far out) are unreadable. humanizeMinutes keeps "Nm" for
  // sub-hour values (the live-tick case) and rolls up to h/d/mo/y beyond that.
  const label = late
    ? `${humanizeMinutes(Math.abs(m))} late`
    : m < 1
      ? '<1m'
      : humanizeMinutes(m);

  return (
    <span className={cls}>
      {(late || hot) && <span className="dot" />}
      {label}
    </span>
  );
}
