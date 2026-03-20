// ============================================================================
// IMS 2.0 - HR / Payroll API
// ============================================================================

import api from './client';

export const hrApi = {
  getAttendance: async (storeId: string, date?: string) => {
    const response = await api.get('/hr/attendance', { params: { store_id: storeId, date } });
    return response.data;
  },

  checkIn: async (storeId: string, latitude: number, longitude: number) => {
    const response = await api.post('/hr/attendance/check-in', {
      store_id: storeId,
      latitude,
      longitude,
    });
    return response.data;
  },

  checkOut: async (attendanceId: string) => {
    const response = await api.post(`/hr/attendance/${attendanceId}/check-out`);
    return response.data;
  },

  getLeaves: async (params?: { userId?: string; status?: string }) => {
    const response = await api.get('/hr/leaves', { params });
    return response.data;
  },

  applyLeave: async (data: Partial<import('../../types').Leave>) => {
    const response = await api.post('/hr/leaves', data);
    return response.data;
  },

  approveLeave: async (leaveId: string, approved: boolean, remarks?: string) => {
    const response = await api.post(`/hr/leaves/${leaveId}/approve`, { approved, remarks });
    return response.data;
  },
};

// ============================================================================
// Incentives API - Staff Incentive Tracking
// ============================================================================

export const incentivesApi = {
  getDashboard: async (month?: number, year?: number) => {
    const params: Record<string, any> = {};
    if (month) params.month = month;
    if (year) params.year = year;
    const response = await api.get('/incentives/dashboard', { params });
    return response.data;
  },

  getLeaderboard: async (month?: number, year?: number, limit?: number) => {
    const params: Record<string, any> = {};
    if (month) params.month = month;
    if (year) params.year = year;
    if (limit) params.limit = limit;
    const response = await api.get('/incentives/leaderboard', { params });
    return response.data;
  },

  getStaffTargets: async (staffId: string, month?: number, year?: number) => {
    const params: Record<string, any> = {};
    if (month) params.month = month;
    if (year) params.year = year;
    const response = await api.get(`/incentives/targets/${staffId}`, { params });
    return response.data;
  },

  setTargets: async (data: { staff_id: string; target_amount: number; month: number; year: number; description?: string }) => {
    const response = await api.post('/incentives/targets', data);
    return response.data;
  },

  recordKicker: async (data: { brand: string; product_name?: string; sale_amount: number; sale_date?: string }, staffId?: string) => {
    const params: Record<string, any> = {};
    if (staffId) params.staff_id = staffId;
    const response = await api.post('/incentives/kickers', data, { params });
    return response.data;
  },

  getKickers: async (staffId: string, month?: number, year?: number) => {
    const params: Record<string, any> = {};
    if (month) params.month = month;
    if (year) params.year = year;
    const response = await api.get(`/incentives/kickers/${staffId}`, { params });
    return response.data;
  },
};

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

  // Get tasks assigned to current user
  getMyTasks: async (includeCompleted: boolean = false) => {
    const response = await api.get('/tasks/my', { params: { include_completed: includeCompleted } });
    return response.data;
  },

  // Get overdue tasks
  getOverdueTasks: async (storeId?: string) => {
    const response = await api.get('/tasks/overdue', { params: { store_id: storeId } });
    return response.data;
  },

  // Get escalated tasks
  getEscalatedTasks: async () => {
    const response = await api.get('/tasks/escalated');
    return response.data;
  },

  // Get task summary/stats
  getTaskSummary: async (storeId?: string) => {
    const response = await api.get('/tasks/summary', { params: { store_id: storeId } });
    return response.data;
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

  // Update a task
  updateTask: async (taskId: string, updates: {
    title?: string;
    description?: string;
    priority?: string;
    due_at?: string;
  }) => {
    const response = await api.put(`/tasks/${taskId}`, updates);
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

  // Reassign a task
  reassignTask: async (taskId: string, newAssignee: string) => {
    const response = await api.post(`/tasks/${taskId}/reassign`, null, {
      params: { new_assignee: newAssignee }
    });
    return response.data;
  },
};
