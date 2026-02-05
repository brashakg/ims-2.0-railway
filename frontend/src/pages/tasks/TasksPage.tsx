// ============================================================================
// IMS 2.0 - Tasks Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect, useCallback } from 'react';
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
  RefreshCw,
  Loader2,
} from 'lucide-react';
import clsx from 'clsx';
import { SOPChecklist } from '../../components/sop/SOPChecklist';
import { tasksApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';

type TaskCategory = 'FOLLOW_UP' | 'CALLBACK' | 'DELIVERY' | 'REMINDER' | 'ORDER' | 'OTHER';
type TaskPriority = 'P1' | 'P2' | 'P3' | 'P4';
type TaskStatus = 'OPEN' | 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED' | 'ESCALATED';

interface Task {
  task_id: string;
  task_number: string;
  title: string;
  description?: string;
  category: TaskCategory;
  priority: TaskPriority;
  status: TaskStatus;
  assigned_to: string;
  assigned_name?: string;
  store_id: string;
  due_at: string;
  linked_entity_type?: string;
  linked_entity_id?: string;
  customer_id?: string;
  customer_name?: string;
  customer_phone?: string;
  created_at: string;
  completed_at?: string;
  created_by: string;
}

const CATEGORY_CONFIG: Record<TaskCategory, { label: string; color: string }> = {
  FOLLOW_UP: { label: 'Follow Up', color: 'bg-blue-100 text-blue-600' },
  CALLBACK: { label: 'Callback', color: 'bg-purple-100 text-purple-600' },
  DELIVERY: { label: 'Delivery', color: 'bg-green-100 text-green-600' },
  REMINDER: { label: 'Reminder', color: 'bg-orange-100 text-orange-600' },
  ORDER: { label: 'Order', color: 'bg-cyan-100 text-cyan-600' },
  OTHER: { label: 'Other', color: 'bg-gray-100 text-gray-600' },
};

const PRIORITY_CONFIG: Record<TaskPriority, { label: string; class: string }> = {
  P1: { label: 'Critical', class: 'text-red-600 font-bold' },
  P2: { label: 'High', class: 'text-red-500' },
  P3: { label: 'Medium', class: 'text-yellow-600' },
  P4: { label: 'Low', class: 'text-gray-500' },
};

export function TasksPage() {
  const { user } = useAuth();
  const toast = useToast();

  const [activeTab, setActiveTab] = useState<'tasks' | 'sop'>('tasks');
  const [filter, setFilter] = useState<'all' | 'pending' | 'completed'>('pending');
  const [categoryFilter, setCategoryFilter] = useState<TaskCategory | 'ALL'>('ALL');

  // API data states
  const [tasks, setTasks] = useState<Task[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState({ pending: 0, today: 0, overdue: 0 });
  const [isUpdating, setIsUpdating] = useState<string | null>(null);

  // Load tasks from API
  const loadTasks = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      // Get tasks based on filter
      const includeCompleted = filter === 'all' || filter === 'completed';
      const response = await tasksApi.getMyTasks(includeCompleted);

      let taskList: Task[] = response?.tasks || [];

      // Apply client-side filters
      if (filter === 'pending') {
        taskList = taskList.filter(t => t.status === 'OPEN' || t.status === 'IN_PROGRESS' || t.status === 'ESCALATED');
      } else if (filter === 'completed') {
        taskList = taskList.filter(t => t.status === 'COMPLETED');
      }

      if (categoryFilter !== 'ALL') {
        taskList = taskList.filter(t => t.category === categoryFilter);
      }

      setTasks(taskList);

      // Load overdue count
      const overdueResponse = await tasksApi.getOverdueTasks(user?.activeStoreId);

      const today = new Date().toISOString().split('T')[0];
      const todayTasks = taskList.filter(t =>
        (t.status === 'OPEN' || t.status === 'IN_PROGRESS') &&
        t.due_at?.startsWith(today)
      );

      setSummary({
        pending: taskList.filter(t => t.status === 'OPEN' || t.status === 'IN_PROGRESS').length,
        today: todayTasks.length,
        overdue: overdueResponse?.total || 0,
      });

    } catch (err) {
      console.error('Failed to load tasks:', err);
      setError('Failed to load tasks. Please try again.');
    } finally {
      setIsLoading(false);
    }
  }, [filter, categoryFilter, user?.activeStoreId]);

  // Load tasks on mount and when filters change
  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  // Toggle task completion
  const toggleTaskStatus = async (task: Task) => {
    setIsUpdating(task.task_id);

    try {
      if (task.status === 'COMPLETED') {
        // Can't un-complete a task via API
        toast.warning('Completed tasks cannot be reopened');
        return;
      }

      // Complete the task
      await tasksApi.completeTask(task.task_id);
      toast.success('Task marked as completed');

      // Reload tasks
      await loadTasks();

    } catch (err) {
      console.error('Failed to update task:', err);
      toast.error('Failed to update task');
    } finally {
      setIsUpdating(null);
    }
  };

  // Start a task
  const handleStartTask = async (task: Task) => {
    if (task.status !== 'OPEN') return;

    setIsUpdating(task.task_id);
    try {
      await tasksApi.startTask(task.task_id);
      toast.success('Task started');
      await loadTasks();
    } catch (err) {
      console.error('Failed to start task:', err);
      toast.error('Failed to start task');
    } finally {
      setIsUpdating(null);
    }
  };

  const formatDate = (dateStr: string) => {
    if (!dateStr) return '';
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
    if (!dueDate) return false;
    return (status === 'OPEN' || status === 'IN_PROGRESS') && new Date(dueDate) < new Date();
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Tasks & SOP</h1>
          <p className="text-gray-500">Manage follow-ups, reminders, and operational checklists</p>
        </div>
        <div className="flex gap-2">
          {activeTab === 'tasks' && (
            <>
              <button
                onClick={loadTasks}
                disabled={isLoading}
                className="btn-secondary flex items-center gap-2"
              >
                <RefreshCw className={clsx('w-4 h-4', isLoading && 'animate-spin')} />
                Refresh
              </button>
              <button
                onClick={() => toast.info('New task modal coming soon')}
                className="btn-primary flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                New Task
              </button>
            </>
          )}
        </div>
      </div>

      {/* Main Tabs */}
      <div className="flex border-b border-gray-200">
        <button
          onClick={() => setActiveTab('tasks')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'tasks'
              ? 'border-bv-gold-600 text-bv-gold-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          )}
        >
          <ListTodo className="w-4 h-4" />
          Tasks
          {summary.pending > 0 && (
            <span className="ml-1 px-2 py-0.5 rounded-full bg-blue-100 text-blue-600 text-xs">
              {summary.pending}
            </span>
          )}
        </button>
        <button
          onClick={() => setActiveTab('sop')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'sop'
              ? 'border-bv-gold-600 text-bv-gold-600'
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
                  <p className="text-2xl font-bold text-gray-900">{summary.pending}</p>
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
                  <p className="text-2xl font-bold text-yellow-600">{summary.today}</p>
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
                  <p className="text-2xl font-bold text-red-600">{summary.overdue}</p>
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
                        ? 'bg-bv-gold-600 text-white'
                        : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                    )}
                  >
                    {f}
                  </button>
                ))}
              </div>
              <select
                value={categoryFilter}
                onChange={e => setCategoryFilter(e.target.value as typeof categoryFilter)}
                className="input-field w-auto"
              >
                <option value="ALL">All Types</option>
                {Object.entries(CATEGORY_CONFIG).map(([type, config]) => (
                  <option key={type} value={type}>{config.label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Error State */}
          {error && (
            <div className="card bg-red-50 border-red-200 text-red-700 flex items-center gap-3">
              <AlertCircle className="w-5 h-5" />
              <span>{error}</span>
              <button onClick={loadTasks} className="ml-auto text-sm underline">
                Retry
              </button>
            </div>
          )}

          {/* Loading State */}
          {isLoading ? (
            <div className="card text-center py-12">
              <Loader2 className="w-8 h-8 mx-auto mb-2 animate-spin text-bv-gold-600" />
              <p className="text-gray-500">Loading tasks...</p>
            </div>
          ) : (
            /* Tasks List */
            <div className="space-y-3">
              {tasks.length === 0 ? (
                <div className="card text-center py-12 text-gray-500">
                  <CheckSquare className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p>No tasks found</p>
                  <p className="text-sm mt-1">Tasks will appear here when created</p>
                </div>
              ) : (
                tasks.map(task => {
                  const categoryConfig = CATEGORY_CONFIG[task.category] || CATEGORY_CONFIG.OTHER;
                  const priorityConfig = PRIORITY_CONFIG[task.priority] || PRIORITY_CONFIG.P3;
                  const overdue = isOverdue(task.due_at, task.status);
                  const updating = isUpdating === task.task_id;

                  return (
                    <div
                      key={task.task_id}
                      className={clsx(
                        'card',
                        task.status === 'COMPLETED' && 'opacity-60',
                        overdue && 'border-red-300 bg-red-50'
                      )}
                    >
                      <div className="flex items-start gap-4">
                        {/* Checkbox */}
                        <button
                          onClick={() => toggleTaskStatus(task)}
                          disabled={updating}
                          className="mt-1"
                        >
                          {updating ? (
                            <Loader2 className="w-5 h-5 text-gray-400 animate-spin" />
                          ) : task.status === 'COMPLETED' ? (
                            <CheckSquare className="w-5 h-5 text-green-600" />
                          ) : (
                            <Square className="w-5 h-5 text-gray-400 hover:text-bv-gold-600" />
                          )}
                        </button>

                        {/* Content */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <span className={clsx(
                              'px-2 py-0.5 rounded-full text-xs font-medium',
                              categoryConfig.color
                            )}>
                              {categoryConfig.label}
                            </span>
                            <span className={clsx('text-xs font-medium', priorityConfig.class)}>
                              {priorityConfig.label}
                            </span>
                            {task.status === 'IN_PROGRESS' && (
                              <span className="badge-info">In Progress</span>
                            )}
                            {task.status === 'ESCALATED' && (
                              <span className="badge-warning">Escalated</span>
                            )}
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
                          {task.description && (
                            <p className="text-sm text-gray-500 mt-1">{task.description}</p>
                          )}

                          <div className="flex items-center gap-4 mt-2 text-sm text-gray-500 flex-wrap">
                            {task.customer_name && (
                              <span className="flex items-center gap-1">
                                <User className="w-3 h-3" />
                                {task.customer_name}
                              </span>
                            )}
                            {task.customer_phone && (
                              <span className="flex items-center gap-1">
                                <Phone className="w-3 h-3" />
                                {task.customer_phone}
                              </span>
                            )}
                            <span className="text-xs text-gray-400">
                              #{task.task_number}
                            </span>
                          </div>
                        </div>

                        {/* Due Date & Actions */}
                        <div className="text-right flex flex-col items-end gap-2">
                          <div className={clsx(
                            'flex items-center gap-1 text-sm font-medium',
                            overdue ? 'text-red-600' : 'text-gray-600'
                          )}>
                            <Clock className="w-4 h-4" />
                            {formatDate(task.due_at)}
                          </div>
                          {task.completed_at && (
                            <p className="text-xs text-gray-400">
                              Completed {formatDate(task.completed_at)}
                            </p>
                          )}
                          {task.status === 'OPEN' && (
                            <button
                              onClick={() => handleStartTask(task)}
                              disabled={updating}
                              className="text-xs text-bv-gold-600 hover:underline"
                            >
                              Start Task
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default TasksPage;
