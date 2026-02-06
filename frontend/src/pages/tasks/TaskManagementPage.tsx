// ============================================================================
// IMS 2.0 - Task & SOP Management System
// ============================================================================
// Coordinate 40 employees with tasks, SOPs, and accountability tracking

import { useState, useEffect } from 'react';
import {
  CheckSquare,
  Plus,
  Search,
  User,
  Users,
  Clock,
  CheckCircle,
  XCircle,
  AlertTriangle,
  ListChecks,
  TrendingUp,
  Target,
  Loader2,
  Eye,
  Edit,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

type TabType = 'my-tasks' | 'team-tasks' | 'sop' | 'analytics';
type TaskStatus = 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'OVERDUE';
type TaskPriority = 'LOW' | 'MEDIUM' | 'HIGH' | 'URGENT';

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
  dueDate: string;
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

interface EmployeePerformance {
  employeeId: string;
  employeeName: string;
  role: string;
  storeId: string;
  storeName: string;
  tasksAssigned: number;
  tasksCompleted: number;
  tasksOverdue: number;
  completionRate: number;
  avgCompletionTime: number; // hours
  rating: number; // 1-5
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
  const [employeePerformance, setEmployeePerformance] = useState<EmployeePerformance[]>([]);
  const [_showCreateTask, setShowCreateTask] = useState(false);
  const [_selectedTask, setSelectedTask] = useState<Task | null>(null);

  useEffect(() => {
    loadData();
  }, [activeTab]);

  const loadData = async () => {
    setIsLoading(true);
    try {
      await new Promise(resolve => setTimeout(resolve, 800));

      // Mock tasks
      setTasks([
        {
          id: '1',
          title: 'Morning Store Opening Checklist',
          description: 'Complete all opening procedures as per SOP',
          assignedTo: 'emp1',
          assignedToName: 'Rajesh Kumar',
          assignedBy: 'admin',
          assignedByName: 'Admin',
          status: 'COMPLETED',
          priority: 'HIGH',
          dueDate: '2024-02-06',
          createdDate: '2024-02-06',
          completedDate: '2024-02-06',
          storeId: '1',
          storeName: 'Main Branch',
          category: 'DAILY',
          checklistItems: [
            { id: '1', text: 'Turn on all lights and AC', completed: true },
            { id: '2', text: 'Check cash register and starting balance', completed: true },
            { id: '3', text: 'Clean display cases', completed: true },
            { id: '4', text: 'Verify stock for high-demand items', completed: true },
            { id: '5', text: 'Check WhatsApp for customer messages', completed: true },
          ],
        },
        {
          id: '2',
          title: 'Update Inventory Stock Counts',
          description: 'Physical count of all fast-moving items and update system',
          assignedTo: 'emp2',
          assignedToName: 'Priya Patel',
          assignedBy: 'admin',
          assignedByName: 'Admin',
          status: 'IN_PROGRESS',
          priority: 'MEDIUM',
          dueDate: '2024-02-06',
          createdDate: '2024-02-05',
          storeId: '1',
          storeName: 'Main Branch',
          category: 'DAILY',
          checklistItems: [
            { id: '1', text: 'Count Ray-Ban inventory', completed: true },
            { id: '2', text: 'Count Titan frames', completed: true },
            { id: '3', text: 'Count contact lenses', completed: false },
            { id: '4', text: 'Update system with counts', completed: false },
          ],
        },
        {
          id: '3',
          title: 'Customer Follow-up Calls - Lens Delivery',
          description: 'Call 5 customers whose lenses are ready for delivery',
          assignedTo: 'emp3',
          assignedToName: 'Amit Sharma',
          assignedBy: 'admin',
          assignedByName: 'Admin',
          status: 'OVERDUE',
          priority: 'URGENT',
          dueDate: '2024-02-05',
          createdDate: '2024-02-04',
          storeId: '2',
          storeName: 'Mall Road',
          category: 'DAILY',
        },
        {
          id: '4',
          title: 'Monthly GST Report Preparation',
          description: 'Compile all invoices and prepare GSTR-1 report',
          assignedTo: 'emp4',
          assignedToName: 'Sneha Desai',
          assignedBy: 'admin',
          assignedByName: 'Admin',
          status: 'PENDING',
          priority: 'HIGH',
          dueDate: '2024-02-10',
          createdDate: '2024-02-06',
          storeId: '1',
          storeName: 'Main Branch',
          category: 'MONTHLY',
        },
      ]);

      // Mock SOPs
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
      ]);

      // Mock employee performance
      setEmployeePerformance([
        {
          employeeId: 'emp1',
          employeeName: 'Rajesh Kumar',
          role: 'Store Manager',
          storeId: '1',
          storeName: 'Main Branch',
          tasksAssigned: 45,
          tasksCompleted: 43,
          tasksOverdue: 1,
          completionRate: 95.6,
          avgCompletionTime: 4.2,
          rating: 4.8,
        },
        {
          employeeId: 'emp2',
          employeeName: 'Priya Patel',
          role: 'Sales Associate',
          storeId: '1',
          storeName: 'Main Branch',
          tasksAssigned: 52,
          tasksCompleted: 48,
          tasksOverdue: 2,
          completionRate: 92.3,
          avgCompletionTime: 5.1,
          rating: 4.5,
        },
        {
          employeeId: 'emp3',
          employeeName: 'Amit Sharma',
          role: 'Sales Associate',
          storeId: '2',
          storeName: 'Mall Road',
          tasksAssigned: 38,
          tasksCompleted: 30,
          tasksOverdue: 5,
          completionRate: 78.9,
          avgCompletionTime: 8.3,
          rating: 3.5,
        },
        {
          employeeId: 'emp4',
          employeeName: 'Sneha Desai',
          role: 'Accountant',
          storeId: '1',
          storeName: 'Main Branch',
          tasksAssigned: 28,
          tasksCompleted: 27,
          tasksOverdue: 0,
          completionRate: 96.4,
          avgCompletionTime: 3.8,
          rating: 5.0,
        },
      ]);

    } catch (error: any) {
      toast.error('Failed to load task data');
    } finally {
      setIsLoading(false);
    }
  };

  const getStatusBadge = (status: TaskStatus) => {
    const config = {
      PENDING: { label: 'Pending', color: 'bg-gray-100 text-gray-800', icon: Clock },
      IN_PROGRESS: { label: 'In Progress', color: 'bg-blue-100 text-blue-800', icon: Target },
      COMPLETED: { label: 'Completed', color: 'bg-green-100 text-green-800', icon: CheckCircle },
      OVERDUE: { label: 'Overdue', color: 'bg-red-100 text-red-800', icon: AlertTriangle },
    };

    const { label, color, icon: Icon } = config[status];

    return (
      <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${color}`}>
        <Icon className="w-3 h-3" />
        {label}
      </span>
    );
  };

  const getPriorityBadge = (priority: TaskPriority) => {
    const config = {
      LOW: { label: 'Low', color: 'bg-gray-100 text-gray-700' },
      MEDIUM: { label: 'Medium', color: 'bg-yellow-100 text-yellow-800' },
      HIGH: { label: 'High', color: 'bg-orange-100 text-orange-800' },
      URGENT: { label: 'Urgent', color: 'bg-red-100 text-red-800' },
    };

    const { label, color } = config[priority];

    return (
      <span className={`px-2 py-1 rounded text-xs font-medium ${color}`}>
        {label}
      </span>
    );
  };

  const myTasks = tasks.filter(task => task.assignedTo === user?.id || user?.roles?.includes('ADMIN') || user?.roles?.includes('SUPERADMIN'));
  const filteredTasks = (activeTab === 'my-tasks' ? myTasks : tasks).filter(task => {
    const matchesSearch = task.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          task.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'ALL' || task.status === statusFilter;
    const matchesPriority = priorityFilter === 'ALL' || task.priority === priorityFilter;
    return matchesSearch && matchesStatus && matchesPriority;
  });

  const filteredSOPs = sops.filter(sop =>
    sop.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
    sop.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
    sop.category.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <CheckSquare className="w-7 h-7 text-green-600" />
            Task & SOP Management
          </h1>
          <p className="text-gray-500 mt-1">Coordinate 40 employees with tasks, checklists, and SOPs</p>
        </div>
        <button
          onClick={() => setShowCreateTask(true)}
          className="btn-primary flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          {activeTab === 'sop' ? 'Create SOP' : 'Create Task'}
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-8">
          <button
            onClick={() => setActiveTab('my-tasks')}
            className={`pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'my-tasks'
                ? 'border-green-600 text-green-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <User className="w-4 h-4" />
              My Tasks
              <span className="px-2 py-0.5 bg-green-100 text-green-700 rounded-full text-xs font-medium">
                {myTasks.filter(t => t.status !== 'COMPLETED').length}
              </span>
            </div>
          </button>
          <button
            onClick={() => setActiveTab('team-tasks')}
            className={`pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'team-tasks'
                ? 'border-green-600 text-green-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <Users className="w-4 h-4" />
              All Team Tasks
            </div>
          </button>
          <button
            onClick={() => setActiveTab('sop')}
            className={`pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'sop'
                ? 'border-green-600 text-green-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <ListChecks className="w-4 h-4" />
              SOPs & Procedures
            </div>
          </button>
          <button
            onClick={() => setActiveTab('analytics')}
            className={`pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'analytics'
                ? 'border-green-600 text-green-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4" />
              Team Performance
            </div>
          </button>
        </nav>
      </div>

      {/* Search & Filters */}
      {activeTab !== 'analytics' && (
        <div className="flex items-center gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
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
                  <button className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
                    <Eye className="w-4 h-4 text-gray-600" />
                  </button>
                  <button className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
                    <Edit className="w-4 h-4 text-gray-600" />
                  </button>
                </div>
              </div>
            </div>
          ))}

          {filteredSOPs.length === 0 && (
            <div className="col-span-2 text-center py-12">
              <ListChecks className="w-12 h-12 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-500">No SOPs found</p>
            </div>
          )}
        </div>
      ) : activeTab === 'analytics' ? (
        /* Team Performance Analytics */
        <div className="space-y-6">
          <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
                  <Users className="w-5 h-5 text-green-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Active Employees</p>
                  <p className="text-2xl font-bold text-gray-900">40</p>
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
                  <p className="text-2xl font-bold text-gray-900">148/163</p>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
                  <TrendingUp className="w-5 h-5 text-purple-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Avg Completion Rate</p>
                  <p className="text-2xl font-bold text-gray-900">90.8%</p>
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
                  <p className="text-2xl font-bold text-red-900">8</p>
                </div>
              </div>
            </div>
          </div>

          {/* Employee Performance Ranking */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Employee Performance Ranking</h3>
            <div className="space-y-3">
              {employeePerformance
                .sort((a, b) => b.completionRate - a.completionRate)
                .map((emp, index) => (
                  <div key={emp.employeeId} className={`p-4 rounded-lg border-2 ${
                    emp.completionRate >= 90 ? 'border-green-200 bg-green-50' :
                    emp.completionRate >= 80 ? 'border-blue-200 bg-blue-50' :
                    'border-red-200 bg-red-50'
                  }`}>
                    <div className="flex items-center gap-4">
                      <div className={`w-10 h-10 rounded-full flex items-center justify-center font-bold text-lg ${
                        index === 0 ? 'bg-yellow-100 text-yellow-800' :
                        index === 1 ? 'bg-gray-200 text-gray-700' :
                        index === 2 ? 'bg-orange-100 text-orange-700' :
                        'bg-gray-100 text-gray-600'
                      }`}>
                        {index + 1}
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-1">
                          <h4 className="font-semibold text-gray-900">{emp.employeeName}</h4>
                          <span className="px-2 py-1 bg-white text-gray-700 text-xs rounded border">
                            {emp.role}
                          </span>
                          <span className="text-xs text-gray-600">{emp.storeName}</span>
                        </div>
                        <div className="flex items-center gap-1">
                          {[...Array(5)].map((_, i) => (
                            <svg
                              key={i}
                              className={`w-4 h-4 ${i < Math.floor(emp.rating) ? 'text-yellow-400' : 'text-gray-300'}`}
                              fill="currentColor"
                              viewBox="0 0 20 20"
                            >
                              <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                            </svg>
                          ))}
                        </div>
                      </div>
                      <div className="grid grid-cols-3 gap-6 text-center">
                        <div>
                          <p className="text-xs text-gray-600">Assigned</p>
                          <p className="text-lg font-semibold text-gray-900">{emp.tasksAssigned}</p>
                        </div>
                        <div>
                          <p className="text-xs text-gray-600">Completed</p>
                          <p className="text-lg font-semibold text-green-600">{emp.tasksCompleted}</p>
                        </div>
                        <div>
                          <p className="text-xs text-gray-600">Overdue</p>
                          <p className={`text-lg font-semibold ${emp.tasksOverdue > 0 ? 'text-red-600' : 'text-gray-900'}`}>
                            {emp.tasksOverdue}
                          </p>
                        </div>
                      </div>
                      <div className="text-right">
                        <p className="text-2xl font-bold text-gray-900">{emp.completionRate.toFixed(1)}%</p>
                        <p className="text-xs text-gray-600">Completion Rate</p>
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        </div>
      ) : (
        /* Tasks List */
        <div className="space-y-4">
          {filteredTasks.map((task) => (
            <div key={task.id} className="card hover:shadow-lg transition-shadow">
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-2">
                    <h3 className="text-lg font-semibold text-gray-900">{task.title}</h3>
                    {getStatusBadge(task.status)}
                    {getPriorityBadge(task.priority)}
                  </div>
                  <p className="text-sm text-gray-600 mb-3">{task.description}</p>
                </div>
              </div>

              <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4 mb-4">
                <div>
                  <p className="text-xs text-gray-600 mb-1">Assigned To</p>
                  <p className="text-sm font-medium text-gray-900">{task.assignedToName}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600 mb-1">Store</p>
                  <p className="text-sm font-medium text-gray-900">{task.storeName}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600 mb-1">Due Date</p>
                  <p className={`text-sm font-medium ${
                    task.status === 'OVERDUE' ? 'text-red-600' : 'text-gray-900'
                  }`}>
                    {new Date(task.dueDate).toLocaleDateString()}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-600 mb-1">Category</p>
                  <p className="text-sm font-medium text-gray-900">{task.category}</p>
                </div>
              </div>

              {/* Checklist Preview */}
              {task.checklistItems && task.checklistItems.length > 0 && (
                <div className="border-t border-gray-200 pt-3">
                  <p className="text-xs text-gray-600 mb-2">
                    Checklist ({task.checklistItems.filter(i => i.completed).length}/{task.checklistItems.length})
                  </p>
                  <div className="space-y-1">
                    {task.checklistItems.map((item) => (
                      <div key={item.id} className="flex items-center gap-2 text-sm">
                        {item.completed ? (
                          <CheckCircle className="w-4 h-4 text-green-600" />
                        ) : (
                          <XCircle className="w-4 h-4 text-gray-400" />
                        )}
                        <span className={item.completed ? 'text-gray-500 line-through' : 'text-gray-700'}>
                          {item.text}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="flex items-center justify-between mt-4 pt-3 border-t border-gray-200">
                <div className="text-xs text-gray-500">
                  Created by {task.assignedByName} on {new Date(task.createdDate).toLocaleDateString()}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setSelectedTask(task)}
                    className="btn-outline text-sm py-1"
                  >
                    View Details
                  </button>
                  {task.status !== 'COMPLETED' && (
                    <button className="btn-primary text-sm py-1">
                      Mark Complete
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}

          {filteredTasks.length === 0 && (
            <div className="text-center py-12">
              <CheckSquare className="w-12 h-12 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-500">No tasks found</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
