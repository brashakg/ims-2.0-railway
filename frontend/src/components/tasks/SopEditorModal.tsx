// ============================================================================
// IMS 2.0 — SOP Editor Modal (Phase 6.14)
// ============================================================================
// User-reported bugs it fixes:
//   "sops are non editable and non assignable"
//   "new sop button doesn't work"
//
// Before: TaskManagementPage showed a hardcoded mock list of SOPs with
// View/Edit icons wired to nothing, and a "New SOP" button whose state
// getter was discarded (`const [, setShowCreateTask] = useState(false)`).
// Now: this modal is the single source for create + edit + assign,
// writes to the new /tasks/sop-templates endpoints.

import { useEffect, useState } from 'react';
import { X, Plus, Trash2, AlertTriangle, Loader2, Check } from 'lucide-react';
import { tasksApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';

export interface SopStep {
  step_number: number;
  instruction: string;
  warning?: string;
}

export interface SopTemplateForm {
  template_id?: string;
  title: string;
  description: string;
  category: string;
  frequency: string;
  estimated_time: number;
  steps: SopStep[];
  assigned_roles: string[];
  assigned_users: string[];
  store_id?: string;
  is_active?: boolean;
}

const ALL_ROLES = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'ACCOUNTANT',
  'CATALOG_MANAGER',
  'OPTOMETRIST',
  'SALES_CASHIER',
  'SALES_STAFF',
  'CASHIER',
  'WORKSHOP_STAFF',
];

const CATEGORIES = ['Operations', 'Finance', 'Sales', 'Clinical', 'Workshop'];
const FREQUENCIES = ['DAILY', 'WEEKLY', 'MONTHLY', 'AD_HOC'];

interface SopEditorModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** null = creating new; existing template = editing. */
  initial: SopTemplateForm | null;
  /** Called after a successful save so the parent can refresh its list. */
  onSaved: () => void;
}

export function SopEditorModal({ isOpen, onClose, initial, onSaved }: SopEditorModalProps) {
  const toast = useToast();
  const [form, setForm] = useState<SopTemplateForm>(emptyForm());
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setForm(initial ? deepClone(initial) : emptyForm());
  }, [isOpen, initial]);

  if (!isOpen) return null;

  const isEdit = !!form.template_id;

  const update = <K extends keyof SopTemplateForm>(key: K, value: SopTemplateForm[K]) =>
    setForm(prev => ({ ...prev, [key]: value }));

  const updateStep = (idx: number, patch: Partial<SopStep>) =>
    setForm(prev => ({
      ...prev,
      steps: prev.steps.map((s, i) => (i === idx ? { ...s, ...patch } : s)),
    }));

  const addStep = () =>
    setForm(prev => ({
      ...prev,
      steps: [...prev.steps, { step_number: prev.steps.length + 1, instruction: '' }],
    }));

  const removeStep = (idx: number) =>
    setForm(prev => ({
      ...prev,
      steps: prev.steps
        .filter((_, i) => i !== idx)
        .map((s, i) => ({ ...s, step_number: i + 1 })),
    }));

  const moveStep = (idx: number, dir: -1 | 1) => {
    const target = idx + dir;
    if (target < 0 || target >= form.steps.length) return;
    setForm(prev => {
      const next = [...prev.steps];
      [next[idx], next[target]] = [next[target], next[idx]];
      return { ...prev, steps: next.map((s, i) => ({ ...s, step_number: i + 1 })) };
    });
  };

  const toggleRole = (role: string) =>
    setForm(prev => ({
      ...prev,
      assigned_roles: prev.assigned_roles.includes(role)
        ? prev.assigned_roles.filter(r => r !== role)
        : [...prev.assigned_roles, role],
    }));

  const handleSave = async () => {
    // Minimum viable validation — server re-validates.
    if (form.title.trim().length < 3) {
      toast.error('Title must be at least 3 characters');
      return;
    }
    if (form.steps.length === 0) {
      toast.error('Add at least one step');
      return;
    }
    if (form.steps.some(s => !s.instruction.trim())) {
      toast.error('Every step needs an instruction');
      return;
    }

    setIsSaving(true);
    try {
      const payload = {
        title: form.title.trim(),
        description: form.description?.trim() || undefined,
        category: form.category,
        frequency: form.frequency,
        estimated_time: form.estimated_time,
        steps: form.steps.map((s, i) => ({
          step_number: i + 1,
          instruction: s.instruction.trim(),
          warning: s.warning?.trim() || undefined,
        })),
        assigned_roles: form.assigned_roles,
        assigned_users: form.assigned_users,
      };
      if (isEdit && form.template_id) {
        await tasksApi.updateSopTemplate(form.template_id, payload);
        toast.success('SOP updated');
      } else {
        await tasksApi.createSopTemplate(payload);
        toast.success('SOP created');
      }
      onSaved();
      onClose();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[SOP] save failed:', e);
      const msg = (e as any)?.response?.data?.detail || (e as Error)?.message || 'Could not save SOP';
      toast.error(typeof msg === 'string' ? msg : 'Could not save SOP');
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!form.template_id) return;
    if (!confirm('Archive this SOP? It will be hidden but not destroyed.')) return;
    setIsDeleting(true);
    try {
      await tasksApi.deleteSopTemplate(form.template_id);
      toast.success('SOP archived');
      onSaved();
      onClose();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[SOP] delete failed:', e);
      toast.error('Could not archive SOP');
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              {isEdit ? 'Edit SOP' : 'New SOP'}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {isEdit
                ? `Editing ${form.template_id}`
                : 'Create a reusable procedure. Assign to roles so the right staff see it.'}
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded text-gray-500">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Title + description */}
          <div>
            <label className="text-xs font-medium text-gray-700 block mb-1">Title *</label>
            <input
              type="text"
              value={form.title}
              onChange={(e) => update('title', e.target.value)}
              placeholder="e.g. Store Opening Procedure"
              className="w-full px-3 py-2 border border-gray-300 rounded text-sm text-gray-900"
              autoFocus
            />
          </div>
          <div>
            <label className="text-xs font-medium text-gray-700 block mb-1">Description</label>
            <textarea
              value={form.description}
              onChange={(e) => update('description', e.target.value)}
              placeholder="One-line summary of what this SOP covers"
              rows={2}
              className="w-full px-3 py-2 border border-gray-300 rounded text-sm text-gray-900 resize-none"
            />
          </div>

          {/* Meta row */}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs font-medium text-gray-700 block mb-1">Category</label>
              <select
                value={form.category}
                onChange={(e) => update('category', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm text-gray-900"
              >
                {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-700 block mb-1">Frequency</label>
              <select
                value={form.frequency}
                onChange={(e) => update('frequency', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm text-gray-900"
              >
                {FREQUENCIES.map(f => <option key={f} value={f}>{f}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-700 block mb-1">Est. time (min)</label>
              <input
                type="number"
                min={1}
                max={480}
                value={form.estimated_time}
                onChange={(e) => update('estimated_time', parseInt(e.target.value || '15', 10) || 15)}
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm text-gray-900"
              />
            </div>
          </div>

          {/* Steps */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-medium text-gray-700">Steps *</label>
              <button
                onClick={addStep}
                className="text-xs font-medium text-bv-red-600 hover:text-bv-red-700 inline-flex items-center gap-1"
              >
                <Plus className="w-3.5 h-3.5" /> Add step
              </button>
            </div>
            {form.steps.length === 0 ? (
              <div className="text-center py-4 text-xs text-gray-500 border border-dashed border-gray-300 rounded">
                No steps yet. Click "Add step" to begin.
              </div>
            ) : (
              <div className="space-y-2">
                {form.steps.map((step, idx) => (
                  <div key={idx} className="border border-gray-200 rounded-lg p-3 bg-gray-50">
                    <div className="flex items-start gap-2">
                      <div className="flex flex-col gap-0.5 pt-1">
                        <button
                          onClick={() => moveStep(idx, -1)}
                          disabled={idx === 0}
                          className="text-gray-400 hover:text-gray-700 text-xs disabled:opacity-30"
                          aria-label="Move up"
                        >▲</button>
                        <button
                          onClick={() => moveStep(idx, 1)}
                          disabled={idx === form.steps.length - 1}
                          className="text-gray-400 hover:text-gray-700 text-xs disabled:opacity-30"
                          aria-label="Move down"
                        >▼</button>
                      </div>
                      <span className="w-7 h-7 rounded-full bg-bv-red-100 text-bv-red-700 text-xs font-semibold flex items-center justify-center flex-shrink-0 mt-0.5">
                        {idx + 1}
                      </span>
                      <div className="flex-1 space-y-1.5">
                        <input
                          type="text"
                          value={step.instruction}
                          onChange={(e) => updateStep(idx, { instruction: e.target.value })}
                          placeholder="What the staff needs to do at this step"
                          className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-gray-900"
                        />
                        <input
                          type="text"
                          value={step.warning || ''}
                          onChange={(e) => updateStep(idx, { warning: e.target.value })}
                          placeholder="Optional warning (e.g. 'Never share security code')"
                          className="w-full px-2 py-1.5 border border-amber-200 rounded text-xs text-amber-800 placeholder:text-amber-400"
                        />
                      </div>
                      <button
                        onClick={() => removeStep(idx)}
                        className="text-red-500 hover:text-red-700 p-1"
                        aria-label="Remove step"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Role assignment */}
          <div>
            <label className="text-xs font-medium text-gray-700 block mb-2">Assign to roles</label>
            <div className="flex flex-wrap gap-1.5">
              {ALL_ROLES.map(role => {
                const active = form.assigned_roles.includes(role);
                return (
                  <button
                    key={role}
                    onClick={() => toggleRole(role)}
                    className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
                      active
                        ? 'bg-bv-red-600 text-white border-bv-red-600'
                        : 'bg-white text-gray-700 border-gray-300 hover:border-bv-red-300'
                    }`}
                  >
                    {active && <Check className="inline w-3 h-3 mr-1" />}
                    {role.replace(/_/g, ' ')}
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-gray-500 mt-2">
              Staff in any of the selected roles will see this SOP in their checklist view.
              Leave empty to make it visible to everyone at the assigned store.
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-gray-200 bg-gray-50">
          {isEdit ? (
            <button
              onClick={handleDelete}
              disabled={isDeleting || isSaving}
              className="inline-flex items-center gap-1.5 text-sm text-red-600 hover:text-red-700 disabled:opacity-50"
            >
              {isDeleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <AlertTriangle className="w-4 h-4" />}
              Archive
            </button>
          ) : <span />}
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              disabled={isSaving || isDeleting}
              className="px-4 py-2 rounded text-sm font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={isSaving || isDeleting}
              className="px-5 py-2 rounded text-sm font-semibold bg-bv-red-600 text-white hover:bg-bv-red-700 disabled:opacity-50 inline-flex items-center gap-2"
            >
              {isSaving && <Loader2 className="w-4 h-4 animate-spin" />}
              {isEdit ? 'Save changes' : 'Create SOP'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function emptyForm(): SopTemplateForm {
  return {
    title: '',
    description: '',
    category: 'Operations',
    frequency: 'DAILY',
    estimated_time: 15,
    steps: [],
    assigned_roles: [],
    assigned_users: [],
  };
}

function deepClone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v));
}

export default SopEditorModal;
