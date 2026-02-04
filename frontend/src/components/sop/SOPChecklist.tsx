// ============================================================================
// IMS 2.0 - SOP Checklist Component
// ============================================================================
// Standard Operating Procedure checklists for store operations
// Includes: Store Opening, Closing, Cleaning, Equipment Check, Cash Reconciliation

import { useState } from 'react';
import {
  CheckCircle,
  Clock,
  User,
  Calendar,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  Sun,
  Moon,
  Sparkles,
  Monitor,
  IndianRupee,
  Camera,
} from 'lucide-react';
import clsx from 'clsx';

type ChecklistType = 'OPENING' | 'CLOSING' | 'CLEANING' | 'EQUIPMENT' | 'CASH';
type ChecklistStatus = 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED';

interface ChecklistItem {
  id: string;
  task: string;
  description?: string;
  requiresPhoto?: boolean;
  requiresNote?: boolean;
  completed: boolean;
  completedBy?: string;
  completedAt?: string;
  photo?: string;
  note?: string;
}

interface Checklist {
  id: string;
  type: ChecklistType;
  title: string;
  icon: typeof Sun;
  items: ChecklistItem[];
  status: ChecklistStatus;
  startedAt?: string;
  completedAt?: string;
  assignedTo?: string;
}

// SOP Checklist Templates
const CHECKLIST_TEMPLATES: Record<ChecklistType, { title: string; icon: typeof Sun; items: Omit<ChecklistItem, 'completed' | 'id'>[] }> = {
  OPENING: {
    title: 'Store Opening Checklist',
    icon: Sun,
    items: [
      { task: 'Arrive 15 minutes before opening time' },
      { task: 'Disable alarm system' },
      { task: 'Turn on all lights and AC' },
      { task: 'Check store cleanliness', requiresNote: true },
      { task: 'Power on all POS systems and verify connectivity' },
      { task: 'Count and verify opening cash float', requiresNote: true },
      { task: 'Check product displays are properly arranged', requiresPhoto: true },
      { task: 'Verify all price tags are visible' },
      { task: 'Check inventory low-stock alerts' },
      { task: 'Review pending orders and appointments' },
      { task: 'Brief staff on daily targets and promotions' },
      { task: 'Unlock entrance doors at opening time' },
    ],
  },
  CLOSING: {
    title: 'Store Closing Checklist',
    icon: Moon,
    items: [
      { task: 'Announce store closing 15 minutes before' },
      { task: 'Ensure all customers have exited' },
      { task: 'Lock entrance doors' },
      { task: 'Run end-of-day sales report' },
      { task: 'Reconcile cash with system', requiresNote: true },
      { task: 'Prepare bank deposit if applicable', requiresNote: true },
      { task: 'Secure cash in safe' },
      { task: 'Shut down all POS terminals' },
      { task: 'Check all trial rooms and back areas' },
      { task: 'Turn off display lights' },
      { task: 'Turn off AC units' },
      { task: 'Secure all display cases' },
      { task: 'Set alarm system' },
      { task: 'Final walkthrough and lock up', requiresPhoto: true },
    ],
  },
  CLEANING: {
    title: 'Daily Cleaning Checklist',
    icon: Sparkles,
    items: [
      { task: 'Sweep and mop all floor areas' },
      { task: 'Clean glass display cases', requiresPhoto: true },
      { task: 'Dust all product shelves' },
      { task: 'Clean mirrors in trial area' },
      { task: 'Sanitize customer seating areas' },
      { task: 'Clean POS counter and equipment' },
      { task: 'Empty all trash bins' },
      { task: 'Clean restrooms (if applicable)' },
      { task: 'Wipe door handles and high-touch surfaces' },
      { task: 'Clean entrance glass doors' },
      { task: 'Check and refill hand sanitizer dispensers' },
    ],
  },
  EQUIPMENT: {
    title: 'Equipment Check',
    icon: Monitor,
    items: [
      { task: 'Test all POS terminals are working' },
      { task: 'Verify card payment machines functional' },
      { task: 'Check barcode scanners operational' },
      { task: 'Test receipt printers (paper and ink levels)' },
      { task: 'Verify internet connectivity' },
      { task: 'Check security cameras recording' },
      { task: 'Test AC units cooling properly' },
      { task: 'Check lighting (replace any burnt bulbs)', requiresNote: true },
      { task: 'Verify phone lines working' },
      { task: 'Check UPS battery backup status' },
      { task: 'Eye test equipment calibration check' },
      { task: 'Lens edging machine operational check' },
    ],
  },
  CASH: {
    title: 'Cash Reconciliation',
    icon: IndianRupee,
    items: [
      { task: 'Print system sales report' },
      { task: 'Count physical cash in drawer', requiresNote: true },
      { task: 'Verify UPI transactions match records', requiresNote: true },
      { task: 'Verify card transactions match records', requiresNote: true },
      { task: 'Calculate total expected cash' },
      { task: 'Compare physical vs expected', requiresNote: true },
      { task: 'Document any discrepancies', requiresNote: true },
      { task: 'Manager sign-off on reconciliation' },
      { task: 'Prepare cash deposit slip' },
      { task: 'Secure excess cash in safe' },
    ],
  },
};

// Initialize checklist from template
const initializeChecklist = (type: ChecklistType): Checklist => {
  const template = CHECKLIST_TEMPLATES[type];
  return {
    id: `checklist-${type}-${Date.now()}`,
    type,
    title: template.title,
    icon: template.icon,
    status: 'NOT_STARTED',
    items: template.items.map((item, index) => ({
      ...item,
      id: `item-${index}`,
      completed: false,
    })),
  };
};

export function SOPChecklist() {
  const [activeChecklist, setActiveChecklist] = useState<ChecklistType>('OPENING');
  const [checklists, setChecklists] = useState<Record<ChecklistType, Checklist>>({
    OPENING: initializeChecklist('OPENING'),
    CLOSING: initializeChecklist('CLOSING'),
    CLEANING: initializeChecklist('CLEANING'),
    EQUIPMENT: initializeChecklist('EQUIPMENT'),
    CASH: initializeChecklist('CASH'),
  });
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());
  const [noteInput, setNoteInput] = useState<Record<string, string>>({});

  const currentChecklist = checklists[activeChecklist];
  const completedCount = currentChecklist.items.filter(i => i.completed).length;
  const totalCount = currentChecklist.items.length;
  const progress = Math.round((completedCount / totalCount) * 100);

  const handleToggleItem = (itemId: string) => {
    const now = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });

    setChecklists(prev => {
      const checklist = prev[activeChecklist];
      const updatedItems = checklist.items.map(item => {
        if (item.id === itemId) {
          return {
            ...item,
            completed: !item.completed,
            completedBy: !item.completed ? 'Current User' : undefined,
            completedAt: !item.completed ? now : undefined,
            note: noteInput[itemId] || item.note,
          };
        }
        return item;
      });

      const allCompleted = updatedItems.every(i => i.completed);
      const anyCompleted = updatedItems.some(i => i.completed);

      return {
        ...prev,
        [activeChecklist]: {
          ...checklist,
          items: updatedItems,
          status: allCompleted ? 'COMPLETED' : anyCompleted ? 'IN_PROGRESS' : 'NOT_STARTED',
          startedAt: anyCompleted && !checklist.startedAt ? now : checklist.startedAt,
          completedAt: allCompleted ? now : undefined,
        },
      };
    });
  };

  const handleNoteChange = (itemId: string, note: string) => {
    setNoteInput(prev => ({ ...prev, [itemId]: note }));
  };

  const toggleExpanded = (itemId: string) => {
    setExpandedItems(prev => {
      const next = new Set(prev);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
  };

  const handleResetChecklist = () => {
    setChecklists(prev => ({
      ...prev,
      [activeChecklist]: initializeChecklist(activeChecklist),
    }));
    setNoteInput({});
  };

  const getStatusBadge = (status: ChecklistStatus) => {
    switch (status) {
      case 'COMPLETED':
        return <span className="badge-success">Completed</span>;
      case 'IN_PROGRESS':
        return <span className="badge-warning">In Progress</span>;
      default:
        return <span className="badge-neutral">Not Started</span>;
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">SOP Checklists</h2>
          <p className="text-sm text-gray-500">Standard Operating Procedures for daily operations</p>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Calendar className="w-4 h-4" />
          {new Date().toLocaleDateString('en-IN', {
            weekday: 'long',
            day: '2-digit',
            month: 'short',
            year: 'numeric'
          })}
        </div>
      </div>

      {/* Checklist Type Tabs */}
      <div className="flex gap-2 overflow-x-auto pb-2">
        {(Object.keys(CHECKLIST_TEMPLATES) as ChecklistType[]).map(type => {
          const template = CHECKLIST_TEMPLATES[type];
          const checklist = checklists[type];
          const Icon = template.icon;
          const isActive = activeChecklist === type;

          return (
            <button
              key={type}
              onClick={() => setActiveChecklist(type)}
              className={clsx(
                'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors',
                isActive
                  ? 'bg-bv-red-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              )}
            >
              <Icon className="w-4 h-4" />
              {template.title.replace(' Checklist', '')}
              {checklist.status === 'COMPLETED' && (
                <CheckCircle className="w-4 h-4 text-green-300" />
              )}
              {checklist.status === 'IN_PROGRESS' && (
                <Clock className="w-4 h-4 text-yellow-300" />
              )}
            </button>
          );
        })}
      </div>

      {/* Active Checklist */}
      <div className="card">
        {/* Checklist Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-bv-red-100 rounded-lg flex items-center justify-center">
              <currentChecklist.icon className="w-5 h-5 text-bv-red-600" />
            </div>
            <div>
              <h3 className="font-semibold text-gray-900">{currentChecklist.title}</h3>
              <div className="flex items-center gap-2 mt-1">
                {getStatusBadge(currentChecklist.status)}
                {currentChecklist.startedAt && (
                  <span className="text-xs text-gray-400">
                    Started: {currentChecklist.startedAt}
                  </span>
                )}
              </div>
            </div>
          </div>
          <button
            onClick={handleResetChecklist}
            className="btn-outline text-sm"
          >
            Reset
          </button>
        </div>

        {/* Progress Bar */}
        <div className="mb-4">
          <div className="flex items-center justify-between text-sm mb-1">
            <span className="text-gray-500">Progress</span>
            <span className="font-medium">{completedCount} / {totalCount} ({progress}%)</span>
          </div>
          <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
            <div
              className={clsx(
                'h-full rounded-full transition-all',
                progress === 100 ? 'bg-green-500' : 'bg-bv-red-600'
              )}
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        {/* Checklist Items */}
        <div className="space-y-2">
          {currentChecklist.items.map((item, index) => {
            const isExpanded = expandedItems.has(item.id);
            const hasExtra = item.requiresNote || item.requiresPhoto;

            return (
              <div
                key={item.id}
                className={clsx(
                  'border rounded-lg overflow-hidden transition-colors',
                  item.completed ? 'border-green-200 bg-green-50' : 'border-gray-200'
                )}
              >
                <div
                  className="flex items-center gap-3 p-3 cursor-pointer"
                  onClick={() => !hasExtra ? handleToggleItem(item.id) : toggleExpanded(item.id)}
                >
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleToggleItem(item.id);
                    }}
                    className={clsx(
                      'w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0',
                      item.completed
                        ? 'bg-green-500 text-white'
                        : 'border-2 border-gray-300 hover:border-bv-red-500'
                    )}
                  >
                    {item.completed && <CheckCircle className="w-4 h-4" />}
                  </button>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-gray-400 font-mono">{String(index + 1).padStart(2, '0')}.</span>
                      <span className={clsx(
                        'text-sm',
                        item.completed ? 'text-gray-500 line-through' : 'text-gray-900'
                      )}>
                        {item.task}
                      </span>
                    </div>
                    {item.completed && item.completedAt && (
                      <div className="flex items-center gap-2 mt-1 text-xs text-gray-400">
                        <User className="w-3 h-3" />
                        {item.completedBy} at {item.completedAt}
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-2">
                    {item.requiresNote && (
                      <span className="text-xs text-blue-500 bg-blue-50 px-2 py-0.5 rounded">
                        Note
                      </span>
                    )}
                    {item.requiresPhoto && (
                      <span className="text-xs text-purple-500 bg-purple-50 px-2 py-0.5 rounded">
                        Photo
                      </span>
                    )}
                    {hasExtra && (
                      isExpanded ? (
                        <ChevronUp className="w-4 h-4 text-gray-400" />
                      ) : (
                        <ChevronDown className="w-4 h-4 text-gray-400" />
                      )
                    )}
                  </div>
                </div>

                {/* Expanded Content */}
                {isExpanded && hasExtra && (
                  <div className="px-3 pb-3 space-y-3 border-t border-gray-100">
                    {item.requiresNote && (
                      <div className="mt-3">
                        <label className="block text-xs text-gray-500 mb-1">Add Note</label>
                        <textarea
                          value={noteInput[item.id] || item.note || ''}
                          onChange={(e) => handleNoteChange(item.id, e.target.value)}
                          className="input-field text-sm"
                          placeholder="Enter details or observations..."
                          rows={2}
                        />
                      </div>
                    )}
                    {item.requiresPhoto && (
                      <div className="mt-3">
                        <label className="block text-xs text-gray-500 mb-1">Attach Photo</label>
                        <button className="btn-outline text-sm flex items-center gap-2">
                          <Camera className="w-4 h-4" />
                          Capture Photo
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Completion Summary */}
        {currentChecklist.status === 'COMPLETED' && (
          <div className="mt-4 p-4 bg-green-50 border border-green-200 rounded-lg">
            <div className="flex items-center gap-3">
              <CheckCircle className="w-6 h-6 text-green-500" />
              <div>
                <p className="font-medium text-green-800">Checklist Completed!</p>
                <p className="text-sm text-green-600">
                  All {totalCount} items verified at {currentChecklist.completedAt}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Warning for incomplete critical items */}
        {currentChecklist.status === 'IN_PROGRESS' && completedCount < totalCount && (
          <div className="mt-4 p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
            <div className="flex items-center gap-3">
              <AlertTriangle className="w-5 h-5 text-yellow-500" />
              <p className="text-sm text-yellow-800">
                {totalCount - completedCount} item{totalCount - completedCount > 1 ? 's' : ''} remaining to complete this checklist
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Daily Summary */}
      <div className="card bg-gray-50">
        <h4 className="font-medium text-gray-900 mb-3">Today's SOP Summary</h4>
        <div className="grid grid-cols-2 tablet:grid-cols-5 gap-3">
          {(Object.keys(CHECKLIST_TEMPLATES) as ChecklistType[]).map(type => {
            const checklist = checklists[type];
            const template = CHECKLIST_TEMPLATES[type];
            const Icon = template.icon;
            const completed = checklist.items.filter(i => i.completed).length;
            const total = checklist.items.length;

            return (
              <div key={type} className="text-center p-3 bg-white rounded-lg">
                <Icon className={clsx(
                  'w-5 h-5 mx-auto mb-1',
                  checklist.status === 'COMPLETED' ? 'text-green-500' :
                  checklist.status === 'IN_PROGRESS' ? 'text-yellow-500' : 'text-gray-400'
                )} />
                <p className="text-xs text-gray-500 truncate">{template.title.replace(' Checklist', '')}</p>
                <p className="text-sm font-medium">{completed}/{total}</p>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default SOPChecklist;
