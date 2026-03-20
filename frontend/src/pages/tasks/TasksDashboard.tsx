// ============================================================================
// IMS 2.0 - Tasks Dashboard
// ============================================================================
// My Tasks, Daily Checklists, Team Tasks, Create Task, Overdue/Escalated Alerts

import { useState, useEffect } from 'react';
import {
  CheckCircle,
  AlertTriangle,
  Plus,
  Clock,
  Users,
  ListChecks,
  Loader2,
  X,
  AlertCircle,
} from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { tasksApi } from '../../services/api';

type TaskTab = 'my-tasks' | 'checklists' | 'team-tasks';
type ChecklistType = 'opening' | 'closing' | 'stock_count';

interface Task {
  task_id: string;
  title: string;
  description?: string;
  priority: string;  // P0, P1, P2, P3, P4
  status: string;    // open, in_progress, completed, escalated
  assigned_to: string;
  due_date: string;
  type: string;      // manual, sop, system
  created_at: string;
  completed_at?: string;
  escalation_level?: number;
}

interface ChecklistItem {
  text: string;
  completed: boolean;
}

// Priority colors (non-customizable per spec)
const PRIORITY_COLORS: Record<string, { border: string; bg: string; text: string }> = {
  P0: { border: '#991B1B', bg: 'bg-red-950', text: 'text-red-100' },  // dark red
  P1: { border: '#DC2626', bg: 'bg-red-600', text: 'text-white' },    // red
  P2: { border: '#EA580C', bg: 'bg-orange-600', text: 'text-white' }, // orange
  P3: { border: '#CA8A04', bg: 'bg-yellow-600', text: 'text-white' }, // yellow
  P4: { border: '#2563EB', bg: 'bg-blue-600', text: 'text-white' },   // blue
};

const DEFAULT_CHECKLISTS: Record<ChecklistType, ChecklistItem[]> = {
  opening: [
    { text: 'Disarm security system', completed: false },
    { text: 'Turn on all lights and AC', completed: false },
    { text: 'Check cash register float (₹5,000)', completed: false },
    { text: 'Clean all display cases and mirrors', completed: false },
    { text: 'Boot up POS system', completed: false },
    { text: 'Verify network connectivity', completed: false },
    { text: 'Check top 10 SKU stock levels', completed: false },
  ],
  closing: [
    { text: 'Count cash in register (by denomination)', completed: false },
    { text: 'Reconcile all payment methods', completed: false },
    { text: 'Update daily sales Excel sheet', completed: false },
    { text: 'Prepare bank deposit bag', completed: false },
    { text: 'Lock cash in safe (retain ₹5,000)', completed: false },
    { text: 'Clean entire store', completed: false },
    { text: 'Send WhatsApp report to owner', completed: false },
    { text: 'Set security system', completed: false },
  ],
  stock_count: [
    { text: 'Count frames in display', completed: false },
    { text: 'Count lenses in inventory', completed: false },
    { text: 'Check expiry dates', completed: false },
    { text: 'Flag low stock items', completed: false },
    { text: 'Update stock report in system', completed: false },
  ],
};

export function TasksDashboard() {
  const { user } = useAuth();
  const toast = useToast();

  const [activeTab, setActiveTab] = useState<TaskTab>('my-tasks');
  const [isLoading, setIsLoading] = useState(true);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [summary, setSummary] = useState({ total: 0, overdue: 0, escalated: 0, open: 0, completed: 0 });

  // Checklist state
  const [checklistType, setChecklistType] = useState<ChecklistType>('opening');
  const [checklistItems, setChecklistItems] = useState<ChecklistItem[]>(DEFAULT_CHECKLISTS.opening);

  // Create task modal
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newTask, setNewTask] = useState({
    title: '',
    description: '',
    priority: 'P3',
    assigned_to: '',
    due_date: new Date().toISOString().split('T')[0],
    type: 'manual',
  });

  useEffect(() => {
    loadData();
  }, [activeTab]);

  const loadData = async () => {
    setIsLoading(true);
    try {
      // Load my tasks
      const tasksRes = await tasksApi.getTasks();
      setTasks(tasksRes?.tasks || []);

      // Load summary
      const summaryRes = await tasksApi.getTaskSummary();
      setSummary(summaryRes || { total: 0, overdue: 0, escalated: 0, open: 0, completed: 0 });
    } catch (error) {
      toast.error('Failed to load tasks');
    } finally {
      setIsLoading(false);
    }
  };

  const handleCompleteTask = async (taskId: string) => {
    try {
      await tasksApi.completeTask(taskId, 'Completed');
      toast.success('Task completed');
      loadData();
    } catch (error) {
      toast.error('Failed to complete task');
    }
  };

  const handleAcknowledgeTask = async (taskId: string) => {
    try {
      await tasksApi.acknowledgeTask(taskId);
      toast.success('Task acknowledged');
      loadData();
    } catch (error) {
      toast.error('Failed to acknowledge task');
    }
  };

  const handleCreateTask = async () => {
    if (!newTask.title.trim()) {
      toast.error('Task title is required');
      return;
    }

    try {
      await tasksApi.createTask({
        title: newTask.title,
        description: newTask.description,
        priority: newTask.priority,
        assigned_to: newTask.assigned_to || user?.id || '',
        due_date: new Date(newTask.due_date),
        type: newTask.type,
      });

      toast.success('Task created successfully');
      setShowCreateModal(false);
      setNewTask({
        title: '',
        description: '',
        priority: 'P3',
        assigned_to: '',
        due_date: new Date().toISOString().split('T')[0],
        type: 'manual',
      });
      loadData();
    } catch (error) {
      toast.error('Failed to create task');
    }
  };

  const handleChecklistItemToggle = (index: number) => {
    const updated = [...checklistItems];
    updated[index].completed = !updated[index].completed;
    setChecklistItems(updated);
  };

  const handleChecklistTypeChange = (type: ChecklistType) => {
    setChecklistType(type);
    setChecklistItems([...DEFAULT_CHECKLISTS[type]]);
  };

  const completedChecklistItems = checklistItems.filter(item => item.completed).length;
  const checklistProgress = Math.round((completedChecklistItems / checklistItems.length) * 100);

  // Filter tasks for display
  const myTasks = tasks.filter(t => t.assigned_to === user?.id);
  // Unused: const overdueTasks = tasks.filter(t => t.status !== 'completed' && new Date(t.due_date) < new Date());
  // Unused: const escalatedTasks = tasks.filter(t => t.status === 'escalated');

  return (
    <div className="min-h-screen bg-gray-900 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">Tasks & Daily Checklists</h1>
          <p className="text-gray-400">Manage your tasks, checklists, and team assignments</p>
        </div>

        {/* Alert: Overdue/Escalated */}
        {(summary.overdue > 0 || summary.escalated > 0) && (
          <div className="mb-6 bg-red-900 bg-opacity-30 border border-red-500 rounded-lg p-4 flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-red-400" />
            <div>
              <p className="font-semibold text-red-300">
                {summary.overdue} overdue task{summary.overdue !== 1 ? 's' : ''} {summary.escalated > 0 && `• ${summary.escalated} escalated`}
              </p>
              <p className="text-sm text-red-200">Action required immediately</p>
            </div>
          </div>
        )}

        {/* Summary Cards */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
          <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-400 text-sm">Total Tasks</p>
                <p className="text-2xl font-bold text-white">{summary.total}</p>
              </div>
              <ListChecks className="w-8 h-8 text-blue-400" />
            </div>
          </div>

          <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-400 text-sm">Open/In Progress</p>
                <p className="text-2xl font-bold text-yellow-400">{summary.open}</p>
              </div>
              <Clock className="w-8 h-8 text-yellow-400" />
            </div>
          </div>

          <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-400 text-sm">Overdue</p>
                <p className="text-2xl font-bold text-red-400">{summary.overdue}</p>
              </div>
              <AlertCircle className="w-8 h-8 text-red-400" />
            </div>
          </div>

          <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-400 text-sm">Completed</p>
                <p className="text-2xl font-bold text-green-400">{summary.completed}</p>
              </div>
              <CheckCircle className="w-8 h-8 text-green-400" />
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-4 mb-6 border-b border-gray-700">
          <button
            onClick={() => setActiveTab('my-tasks')}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === 'my-tasks'
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            )}
          >
            My Tasks ({myTasks.length})
          </button>

          <button
            onClick={() => setActiveTab('checklists')}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === 'checklists'
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            )}
          >
            Daily Checklists
          </button>

          {(user?.roles?.includes('STORE_MANAGER') || user?.roles?.includes('AREA_MANAGER')) && (
            <button
              onClick={() => setActiveTab('team-tasks')}
              className={clsx(
                'px-4 py-3 font-medium border-b-2 transition-colors',
                activeTab === 'team-tasks'
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-gray-400 hover:text-gray-300'
              )}
            >
              Team Tasks
            </button>
          )}
        </div>

        {/* Content */}
        {isLoading ? (
          <div className="flex items-center justify-center h-64">
            <Loader2 className="w-8 h-8 text-blue-400 animate-spin" />
          </div>
        ) : activeTab === 'my-tasks' ? (
          <div>
            {/* Create Task Button */}
            <button
              onClick={() => setShowCreateModal(true)}
              className="mb-6 flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
            >
              <Plus className="w-4 h-4" />
              Create Task
            </button>

            {/* My Tasks List */}
            <div className="space-y-4">
              {myTasks.length === 0 ? (
                <div className="text-center py-12 bg-gray-800 rounded-lg border border-gray-700">
                  <CheckCircle className="w-12 h-12 text-gray-600 mx-auto mb-3" />
                  <p className="text-gray-400">No tasks assigned to you</p>
                </div>
              ) : (
                myTasks.map(task => (
                  <div
                    key={task.task_id}
                    className="bg-gray-800 rounded-lg border border-gray-700 p-4 hover:border-gray-600 transition-colors"
                    style={{
                      borderLeft: `4px solid ${PRIORITY_COLORS[task.priority]?.border || '#2563EB'}`
                    }}
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-3 mb-2">
                          <h3 className="text-lg font-semibold text-white truncate">{task.title}</h3>
                          <span className={clsx(
                            'text-xs font-bold px-2 py-1 rounded',
                            PRIORITY_COLORS[task.priority]?.bg,
                            PRIORITY_COLORS[task.priority]?.text
                          )}>
                            {task.priority}
                          </span>
                          <span className="text-xs px-2 py-1 rounded bg-gray-700 text-gray-300">
                            {task.status}
                          </span>
                        </div>
                        {task.description && (
                          <p className="text-sm text-gray-400 mb-3">{task.description}</p>
                        )}
                        <div className="flex items-center gap-4 text-sm text-gray-400">
                          <span>Due: {new Date(task.due_date).toLocaleDateString()}</span>
                          <span>Type: {task.type}</span>
                        </div>
                      </div>

                      {/* Action Buttons */}
                      <div className="flex gap-2">
                        {task.status === 'open' && (
                          <button
                            onClick={() => handleAcknowledgeTask(task.task_id)}
                            className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-sm text-white rounded transition-colors"
                          >
                            Acknowledge
                          </button>
                        )}
                        {task.status !== 'completed' && (
                          <button
                            onClick={() => handleCompleteTask(task.task_id)}
                            className="px-3 py-1 bg-green-600 hover:bg-green-700 text-sm text-white rounded transition-colors"
                          >
                            Complete
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        ) : activeTab === 'checklists' ? (
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
            {/* Checklist Type Selector */}
            <div className="mb-6 flex gap-3">
              {(['opening', 'closing', 'stock_count'] as ChecklistType[]).map(type => (
                <button
                  key={type}
                  onClick={() => handleChecklistTypeChange(type)}
                  className={clsx(
                    'px-4 py-2 rounded-lg font-medium transition-colors',
                    checklistType === type
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                  )}
                >
                  {type === 'opening' && 'Opening'}
                  {type === 'closing' && 'Closing'}
                  {type === 'stock_count' && 'Stock Count'}
                </button>
              ))}
            </div>

            {/* Progress Bar */}
            <div className="mb-6">
              <div className="flex items-center justify-between mb-2">
                <p className="text-gray-300 font-medium">Progress</p>
                <p className="text-sm text-gray-400">{completedChecklistItems} of {checklistItems.length} completed</p>
              </div>
              <div className="w-full bg-gray-700 rounded-full h-2">
                <div
                  className="bg-green-500 h-2 rounded-full transition-all duration-300"
                  style={{ width: `${checklistProgress}%` }}
                />
              </div>
            </div>

            {/* Checklist Items */}
            <div className="space-y-3">
              {checklistItems.map((item, index) => (
                <label key={index} className="flex items-start gap-3 p-3 rounded-lg hover:bg-gray-700 transition-colors cursor-pointer">
                  <input
                    type="checkbox"
                    checked={item.completed}
                    onChange={() => handleChecklistItemToggle(index)}
                    className="mt-1 w-5 h-5 rounded border-gray-600 text-green-500 cursor-pointer"
                  />
                  <span className={clsx(
                    'text-sm flex-1',
                    item.completed ? 'line-through text-gray-500' : 'text-gray-300'
                  )}>
                    {item.text}
                  </span>
                </label>
              ))}
            </div>
          </div>
        ) : (
          <div className="text-center py-12 bg-gray-800 rounded-lg border border-gray-700">
            <Users className="w-12 h-12 text-gray-600 mx-auto mb-3" />
            <p className="text-gray-400">Team tasks management coming soon</p>
          </div>
        )}
      </div>

      {/* Create Task Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg border border-gray-700 w-full max-w-md p-6 max-h-screen overflow-y-auto">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-white">Create Task</h2>
              <button
                onClick={() => setShowCreateModal(false)}
                className="text-gray-400 hover:text-gray-300"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4">
              {/* Title */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Task Title *</label>
                <input
                  type="text"
                  value={newTask.title}
                  onChange={e => setNewTask({...newTask, title: e.target.value})}
                  placeholder="Enter task title"
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-500 focus:border-blue-500 focus:outline-none"
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Description</label>
                <textarea
                  value={newTask.description}
                  onChange={e => setNewTask({...newTask, description: e.target.value})}
                  placeholder="Enter task description"
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-500 focus:border-blue-500 focus:outline-none h-24 resize-none"
                />
              </div>

              {/* Priority */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Priority</label>
                <select
                  value={newTask.priority}
                  onChange={e => setNewTask({...newTask, priority: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:border-blue-500 focus:outline-none"
                >
                  <option value="P0">P0 - Critical (Dark Red)</option>
                  <option value="P1">P1 - High (Red)</option>
                  <option value="P2">P2 - Medium (Orange)</option>
                  <option value="P3">P3 - Low (Yellow)</option>
                  <option value="P4">P4 - Info (Blue)</option>
                </select>
              </div>

              {/* Due Date */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Due Date</label>
                <input
                  type="date"
                  value={newTask.due_date}
                  onChange={e => setNewTask({...newTask, due_date: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:border-blue-500 focus:outline-none"
                />
              </div>

              {/* Type */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Type</label>
                <select
                  value={newTask.type}
                  onChange={e => setNewTask({...newTask, type: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:border-blue-500 focus:outline-none"
                >
                  <option value="manual">Manual Task</option>
                  <option value="sop">SOP Template</option>
                  <option value="system">System Generated</option>
                </select>
              </div>

              {/* Action Buttons */}
              <div className="flex gap-3 pt-4">
                <button
                  onClick={handleCreateTask}
                  className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors"
                >
                  Create Task
                </button>
                <button
                  onClick={() => setShowCreateModal(false)}
                  className="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg font-medium transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
