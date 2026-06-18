// ============================================================================
// IMS 2.0 - HR / Payroll API
// ============================================================================

import api from './client';

export const hrApi = {
  getAttendance: async (storeId: string, date?: string) => {
    const response = await api.get('/hr/attendance', { params: { store_id: storeId, date } });
    const data = response.data;
    // Backend (GET /hr/attendance) returns camelCase keys attendanceId /
    // employeeId / employeeName / checkIn / checkOut. The HR page + self-service
    // view read id / userId / userName / checkInTime / checkOutTime. Map the
    // seam here while keeping the raw fields so both shapes resolve.
    const rawRecords = Array.isArray(data?.records) ? data.records : Array.isArray(data) ? data : [];
    const records = rawRecords.map((r: any) => ({
      ...r,
      id: r.attendanceId ?? r.id ?? '',
      userId: r.employeeId ?? r.userId ?? '',
      userName: r.employeeName ?? r.userName ?? '',
      checkInTime: r.checkIn ?? r.checkInTime ?? null,
      checkOutTime: r.checkOut ?? r.checkOutTime ?? null,
    }));
    return { ...(data && typeof data === 'object' ? data : {}), records };
  },

  // Geo-fenced, late-mark-aware check-in. The backend reads store_id /
  // latitude / longitude as QUERY params (not a JSON body) and enforces the
  // store radius for roles 4-7 server-side, returning is_late + late_minutes.
  checkIn: async (storeId: string, latitude: number, longitude: number) => {
    const response = await api.post('/hr/attendance/check-in', null, {
      params: { store_id: storeId, latitude, longitude },
    });
    return response.data as {
      message: string;
      checkInTime: string;
      is_late: boolean;
      late_minutes: number;
      geo: { verified: boolean; reason: string };
    };
  },

  checkOut: async (attendanceId: string) => {
    const response = await api.post(`/hr/attendance/${attendanceId}/check-out`);
    return response.data;
  },

  getLeaves: async (params?: { userId?: string; status?: string }) => {
    const response = await api.get('/hr/leaves', { params });
    const data = response.data;
    // Backend (GET /hr/leaves) returns RAW snake_case leave docs:
    // leave_id / employee_id / employee_name / leave_type / from_date /
    // to_date / reason / status / applied_at / approved_by. The FE reads
    // id / userId / userName / leaveType / startDate / endDate / days /
    // appliedAt / approvedBy. Map the seam (and compute inclusive day count)
    // while keeping the raw fields so approveLeave gets a valid leave_id.
    const rawLeaves = Array.isArray(data?.leaves) ? data.leaves : Array.isArray(data) ? data : [];
    const leaves = rawLeaves.map((l: any) => {
      const startDate = l.from_date ?? l.startDate ?? null;
      const endDate = l.to_date ?? l.endDate ?? startDate;
      let days = l.days;
      if (days == null && startDate) {
        const s = new Date(startDate);
        const e = new Date(endDate || startDate);
        const diff = Math.floor((e.getTime() - s.getTime()) / 86400000) + 1;
        days = Number.isFinite(diff) && diff > 0 ? diff : 1;
      }
      return {
        ...l,
        id: l.leave_id ?? l.id ?? '',
        userId: l.employee_id ?? l.userId ?? '',
        userName: l.employee_name ?? l.userName ?? '',
        leaveType: l.leave_type ?? l.leaveType ?? '',
        startDate,
        endDate,
        days: days ?? 1,
        appliedAt: l.applied_at ?? l.appliedAt ?? null,
        approvedBy: l.approved_by ?? l.approvedBy ?? null,
      };
    });
    return { ...(data && typeof data === 'object' ? data : {}), leaves };
  },

  applyLeave: async (data: Partial<import('../../types').Leave>) => {
    const response = await api.post('/hr/leaves', data);
    return response.data;
  },

  approveLeave: async (leaveId: string, approved: boolean, remarks?: string) => {
    // Backend exposes SEPARATE endpoints: /approve (no body) and
    // /reject (?reason=). The old single-call form silently approved even
    // when approved=false and dropped remarks.
    if (approved) {
      const response = await api.post(`/hr/leaves/${leaveId}/approve`);
      return response.data;
    }
    const response = await api.post(`/hr/leaves/${leaveId}/reject`, null, {
      params: { reason: remarks || 'Rejected' },
    });
    return response.data;
  },

  // F26 remote fast-path approval. The manager has already approved the E4
  // leave_approval request in their approvals inbox (PIN-gated) and holds the
  // one-time approval_token it minted; this spends it and stamps the leave
  // approved_via='fast_path'. Used for short-notice CASUAL/SICK leave actioned
  // from another store / device.
  approveLeaveRemote: async (leaveId: string, approvalToken: string) => {
    const response = await api.post(`/hr/leaves/${leaveId}/approve-remote`, {
      approval_token: approvalToken,
    });
    return response.data as {
      message: string;
      leave_id: string;
      approved_via: string;
    };
  },

  getLatestPayslip: async (employeeId: string) => {
    const response = await api.get(`/payroll/payslip/${employeeId}`);
    return response.data;
  },

  // --- Employee self-service (own data; /hr/me/*) -------------------
  // These hit the hr_self_service_router, which is mounted OUTSIDE the HR
  // finance-role gate so ANY logged-in staff member can read their OWN data.
  // The backend pins every read to the requesting user (no employee_id param),
  // so floor staff (sales / cashier / optometrist / workshop) can use them even
  // though they cannot hit the rest of /hr/* or /payroll/*. All fail-soft.

  /** This-month (or given month) own attendance: per-day codes + counts. */
  getMyAttendance: async (opts?: { month?: number; year?: number }) => {
    const params: Record<string, number> = {};
    if (opts?.month) params.month = opts.month;
    if (opts?.year) params.year = opts.year;
    const response = await api.get('/hr/me/attendance', { params });
    return response.data as MyAttendance;
  },

  /** Own leaves for the year + approved/pending balance summary. */
  getMyLeaves: async (opts?: { year?: number }) => {
    const params: Record<string, number> = {};
    if (opts?.year) params.year = opts.year;
    const response = await api.get('/hr/me/leaves', { params });
    return response.data as MyLeaves;
  },

  /** Own most-recent payslip (same shape as GET /payroll/payslip/{id}). */
  getMyPayslip: async () => {
    const response = await api.get('/hr/me/payslip');
    return response.data as { payslip: MyPayslip | null };
  },

  /** Own commission for this (or given) month. */
  getMyCommission: async (opts?: { month?: number; year?: number }) => {
    const params: Record<string, number> = {};
    if (opts?.month) params.month = opts.month;
    if (opts?.year) params.year = opts.year;
    const response = await api.get('/hr/me/commission', { params });
    return response.data as MyCommission;
  },

  // Monthly attendance grid (per-employee x per-day status matrix).
  // month: 'YYYY-MM'. Returns AttendanceGrid (see type below).
  getAttendanceGrid: async (opts: { month: string; storeId?: string }) => {
    const params: Record<string, string> = { month: opts.month };
    if (opts.storeId) params.store_id = opts.storeId;
    const response = await api.get('/hr/attendance/grid', { params });
    return response.data as AttendanceGrid;
  },

  // Compact monthly attendance summary (present/absent/late counts) for the
  // HR page summary card. month: 'YYYY-MM'. Fail-soft on the caller side —
  // the HR card derives counts from the grid if this endpoint is unavailable.
  getAttendanceSummary: async (opts: { month: string; storeId?: string }) => {
    const params: Record<string, string> = { month: opts.month };
    if (opts.storeId) params.store_id = opts.storeId;
    const response = await api.get('/hr/attendance/summary', { params });
    return response.data as AttendanceMonthSummary;
  },

  // Admin upsert of one employee's attendance for a single day. Used by the
  // grid Edit modal. Maps to the existing POST /hr/attendance/mark which
  // upserts by (employee_id, date) — no per-cell id needed. status is one of
  // PRESENT/ABSENT/HALF_DAY/LEAVE/HOLIDAY (also accepts LWP/WEEK_OFF server-side).
  markAttendance: async (payload: {
    employee_id: string;
    date: string; // 'YYYY-MM-DD'
    status: string;
    check_in?: string | null;  // ISO datetime
    check_out?: string | null; // ISO datetime
  }) => {
    const body: Record<string, unknown> = {
      employee_id: payload.employee_id,
      date: payload.date,
      status: payload.status,
    };
    if (payload.check_in) body.check_in = payload.check_in;
    if (payload.check_out) body.check_out = payload.check_out;
    const response = await api.post('/hr/attendance/mark', body);
    return response.data as { message: string; date: string };
  },

  // Edit an existing attendance record by its id (forward-compatible with a
  // PUT /hr/attendance/{id} backend route). The grid does not surface per-cell
  // ids today, so the Edit modal uses markAttendance above; this is provided
  // for callers that already hold an attendance id.
  updateAttendance: async (
    attendanceId: string,
    updates: { status?: string; check_in?: string | null; check_out?: string | null },
  ) => {
    const response = await api.put(`/hr/attendance/${attendanceId}`, updates);
    return response.data;
  },

  // --- Shift config (attendance engine) ----------------------------
  getShifts: async (opts?: { storeId?: string; activeOnly?: boolean }) => {
    const params: Record<string, string | boolean> = {};
    if (opts?.storeId) params.store_id = opts.storeId;
    if (opts?.activeOnly !== undefined) params.active_only = opts.activeOnly;
    const response = await api.get('/hr/shifts', { params });
    return response.data as { shifts: Shift[]; total: number };
  },

  createShift: async (payload: {
    name: string;
    start_time: string;
    end_time: string;
    grace_minutes?: number;
    weekly_off?: number[];
    store_id?: string;
  }) => {
    const response = await api.post('/hr/shifts', payload);
    return response.data as { message: string; shift: Shift };
  },

  assignShift: async (employeeId: string, shiftId: string) => {
    const response = await api.post('/hr/shifts/assign', {
      employee_id: employeeId,
      shift_id: shiftId,
    });
    return response.data;
  },

  // --- Late-mark report --------------------------------------------
  getLateMarks: async (opts: { month: string; storeId?: string; employeeId?: string }) => {
    const params: Record<string, string> = { month: opts.month };
    if (opts.storeId) params.store_id = opts.storeId;
    if (opts.employeeId) params.employee_id = opts.employeeId;
    const response = await api.get('/hr/attendance/late-marks', { params });
    return response.data as LateMarksReport;
  },

  // --- LWP report (accountant reads; NOT auto-applied to payroll) ---
  getLwpReport: async (opts: { year: number; month: number; storeId?: string; employeeId?: string }) => {
    const params: Record<string, string | number> = { year: opts.year, month: opts.month };
    if (opts.storeId) params.store_id = opts.storeId;
    if (opts.employeeId) params.employee_id = opts.employeeId;
    const response = await api.get('/hr/reports/lwp', { params });
    return response.data as LwpReport;
  },

  // --- Week-off swap (request -> manager approval) ------------------
  getWeekOffSwaps: async (opts?: { status?: string; storeId?: string; employeeId?: string }) => {
    const params: Record<string, string> = {};
    if (opts?.status) params.status = opts.status;
    if (opts?.storeId) params.store_id = opts.storeId;
    if (opts?.employeeId) params.employee_id = opts.employeeId;
    const response = await api.get('/hr/weekoff-swaps', { params });
    return response.data as { swaps: WeekOffSwap[]; total: number };
  },

  requestWeekOffSwap: async (payload: { from_date: string; to_date: string; reason?: string }) => {
    const response = await api.post('/hr/weekoff-swaps', payload);
    return response.data as { message: string; swap: WeekOffSwap };
  },

  approveWeekOffSwap: async (swapId: string) => {
    const response = await api.post(`/hr/weekoff-swaps/${swapId}/approve`);
    return response.data;
  },

  rejectWeekOffSwap: async (swapId: string, reason: string) => {
    const response = await api.post(`/hr/weekoff-swaps/${swapId}/reject`, null, {
      params: { reason },
    });
    return response.data;
  },
};

// ============================================================================
// Employee documents (govt-ID + HR paperwork) — PII, RBAC + store-scoped.
// ============================================================================
// Bytes are stored server-side in the access-controlled GridFS file store; the
// download path streams them only after the same RBAC + store-scope check. We
// never expose a public URL.
export type EmployeeDocType =
  | 'AADHAAR' | 'PAN' | 'UAN_PF' | 'ESIC' | 'RESUME' | 'PHOTO' | 'OTHER';

export interface EmployeeDocument {
  doc_id: string;
  doc_type: EmployeeDocType;
  filename: string;
  content_type: string;
  size: number;
  file_id: string;
  uploaded_at: string;
  uploaded_by: string;
}

export const employeeDocApi = {
  /** Upload one document for an employee. Multipart; bytes go to GridFS. */
  upload: async (
    employeeId: string,
    file: File,
    docType: EmployeeDocType,
  ): Promise<EmployeeDocument> => {
    const form = new FormData();
    form.append('file', file);
    form.append('doc_type', docType);
    const response = await api.post<EmployeeDocument>(
      `/hr/employees/${employeeId}/documents`,
      form,
      {
        // axios sets the multipart boundary itself; clear the JSON default.
        headers: { 'Content-Type': 'multipart/form-data' },
        // 25 MB on a slow line needs more than the shared 10 s default.
        timeout: 60000,
      },
    );
    return response.data;
  },

  /** List an employee's document metadata (no bytes). */
  list: async (
    employeeId: string,
  ): Promise<{ employee_id: string; documents: EmployeeDocument[] }> => {
    const response = await api.get(`/hr/employees/${employeeId}/documents`);
    return response.data;
  },

  /** Fetch one document's bytes as a Blob (for inline preview / download). */
  downloadBlob: async (
    employeeId: string,
    docId: string,
  ): Promise<{ blob: Blob; mime_type: string }> => {
    const response = await api.get<Blob>(
      `/hr/employees/${employeeId}/documents/${docId}`,
      { responseType: 'blob' },
    );
    const mime_type =
      (response.headers['content-type'] as string) || 'application/octet-stream';
    return { blob: response.data, mime_type };
  },

  /** Remove a document (also best-effort deletes the GridFS blob). */
  remove: async (employeeId: string, docId: string) => {
    const response = await api.delete(
      `/hr/employees/${employeeId}/documents/${docId}`,
    );
    return response.data;
  },
};

// --- Employee self-service response shapes (/hr/me/*) ----------------------

export interface MyAttendanceSummary {
  present: number;
  absent: number;
  half_day: number;
  leave: number;
  holiday: number;
  week_off: number;
  lwp: number;
  late: number;
}

export interface MyAttendance {
  month: number;
  year: number;
  /** date 'YYYY-MM-DD' -> code (P/A/HD/L/LWP/WO/-). */
  days: Record<string, AttendanceCode>;
  summary: MyAttendanceSummary;
}

export interface MyLeaveRow {
  leave_id: string;
  leave_type: string;
  from_date: string;
  to_date: string;
  days: number;
  status: string;
  reason: string;
  applied_at: string;
}

export interface MyLeaves {
  year: number;
  leaves: MyLeaveRow[];
  summary: {
    approved_days: number;
    pending_days: number;
    by_type: Record<string, number>;
  };
}

export interface MyPayslip {
  payslip_id?: string;
  employee_id?: string;
  employee_name?: string;
  month?: number;
  year?: number;
  breakdown?: Record<string, unknown> & {
    gross_salary?: number;
    total_deductions?: number;
    net_pay?: number;
  };
  [k: string]: unknown;
}

export interface MyCommission {
  month: number;
  year: number;
  sales_count: number;
  revenue: number;
  commission_rate_percent: number;
  commission_amount: number;
}

export type AttendanceCode = 'P' | 'A' | 'L' | 'HD' | 'LWP' | 'WO' | '-';

export interface AttendanceGridSummary {
  present: number;
  absent: number;
  leave: number;
  lwp: number;
  half_day: number;
  late: number;
  week_off: number;
}

export interface AttendanceGridEmployee {
  employee_id: string;
  name: string;
  store_id: string;
  days: Record<string, AttendanceCode>;
  summary: AttendanceGridSummary;
}

export interface AttendanceGrid {
  month: string;
  days: number[];
  employees: AttendanceGridEmployee[];
  totals: AttendanceGridSummary;
}

// Compact month-level totals for the HR summary card. Mirrors the grid totals
// shape so the HR card can fall back to the grid if /summary is unavailable.
export interface AttendanceMonthSummary {
  month: string;
  present: number;
  absent: number;
  leave: number;
  half_day: number;
  lwp: number;
  week_off: number;
  late: number;
  employee_count?: number;
}

// weekly_off uses Python weekday() convention: Mon=0 .. Sun=6.
export interface Shift {
  shift_id: string;
  store_id?: string | null;
  name: string;
  start_time: string;   // 'HH:MM'
  end_time: string;     // 'HH:MM'
  grace_minutes: number;
  weekly_off: number[];
  is_active: boolean;
  created_by?: string;
  created_at?: string;
}

export interface LateMarkRow {
  employee_id: string;
  name: string;
  late_count: number;
  total_late_minutes: number;
  avg_late_minutes: number;
  dates: string[];
}

export interface LateMarksReport {
  month: string;
  employees: LateMarkRow[];
  total_late_marks: number;
}

export interface LwpRow {
  employee_id: string;
  name: string;
  lwp_days: number;
  absent_days: number;
  marked_lwp_days: number;
  half_days: number;
  unpaid_leave_days: number;
}

export interface LwpReport {
  year: number;
  month: number;
  employees: LwpRow[];
  total_lwp_days: number;
  note?: string;
}

export type WeekOffSwapStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'CANCELLED';

export interface WeekOffSwap {
  swap_id: string;
  employee_id: string;
  store_id?: string | null;
  from_date: string;
  to_date: string;
  reason?: string;
  status: WeekOffSwapStatus;
  requested_by?: string;
  approved_by?: string;
  approved_at?: string;
  rejection_reason?: string;
  created_at?: string;
}

// ============================================================================
// Incentives API - Staff Incentive Tracking
// ============================================================================


// ============================================================================
// Tasks API
// ============================================================================

export const tasksApi = {
  // Get all tasks with optional filters
  getTasks: async (params?: {
    status?: string;
    priority?: string;
    assigned_to?: string;
    store_id?: string;
    skip?: number;
    limit?: number;
  }) => {
    const response = await api.get('/tasks', { params });
    return response.data;
  },

  // --- SOP templates (Phase 6.14) ----------------------------------
  // sop_templates collection — persistent, role-gated, assignable to
  // roles/users/stores. Replaces the static DEFAULT_CHECKLISTS dict.
  getSopTemplates: async (opts?: { category?: string; storeId?: string; activeOnly?: boolean }) => {
    const params: Record<string, string | boolean> = {};
    if (opts?.category) params.category = opts.category;
    if (opts?.storeId) params.store_id = opts.storeId;
    if (opts?.activeOnly !== undefined) params.active_only = opts.activeOnly;
    const response = await api.get('/tasks/sop-templates', { params });
    return response.data as {
      templates: Array<{
        template_id: string;
        title: string;
        description: string;
        category: string;
        frequency: string;
        estimated_time: number;
        steps: Array<{ step_number: number; instruction: string; warning?: string }>;
        assigned_roles: string[];
        assigned_users: string[];
        store_id: string | null;
        is_active: boolean;
        created_at: string;
        updated_at: string;
      }>;
      total: number;
    };
  },

  createSopTemplate: async (payload: {
    title: string;
    description?: string;
    category?: string;
    frequency?: string;
    estimated_time?: number;
    steps?: Array<{ step_number: number; instruction: string; warning?: string }>;
    assigned_roles?: string[];
    assigned_users?: string[];
    store_id?: string;
  }) => {
    const response = await api.post('/tasks/sop-templates', payload);
    return response.data;
  },

  updateSopTemplate: async (templateId: string, updates: Partial<{
    title: string;
    description: string;
    category: string;
    frequency: string;
    estimated_time: number;
    steps: Array<{ step_number: number; instruction: string; warning?: string }>;
    assigned_roles: string[];
    assigned_users: string[];
    is_active: boolean;
  }>) => {
    const response = await api.patch(`/tasks/sop-templates/${templateId}`, updates);
    return response.data;
  },

  deleteSopTemplate: async (templateId: string) => {
    const response = await api.delete(`/tasks/sop-templates/${templateId}`);
    return response.data;
  },

  assignSop: async (
    templateId: string,
    assignment: { assigned_roles?: string[]; assigned_users?: string[] },
  ) => {
    const response = await api.post(`/tasks/sop-templates/${templateId}/assign`, assignment);
    return response.data;
  },

  // Get tasks assigned to current user
  // Backend route is /tasks/my-tasks (not /tasks/my) — audit Run #2 fix
  getMyTasks: async (includeCompleted: boolean = false) => {
    const response = await api.get('/tasks/my-tasks', { params: { include_completed: includeCompleted } });
    return response.data;
  },

  // Get overdue tasks
  getOverdueTasks: async (storeId?: string) => {
    const response = await api.get('/tasks/overdue', { params: { store_id: storeId } });
    return response.data;
  },

  // Get escalated tasks. Backend doesn't expose a dedicated endpoint yet;
  // fall back to the generic list with escalation_level > 0 on the client.
  getEscalatedTasks: async () => {
    try {
      const response = await api.get('/tasks', { params: { status: 'escalated' } });
      return response.data;
    } catch {
      return { tasks: [], total: 0 };
    }
  },

  // Get task summary/stats
  getTaskSummary: async (storeId?: string) => {
    const response = await api.get('/tasks/summary', { params: { store_id: storeId } });
    return response.data;
  },

  // Variance-driven automation + integrity detectors
  scanPaymentVariance: async (days = 7, storeId?: string) => {
    const response = await api.post('/tasks/scan/payment-variance', null, { params: { days, store_id: storeId } });
    return response.data as { scanned: number; anomalies: number; tasks_created: number; details?: any[] };
  },
  getFakeClosures: async (storeId?: string) => {
    const response = await api.get('/tasks/integrity/fake-closures', { params: { store_id: storeId } });
    return response.data as { flagged: any[]; count: number };
  },
  getSilentTasks: async (storeId?: string) => {
    const response = await api.get('/tasks/integrity/silent', { params: { store_id: storeId } });
    return response.data as { silent: any[]; count: number };
  },

  // Get single task by ID
  getTask: async (taskId: string) => {
    const response = await api.get(`/tasks/${taskId}`);
    return response.data;
  },

  // Create a new task
  createTask: async (task: {
    title: string;
    description?: string;
    priority?: string;
    assigned_to: string;
    due_date: Date | string;
    type?: string;
  }) => {
    const payload = {
      title: task.title,
      description: task.description,
      priority: task.priority || 'P3',
      assigned_to: task.assigned_to,
      due_date: typeof task.due_date === 'string' ? task.due_date : task.due_date.toISOString(),
      type: task.type || 'manual',
    };
    const response = await api.post('/tasks', payload);
    return response.data;
  },

  // Update a task (status, notes, priority, etc.).
  // Backend route is PATCH /tasks/{id} (TaskUpdate) -- the previous PUT 405'd,
  // which is why editing / adding notes appeared to "do nothing".
  updateTask: async (taskId: string, updates: {
    title?: string;
    description?: string;
    priority?: string;
    status?: string;
    notes?: string;
    due_at?: string;
  }) => {
    const response = await api.patch(`/tasks/${taskId}`, updates);
    return response.data;
  },

  // Start a task
  startTask: async (taskId: string) => {
    const response = await api.post(`/tasks/${taskId}/start`);
    return response.data;
  },

  // Complete a task
  completeTask: async (taskId: string, notes: string = '') => {
    const response = await api.patch(`/tasks/${taskId}/complete`, {
      completion_notes: notes
    });
    return response.data;
  },

  // Escalate a task
  escalateTask: async (taskId: string, escalateTo: string, level: number = 1) => {
    const response = await api.post(`/tasks/${taskId}/escalate`, null, {
      params: { escalate_to: escalateTo, level }
    });
    return response.data;
  },

  // Acknowledge a task
  acknowledgeTask: async (taskId: string) => {
    const response = await api.post(`/tasks/${taskId}/acknowledge`);
    return response.data;
  },

  // Reassign a task. Backend expects a JSON body { assigned_to, reason }.
  reassignTask: async (taskId: string, newAssignee: string, reason?: string) => {
    const response = await api.post(`/tasks/${taskId}/reassign`, {
      assigned_to: newAssignee,
      reason,
    });
    return response.data;
  },

  // --- SLA config (Phase 2) ----------------------------------------
  // Per-priority escalation SLA matrix (Standard default + admin overrides).
  getSlaConfig: async () => {
    const response = await api.get('/tasks/sla-config');
    return response.data as {
      matrix: Record<string, { ack_minutes: number; grace_minutes: number }>;
      is_default: boolean;
      updated_at?: string | null;
      updated_by?: string | null;
    };
  },

  updateSlaConfig: async (
    matrix: Record<string, { ack_minutes: number; grace_minutes: number }>,
  ) => {
    const response = await api.put('/tasks/sla-config', { matrix });
    return response.data;
  },

  // --- SOP daily checklists (Phase 4) ------------------------------
  // A checklist is a run of an SOP template at a store on a date.
  getSopChecklist: async (templateId: string, opts?: { date?: string; storeId?: string }) => {
    const params: Record<string, string> = { template_id: templateId };
    if (opts?.date) params.date = opts.date;
    if (opts?.storeId) params.store_id = opts.storeId;
    const response = await api.get('/tasks/sop-checklist', { params });
    return response.data as SopChecklist;
  },

  toggleSopChecklistItem: async (payload: {
    template_id: string;
    step_number: number;
    completed: boolean;
    date?: string;
    store_id?: string;
  }) => {
    const response = await api.post('/tasks/sop-checklist/item', payload);
    return response.data as SopChecklist;
  },

  seedDefaultSops: async (storeId?: string) => {
    const response = await api.post('/tasks/sop-templates/seed-defaults', null, {
      params: storeId ? { store_id: storeId } : {},
    });
    return response.data as { created: number; store_id: string; message: string };
  },
};

export interface SopChecklistItem {
  step_number: number;
  instruction: string;
  warning?: string | null;
  completed: boolean;
  completed_by?: string | null;
  completed_at?: string | null;
}

export interface SopChecklist {
  template_id: string;
  title: string;
  store_id?: string;
  date: string;
  items: SopChecklistItem[];
  progress: { done: number; total: number; percent: number };
  status: string;
}
