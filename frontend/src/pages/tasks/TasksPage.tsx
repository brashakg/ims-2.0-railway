// ============================================================================
// IMS 2.0 - Tasks Page
// ============================================================================

import { useState } from 'react';
import {
  CheckSquare,
  Square,
  Clock,
  AlertCircle,
  Plus,
  Calendar,
  User,
  Phone,
  ClipboardCheck,
  ListTodo,
} from 'lucide-react';
import clsx from 'clsx';
import { SOPChecklist } from '../../components/sop/SOPChecklist';

type TaskType = 'FOLLOW_UP' | 'CALLBACK' | 'DELIVERY' | 'REMINDER' | 'OTHER';
type TaskPriority = 'LOW' | 'MEDIUM' | 'HIGH';
type TaskStatus = 'PENDING' | 'COMPLETED' | 'CANCELLED';

// Mock tasks
const mockTasks = [
  {
    id: 'task-001',
    type: 'FOLLOW_UP' as TaskType,
    title: 'Follow up on progressive lens order',
    description: 'Customer requested callback about progressive lens comfort',
    customerId: 'cust-002',
    customerName: 'Sunita Sharma',
    customerPhone: '9988776655',
    dueDate: '2025-01-21',
    priority: 'HIGH' as TaskPriority,
    status: 'PENDING' as TaskStatus,
    assignedTo: 'current-user',
    assignedName: 'You',
    createdAt: '2025-01-19T10:00:00Z',
  },
  {
    id: 'task-002',
    type: 'DELIVERY' as TaskType,
    title: 'Notify customer - Order ready',
    description: 'Order BV-KOL-001-2501-0007 is ready for pickup',
    customerId: 'cust-007',
    customerName: 'Vikram Patel',
    customerPhone: '9123400567',
    orderId: 'ord-007',
    dueDate: '2025-01-21',
    priority: 'MEDIUM' as TaskPriority,
    status: 'PENDING' as TaskStatus,
    assignedTo: 'current-user',
    assignedName: 'You',
    createdAt: '2025-01-21T10:00:00Z',
  },
  {
    id: 'task-003',
    type: 'CALLBACK' as TaskType,
    title: 'Callback for contact lens trial',
    description: 'Customer wants to try monthly contact lenses',
    customerId: 'cust-004',
    customerName: 'Ananya Das',
    customerPhone: '9876512345',
    dueDate: '2025-01-22',
    priority: 'LOW' as TaskPriority,
    status: 'PENDING' as TaskStatus,
    assignedTo: 'current-user',
    assignedName: 'You',
    createdAt: '2025-01-20T14:00:00Z',
  },
  {
    id: 'task-004',
    type: 'REMINDER' as TaskType,
    title: 'Annual eye checkup reminder',
    description: 'Send reminder for annual eye checkup',
    customerId: 'cust-001',
    customerName: 'Rajesh Kumar',
    customerPhone: '9876543210',
    dueDate: '2025-01-25',
    priority: 'LOW' as TaskPriority,
    status: 'PENDING' as TaskStatus,
    assignedTo: 'current-user',
    assignedName: 'You',
    createdAt: '2025-01-15T09:00:00Z',
  },
  {
    id: 'task-005',
    type: 'FOLLOW_UP' as TaskType,
    title: 'Check on lens adaptation',
    description: 'Follow up on new progressive lens adaptation',
    customerId: 'cust-003',
    customerName: 'ABC Enterprises',
    customerPhone: '9123456789',
    dueDate: '2025-01-20',
    priority: 'MEDIUM' as TaskPriority,
    status: 'COMPLETED' as TaskStatus,
    assignedTo: 'current-user',
    assignedName: 'You',
    completedAt: '2025-01-20T16:00:00Z',
    createdAt: '2025-01-18T11:00:00Z',
  },
];

const TYPE_CONFIG: Record<TaskType, { label: string; color: string }> = {
  FOLLOW_UP: { label: 'Follow Up', color: 'bg-blue-100 text-blue-600' },
  CALLBACK: { label: 'Callback', color: 'bg-purple-100 text-purple-600' },
  DELIVERY: { label: 'Delivery', color: 'bg-green-100 text-green-600' },
  REMINDER: { label: 'Reminder', color: 'bg-orange-100 text-orange-600' },
  OTHER: { label: 'Other', color: 'bg-gray-100 text-gray-600' },
};

const PRIORITY_CONFIG: Record<TaskPriority, { label: string; class: string }> = {
  LOW: { label: 'Low', class: 'text-gray-500' },
  MEDIUM: { label: 'Medium', class: 'text-yellow-600' },
  HIGH: { label: 'High', class: 'text-red-600' },
};

export function TasksPage() {
  const [activeTab, setActiveTab] = useState<'tasks' | 'sop'>('tasks');
  const [filter, setFilter] = useState<'all' | 'pending' | 'completed'>('pending');
  const [typeFilter, setTypeFilter] = useState<TaskType | 'ALL'>('ALL');

  // Filter tasks
  const filteredTasks = mockTasks.filter(task => {
    const matchesStatus = filter === 'all' ||
      (filter === 'pending' && task.status === 'PENDING') ||
      (filter === 'completed' && task.status === 'COMPLETED');

    const matchesType = typeFilter === 'ALL' || task.type === typeFilter;

    return matchesStatus && matchesType;
  });

  // Stats
  const pendingCount = mockTasks.filter(t => t.status === 'PENDING').length;
  const overdueCount = mockTasks.filter(t => {
    return t.status === 'PENDING' && new Date(t.dueDate) < new Date();
  }).length;
  const todayCount = mockTasks.filter(t => {
    const today = new Date().toISOString().split('T')[0];
    return t.status === 'PENDING' && t.dueDate === today;
  }).length;

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);

    if (date.toDateString() === today.toDateString()) return 'Today';
    if (date.toDateString() === tomorrow.toDateString()) return 'Tomorrow';

    return date.toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
    });
  };

  const isOverdue = (dueDate: string, status: TaskStatus) => {
    return status === 'PENDING' && new Date(dueDate) < new Date();
  };

  const toggleTaskStatus = (taskId: string) => {
    // In production, this would call an API
    void taskId; // TODO: Implement task status toggle
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Tasks & SOP</h1>
          <p className="text-gray-500">Manage follow-ups, reminders, and operational checklists</p>
        </div>
        {activeTab === 'tasks' && (
          <button className="btn-primary flex items-center gap-2">
            <Plus className="w-4 h-4" />
            New Task
          </button>
        )}
      </div>

      {/* Main Tabs */}
      <div className="flex border-b border-gray-200">
        <button
          onClick={() => setActiveTab('tasks')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'tasks'
              ? 'border-bv-red-600 text-bv-red-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          )}
        >
          <ListTodo className="w-4 h-4" />
          Tasks
          {pendingCount > 0 && (
            <span className="ml-1 px-2 py-0.5 rounded-full bg-blue-100 text-blue-600 text-xs">
              {pendingCount}
            </span>
          )}
        </button>
        <button
          onClick={() => setActiveTab('sop')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'sop'
              ? 'border-bv-red-600 text-bv-red-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          )}
        >
          <ClipboardCheck className="w-4 h-4" />
          SOP Checklists
        </button>
      </div>

      {/* SOP Tab Content */}
      {activeTab === 'sop' && <SOPChecklist />}

      {/* Tasks Tab Content */}
      {activeTab === 'tasks' && (
        <>
          {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <CheckSquare className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Pending</p>
              <p className="text-2xl font-bold text-gray-900">{pendingCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
              <Calendar className="w-5 h-5 text-yellow-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Due Today</p>
              <p className="text-2xl font-bold text-yellow-600">{todayCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
              <AlertCircle className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Overdue</p>
              <p className="text-2xl font-bold text-red-600">{overdueCount}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4 items-center justify-between">
          <div className="flex gap-2">
            {(['pending', 'completed', 'all'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={clsx(
                  'px-4 py-2 rounded-lg text-sm font-medium transition-colors capitalize',
                  filter === f
                    ? 'bg-bv-red-600 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                )}
              >
                {f}
              </button>
            ))}
          </div>
          <select
            value={typeFilter}
            onChange={e => setTypeFilter(e.target.value as typeof typeFilter)}
            className="input-field w-auto"
          >
            <option value="ALL">All Types</option>
            {Object.entries(TYPE_CONFIG).map(([type, config]) => (
              <option key={type} value={type}>{config.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Tasks List */}
      <div className="space-y-3">
        {filteredTasks.length === 0 ? (
          <div className="card text-center py-12 text-gray-500">
            <CheckSquare className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>No tasks found</p>
          </div>
        ) : (
          filteredTasks.map(task => {
            const typeConfig = TYPE_CONFIG[task.type];
            const priorityConfig = PRIORITY_CONFIG[task.priority];
            const overdue = isOverdue(task.dueDate, task.status);

            return (
              <div
                key={task.id}
                className={clsx(
                  'card',
                  task.status === 'COMPLETED' && 'opacity-60',
                  overdue && 'border-red-300 bg-red-50'
                )}
              >
                <div className="flex items-start gap-4">
                  {/* Checkbox */}
                  <button
                    onClick={() => toggleTaskStatus(task.id)}
                    className="mt-1"
                  >
                    {task.status === 'COMPLETED' ? (
                      <CheckSquare className="w-5 h-5 text-green-600" />
                    ) : (
                      <Square className="w-5 h-5 text-gray-400 hover:text-bv-red-600" />
                    )}
                  </button>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={clsx(
                        'px-2 py-0.5 rounded-full text-xs font-medium',
                        typeConfig.color
                      )}>
                        {typeConfig.label}
                      </span>
                      <span className={clsx('text-xs font-medium', priorityConfig.class)}>
                        {priorityConfig.label} Priority
                      </span>
                      {overdue && (
                        <span className="badge-error">Overdue</span>
                      )}
                    </div>

                    <p className={clsx(
                      'font-medium text-gray-900',
                      task.status === 'COMPLETED' && 'line-through'
                    )}>
                      {task.title}
                    </p>
                    <p className="text-sm text-gray-500 mt-1">{task.description}</p>

                    <div className="flex items-center gap-4 mt-2 text-sm text-gray-500">
                      <span className="flex items-center gap-1">
                        <User className="w-3 h-3" />
                        {task.customerName}
                      </span>
                      <span className="flex items-center gap-1">
                        <Phone className="w-3 h-3" />
                        {task.customerPhone}
                      </span>
                    </div>
                  </div>

                  {/* Due Date */}
                  <div className="text-right">
                    <div className={clsx(
                      'flex items-center gap-1 text-sm font-medium',
                      overdue ? 'text-red-600' : 'text-gray-600'
                    )}>
                      <Clock className="w-4 h-4" />
                      {formatDate(task.dueDate)}
                    </div>
                    {task.completedAt && (
                      <p className="text-xs text-gray-400 mt-1">
                        Completed {formatDate(task.completedAt)}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
        </>
      )}
    </div>
  );
}

export default TasksPage;
