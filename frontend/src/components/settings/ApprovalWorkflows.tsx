// ============================================================================
// IMS 2.0 - Approval Workflows Configuration
// ============================================================================
// Configure approval rules for discounts, refunds, POs, stock adjustments,
// and credit sales. Designed for Indian optical retail ERP.

import { useState, useCallback } from 'react';
import {
  Shield,
  ShieldCheck,
  ShieldAlert,
  ChevronDown,
  ChevronUp,
  Check,
  X,
  Clock,
  Bell,
  BellOff,
  Save,
  Loader2,
  AlertTriangle,
  BadgePercent,
  RotateCcw,
  ShoppingCart,
  PackageMinus,
  CreditCard,
  User,
  CalendarClock,
  ToggleLeft,
  ToggleRight,
  Info,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';

// ============================================================================
// Types
// ============================================================================

interface ApprovalWorkflow {
  id: string;
  type: string;
  name: string;
  description: string;
  isEnabled: boolean;
  thresholdType: 'AMOUNT' | 'PERCENTAGE' | 'ALWAYS';
  thresholdValue?: number;
  approverRoles: string[];
  escalationTimeout?: number;
  notifyOnRequest: boolean;
  notifyOnApproval: boolean;
}

interface PendingApproval {
  id: string;
  workflowType: string;
  description: string;
  requestedBy: string;
  requestedAt: string;
  details: string;
  urgency: 'LOW' | 'MEDIUM' | 'HIGH';
}

// ============================================================================
// Constants
// ============================================================================

const APPROVER_ROLES = [
  { value: 'SUPERADMIN', label: 'Super Admin' },
  { value: 'ADMIN', label: 'Admin' },
  { value: 'AREA_MANAGER', label: 'Area Manager' },
  { value: 'STORE_MANAGER', label: 'Store Manager' },
  { value: 'ACCOUNTANT', label: 'Accountant' },
] as const;

const WORKFLOW_ICONS: Record<string, typeof Shield> = {
  DISCOUNT_APPROVAL: BadgePercent,
  REFUND_APPROVAL: RotateCcw,
  PO_APPROVAL: ShoppingCart,
  STOCK_ADJUSTMENT: PackageMinus,
  CREDIT_SALE: CreditCard,
};

const URGENCY_STYLES: Record<string, string> = {
  LOW: 'bg-blue-50 text-blue-700 border-blue-200',
  MEDIUM: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  HIGH: 'bg-red-50 text-red-700 border-red-200',
};

// ============================================================================
// Mock Data
// ============================================================================

const INITIAL_WORKFLOWS: ApprovalWorkflow[] = [
  {
    id: 'wf-001',
    type: 'DISCOUNT_APPROVAL',
    name: 'Discount Approval',
    description:
      'Requires manager approval when a discount exceeds the configured threshold. Prevents unauthorized heavy discounting on optical products and lenses.',
    isEnabled: true,
    thresholdType: 'PERCENTAGE',
    thresholdValue: 15,
    approverRoles: ['ADMIN', 'STORE_MANAGER'],
    escalationTimeout: 2,
    notifyOnRequest: true,
    notifyOnApproval: true,
  },
  {
    id: 'wf-002',
    type: 'REFUND_APPROVAL',
    name: 'Refund Approval',
    description:
      'All refund requests must be approved by an authorized manager before processing. Applies to both cash and digital payment refunds.',
    isEnabled: true,
    thresholdType: 'ALWAYS',
    thresholdValue: undefined,
    approverRoles: ['ADMIN', 'STORE_MANAGER', 'ACCOUNTANT'],
    escalationTimeout: 4,
    notifyOnRequest: true,
    notifyOnApproval: true,
  },
  {
    id: 'wf-003',
    type: 'PO_APPROVAL',
    name: 'Purchase Order Approval',
    description:
      'Purchase orders exceeding the configured amount threshold require approval before being sent to the supplier. Helps control procurement spend.',
    isEnabled: true,
    thresholdType: 'AMOUNT',
    thresholdValue: 50000,
    approverRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'],
    escalationTimeout: 8,
    notifyOnRequest: true,
    notifyOnApproval: false,
  },
  {
    id: 'wf-004',
    type: 'STOCK_ADJUSTMENT',
    name: 'Stock Write-off Approval',
    description:
      'Stock write-offs and manual adjustments (damage, expiry, loss) require approval to maintain inventory accuracy and prevent shrinkage.',
    isEnabled: false,
    thresholdType: 'ALWAYS',
    thresholdValue: undefined,
    approverRoles: ['ADMIN', 'STORE_MANAGER'],
    escalationTimeout: 24,
    notifyOnRequest: true,
    notifyOnApproval: false,
  },
  {
    id: 'wf-005',
    type: 'CREDIT_SALE',
    name: 'Credit Sale Approval',
    description:
      'Credit sales (pay-later transactions) require manager approval. Ensures credit is only extended to verified customers with proper documentation.',
    isEnabled: false,
    thresholdType: 'AMOUNT',
    thresholdValue: 5000,
    approverRoles: ['ADMIN', 'STORE_MANAGER', 'ACCOUNTANT'],
    escalationTimeout: 1,
    notifyOnRequest: true,
    notifyOnApproval: true,
  },
];

const INITIAL_PENDING_APPROVALS: PendingApproval[] = [
  {
    id: 'pa-001',
    workflowType: 'DISCOUNT_APPROVAL',
    description: 'Discount of 25% on Order #ORD-1087',
    requestedBy: 'Rahul Sharma (Cashier)',
    requestedAt: '2026-02-07T09:30:00',
    details:
      'Customer requested bulk discount on 3 pairs of Titan Eyeplus frames + CR-39 lenses. Total order value: \u20B918,450. Discount amount: \u20B94,612.',
    urgency: 'MEDIUM',
  },
  {
    id: 'pa-002',
    workflowType: 'REFUND_APPROVAL',
    description: 'Full refund for Order #ORD-1052 - \u20B93,200',
    requestedBy: 'Priya Patel (Sales Staff)',
    requestedAt: '2026-02-07T08:15:00',
    details:
      'Customer returned Ray-Ban prescription sunglasses due to incorrect power. Lens power mismatch confirmed by optometrist. Original payment via UPI.',
    urgency: 'HIGH',
  },
  {
    id: 'pa-003',
    workflowType: 'PO_APPROVAL',
    description: 'Purchase Order #PO-2041 to Essilor India - \u20B91,25,000',
    requestedBy: 'Amit Kumar (Store Manager)',
    requestedAt: '2026-02-06T16:45:00',
    details:
      'Monthly restock of Crizal and Varilux lenses. 50 pairs Crizal Sapphire UV, 30 pairs Varilux Comfort. Supplier: Essilor India Pvt Ltd.',
    urgency: 'LOW',
  },
  {
    id: 'pa-004',
    workflowType: 'DISCOUNT_APPROVAL',
    description: 'Discount of 20% on Order #ORD-1092',
    requestedBy: 'Sneha Reddy (Sales Staff)',
    requestedAt: '2026-02-07T10:05:00',
    details:
      'Loyalty customer (5+ purchases). Requested discount on Oakley sports frames + photochromic lenses combo. Order value: \u20B912,800.',
    urgency: 'MEDIUM',
  },
];

// ============================================================================
// Helper Functions
// ============================================================================

function formatThreshold(workflow: ApprovalWorkflow): string {
  if (workflow.thresholdType === 'ALWAYS') return 'Always required';
  if (workflow.thresholdType === 'PERCENTAGE' && workflow.thresholdValue != null) {
    return `When > ${workflow.thresholdValue}%`;
  }
  if (workflow.thresholdType === 'AMOUNT' && workflow.thresholdValue != null) {
    return `When > \u20B9${workflow.thresholdValue.toLocaleString('en-IN')}`;
  }
  return 'Not configured';
}

function formatRoles(roles: string[]): string {
  return roles
    .map((r) => APPROVER_ROLES.find((ar) => ar.value === r)?.label ?? r)
    .join(', ');
}

function formatTimeAgo(isoString: string): string {
  const now = new Date();
  const then = new Date(isoString);
  const diffMs = now.getTime() - then.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

// ============================================================================
// Sub-Components
// ============================================================================

function PendingApprovalCard({
  item,
  onApprove,
  onReject,
}: {
  item: PendingApproval;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const Icon = WORKFLOW_ICONS[item.workflowType] ?? Shield;

  return (
    <div className="border border-gray-200 rounded-lg bg-white shadow-sm">
      <div className="p-4">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 p-2 rounded-lg bg-orange-50 text-orange-600 shrink-0">
            <Icon className="w-4 h-4" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h4 className="text-sm font-semibold text-gray-900 truncate">
                {item.description}
              </h4>
              <span
                className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${URGENCY_STYLES[item.urgency]}`}
              >
                {item.urgency}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-3 text-xs text-gray-500">
              <span className="inline-flex items-center gap-1">
                <User className="w-3 h-3" />
                {item.requestedBy}
              </span>
              <span className="inline-flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {formatTimeAgo(item.requestedAt)}
              </span>
            </div>
            {expanded && (
              <div className="mt-3 p-3 bg-gray-50 rounded-md text-sm text-gray-700 leading-relaxed">
                {item.details}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setExpanded(!expanded)}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-md transition-colors"
              title={expanded ? 'Collapse' : 'View details'}
            >
              {expanded ? (
                <ChevronUp className="w-4 h-4" />
              ) : (
                <ChevronDown className="w-4 h-4" />
              )}
            </button>
            <button
              onClick={() => onApprove(item.id)}
              className="inline-flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white text-xs font-medium rounded-md hover:bg-green-700 transition-colors"
            >
              <Check className="w-3.5 h-3.5" />
              Approve
            </button>
            <button
              onClick={() => onReject(item.id)}
              className="inline-flex items-center gap-1 px-3 py-1.5 bg-white text-red-600 text-xs font-medium rounded-md border border-red-300 hover:bg-red-50 transition-colors"
            >
              <X className="w-3.5 h-3.5" />
              Reject
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function WorkflowCard({
  workflow,
  onToggle,
  onSave,
}: {
  workflow: ApprovalWorkflow;
  onToggle: (id: string) => void;
  onSave: (updated: ApprovalWorkflow) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState<ApprovalWorkflow>(workflow);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const Icon = WORKFLOW_ICONS[workflow.type] ?? Shield;

  const handleSave = useCallback(async () => {
    setSaving(true);
    // Simulate API call
    await new Promise((resolve) => setTimeout(resolve, 600));
    onSave(editing);
    setSaving(false);
    setExpanded(false);
    toast.success(`${editing.name} configuration saved successfully.`);
  }, [editing, onSave, toast]);

  const handleRoleToggle = useCallback(
    (role: string) => {
      setEditing((prev) => {
        const roles = prev.approverRoles.includes(role)
          ? prev.approverRoles.filter((r) => r !== role)
          : [...prev.approverRoles, role];
        return { ...prev, approverRoles: roles };
      });
    },
    []
  );

  return (
    <div
      className={`border rounded-lg bg-white shadow-sm transition-all ${
        workflow.isEnabled ? 'border-gray-200' : 'border-gray-100 opacity-75'
      }`}
    >
      {/* Card Header */}
      <div className="p-4">
        <div className="flex items-start gap-3">
          <div
            className={`mt-0.5 p-2.5 rounded-lg shrink-0 ${
              workflow.isEnabled
                ? 'bg-indigo-50 text-indigo-600'
                : 'bg-gray-100 text-gray-400'
            }`}
          >
            <Icon className="w-5 h-5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-gray-900">
                {workflow.name}
              </h3>
              {workflow.isEnabled ? (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700">
                  <ShieldCheck className="w-3 h-3" />
                  Active
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">
                  <ShieldAlert className="w-3 h-3" />
                  Inactive
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-gray-500 line-clamp-2">
              {workflow.description}
            </p>
            <div className="mt-2 flex items-center gap-4 text-xs text-gray-600">
              <span className="inline-flex items-center gap-1">
                <AlertTriangle className="w-3 h-3 text-amber-500" />
                {formatThreshold(workflow)}
              </span>
              <span className="inline-flex items-center gap-1">
                <User className="w-3 h-3 text-blue-500" />
                {formatRoles(workflow.approverRoles)}
              </span>
              {workflow.escalationTimeout != null && (
                <span className="inline-flex items-center gap-1">
                  <CalendarClock className="w-3 h-3 text-purple-500" />
                  Escalate in {workflow.escalationTimeout}h
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => onToggle(workflow.id)}
              className="p-1 rounded-md hover:bg-gray-100 transition-colors"
              title={workflow.isEnabled ? 'Disable workflow' : 'Enable workflow'}
            >
              {workflow.isEnabled ? (
                <ToggleRight className="w-7 h-7 text-green-600" />
              ) : (
                <ToggleLeft className="w-7 h-7 text-gray-400" />
              )}
            </button>
            <button
              onClick={() => {
                setExpanded(!expanded);
                if (!expanded) setEditing(workflow);
              }}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-md transition-colors"
              title={expanded ? 'Collapse' : 'Configure'}
            >
              {expanded ? (
                <ChevronUp className="w-4 h-4" />
              ) : (
                <ChevronDown className="w-4 h-4" />
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Expanded Configuration */}
      {expanded && (
        <div className="border-t border-gray-100 bg-gray-50/50 p-4 space-y-5">
          {/* Threshold Configuration */}
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-2">
              Trigger Condition
            </label>
            <div className="flex items-center gap-3">
              <select
                value={editing.thresholdType}
                onChange={(e) =>
                  setEditing((prev) => ({
                    ...prev,
                    thresholdType: e.target.value as ApprovalWorkflow['thresholdType'],
                    thresholdValue:
                      e.target.value === 'ALWAYS' ? undefined : prev.thresholdValue ?? 0,
                  }))
                }
                className="px-3 py-2 border border-gray-300 rounded-md text-sm bg-white focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
              >
                <option value="ALWAYS">Always Required</option>
                <option value="PERCENTAGE">Percentage Threshold</option>
                <option value="AMOUNT">Amount Threshold (\u20B9)</option>
              </select>
              {editing.thresholdType !== 'ALWAYS' && (
                <div className="relative">
                  {editing.thresholdType === 'AMOUNT' && (
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-gray-400">
                      \u20B9
                    </span>
                  )}
                  <input
                    type="number"
                    min={0}
                    value={editing.thresholdValue ?? 0}
                    onChange={(e) =>
                      setEditing((prev) => ({
                        ...prev,
                        thresholdValue: Number(e.target.value),
                      }))
                    }
                    className={`w-36 py-2 border border-gray-300 rounded-md text-sm bg-white focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none ${
                      editing.thresholdType === 'AMOUNT' ? 'pl-7 pr-3' : 'px-3'
                    }`}
                  />
                  {editing.thresholdType === 'PERCENTAGE' && (
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-sm text-gray-400">
                      %
                    </span>
                  )}
                </div>
              )}
            </div>
            {editing.thresholdType === 'PERCENTAGE' && (
              <p className="mt-1.5 text-xs text-gray-500 flex items-center gap-1">
                <Info className="w-3 h-3" />
                Approval required when discount exceeds {editing.thresholdValue ?? 0}%
              </p>
            )}
            {editing.thresholdType === 'AMOUNT' && (
              <p className="mt-1.5 text-xs text-gray-500 flex items-center gap-1">
                <Info className="w-3 h-3" />
                Approval required for amounts above \u20B9
                {(editing.thresholdValue ?? 0).toLocaleString('en-IN')}
              </p>
            )}
          </div>

          {/* Approver Roles */}
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-2">
              Approver Roles
            </label>
            <div className="flex flex-wrap gap-2">
              {APPROVER_ROLES.map((role) => {
                const isSelected = editing.approverRoles.includes(role.value);
                return (
                  <button
                    key={role.value}
                    onClick={() => handleRoleToggle(role.value)}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border transition-colors ${
                      isSelected
                        ? 'bg-indigo-50 text-indigo-700 border-indigo-300 hover:bg-indigo-100'
                        : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                    }`}
                  >
                    {isSelected && <Check className="w-3 h-3" />}
                    {role.label}
                  </button>
                );
              })}
            </div>
            {editing.approverRoles.length === 0 && (
              <p className="mt-1.5 text-xs text-red-500 flex items-center gap-1">
                <AlertTriangle className="w-3 h-3" />
                At least one approver role must be selected.
              </p>
            )}
          </div>

          {/* Escalation Timeout */}
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-2">
              Auto-Escalation Timeout
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={168}
                value={editing.escalationTimeout ?? 0}
                onChange={(e) =>
                  setEditing((prev) => ({
                    ...prev,
                    escalationTimeout: Number(e.target.value) || undefined,
                  }))
                }
                className="w-24 px-3 py-2 border border-gray-300 rounded-md text-sm bg-white focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
              />
              <span className="text-sm text-gray-500">
                hours before escalating to next level
              </span>
            </div>
            <p className="mt-1.5 text-xs text-gray-500 flex items-center gap-1">
              <Info className="w-3 h-3" />
              Set to 0 to disable auto-escalation.
            </p>
          </div>

          {/* Notification Toggles */}
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-2">
              Notifications
            </label>
            <div className="flex items-center gap-6">
              <label className="inline-flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editing.notifyOnRequest}
                  onChange={(e) =>
                    setEditing((prev) => ({
                      ...prev,
                      notifyOnRequest: e.target.checked,
                    }))
                  }
                  className="w-4 h-4 text-indigo-600 border-gray-300 rounded focus:ring-indigo-500"
                />
                <span className="text-sm text-gray-700 inline-flex items-center gap-1">
                  <Bell className="w-3.5 h-3.5 text-gray-400" />
                  Notify on new request
                </span>
              </label>
              <label className="inline-flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editing.notifyOnApproval}
                  onChange={(e) =>
                    setEditing((prev) => ({
                      ...prev,
                      notifyOnApproval: e.target.checked,
                    }))
                  }
                  className="w-4 h-4 text-indigo-600 border-gray-300 rounded focus:ring-indigo-500"
                />
                <span className="text-sm text-gray-700 inline-flex items-center gap-1">
                  {editing.notifyOnApproval ? (
                    <Bell className="w-3.5 h-3.5 text-gray-400" />
                  ) : (
                    <BellOff className="w-3.5 h-3.5 text-gray-400" />
                  )}
                  Notify on approval/rejection
                </span>
              </label>
            </div>
          </div>

          {/* Save Button */}
          <div className="flex items-center justify-end gap-3 pt-2 border-t border-gray-200">
            <button
              onClick={() => {
                setEditing(workflow);
                setExpanded(false);
              }}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving || editing.approverRoles.length === 0}
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-md hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {saving ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              Save Configuration
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function ApprovalWorkflows() {
  const toast = useToast();
  const [workflows, setWorkflows] = useState<ApprovalWorkflow[]>(INITIAL_WORKFLOWS);
  const [pendingApprovals, setPendingApprovals] =
    useState<PendingApproval[]>(INITIAL_PENDING_APPROVALS);

  const activeCount = workflows.filter((w) => w.isEnabled).length;
  const pendingCount = pendingApprovals.length;

  const handleToggle = useCallback(
    (id: string) => {
      setWorkflows((prev) =>
        prev.map((w) => {
          if (w.id !== id) return w;
          const toggled = { ...w, isEnabled: !w.isEnabled };
          toast.info(
            `${toggled.name} ${toggled.isEnabled ? 'enabled' : 'disabled'}.`
          );
          return toggled;
        })
      );
    },
    [toast]
  );

  const handleSave = useCallback((updated: ApprovalWorkflow) => {
    setWorkflows((prev) => prev.map((w) => (w.id === updated.id ? updated : w)));
  }, []);

  const handleApprove = useCallback(
    (id: string) => {
      const item = pendingApprovals.find((p) => p.id === id);
      setPendingApprovals((prev) => prev.filter((p) => p.id !== id));
      toast.success(`Approved: ${item?.description ?? 'Request'}`);
    },
    [pendingApprovals, toast]
  );

  const handleReject = useCallback(
    (id: string) => {
      const item = pendingApprovals.find((p) => p.id === id);
      setPendingApprovals((prev) => prev.filter((p) => p.id !== id));
      toast.warning(`Rejected: ${item?.description ?? 'Request'}`);
    },
    [pendingApprovals, toast]
  );

  return (
    <div className="space-y-6">
      {/* Summary Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="flex items-center gap-3 p-4 bg-white border border-gray-200 rounded-lg shadow-sm">
          <div className="p-2.5 bg-indigo-50 rounded-lg">
            <ShieldCheck className="w-5 h-5 text-indigo-600" />
          </div>
          <div>
            <p className="text-2xl font-bold text-gray-900">{activeCount}</p>
            <p className="text-xs text-gray-500">
              Active Workflow{activeCount !== 1 ? 's' : ''}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 p-4 bg-white border border-gray-200 rounded-lg shadow-sm">
          <div className="p-2.5 bg-orange-50 rounded-lg">
            <Clock className="w-5 h-5 text-orange-600" />
          </div>
          <div>
            <p className="text-2xl font-bold text-gray-900">{pendingCount}</p>
            <p className="text-xs text-gray-500">
              Pending Approval{pendingCount !== 1 ? 's' : ''}
            </p>
          </div>
        </div>
      </div>

      {/* Pending Approvals Section */}
      {pendingApprovals.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <Clock className="w-4 h-4 text-orange-600" />
            <h2 className="text-sm font-semibold text-gray-900">
              Pending Approvals ({pendingApprovals.length})
            </h2>
          </div>
          <div className="space-y-3">
            {pendingApprovals.map((item) => (
              <PendingApprovalCard
                key={item.id}
                item={item}
                onApprove={handleApprove}
                onReject={handleReject}
              />
            ))}
          </div>
        </div>
      )}

      {pendingApprovals.length === 0 && (
        <div className="flex items-center gap-3 p-4 bg-green-50 border border-green-200 rounded-lg">
          <ShieldCheck className="w-5 h-5 text-green-600 shrink-0" />
          <p className="text-sm text-green-700">
            All caught up -- no pending approvals at the moment.
          </p>
        </div>
      )}

      {/* Workflow Rules Section */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <Shield className="w-4 h-4 text-indigo-600" />
          <h2 className="text-sm font-semibold text-gray-900">
            Workflow Rules ({workflows.length})
          </h2>
        </div>
        <div className="space-y-3">
          {workflows.map((workflow) => (
            <WorkflowCard
              key={workflow.id}
              workflow={workflow}
              onToggle={handleToggle}
              onSave={handleSave}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export default ApprovalWorkflows;
