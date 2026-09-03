// ============================================================================
// IMS 2.0 - /tasks/sops  (the SOP library)
// ============================================================================
// OWNER RULING 2026-09-03 - THE FABRICATED SOPs ARE GONE.
//
// The old TaskManagementPage answered an empty template list by rendering four
// hard-coded SOPs styled exactly like real ones: "Store Opening Procedure",
// "End of Day Cash Reconciliation" (verify a Rs 5,000 starting float; retain
// Rs 5,000 in the safe overnight), "Customer Order Processing" and "Inventory
// Receiving". In four live shops a staff member could not tell those from
// policy the owner wrote. They are deleted, with no replacements invented -
// the owner supplies the real procedures; this page is the clean place to put
// them.
//
// Gated to TEAM_TASK_ROLES on the route (taskRoles.ts).

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Edit, Eye, ListChecks, Loader2, Plus } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { SopEditorModal, type SopTemplateForm } from '../../components/tasks/SopEditorModal';
import { useSopTemplates, type SopTemplate } from './tasksQueries';

function toForm(sop: SopTemplate): SopTemplateForm {
  return {
    template_id: sop.id,
    title: sop.title,
    description: sop.description,
    category: sop.category as SopTemplateForm['category'],
    frequency: sop.frequency as SopTemplateForm['frequency'],
    estimated_time: sop.estimatedTime,
    steps: sop.steps.map((s) => ({
      step_number: s.stepNumber,
      instruction: s.instruction,
      warning: s.warning,
    })),
    assigned_roles: sop.assignedRoles,
    assigned_users: [],
  };
}

export function TasksSopPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const sopsQ = useSopTemplates(user?.activeStoreId);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<SopTemplateForm | null>(null);
  const [search, setSearch] = useState('');

  const openEditor = (sop: SopTemplate | null) => {
    setEditing(sop ? toForm(sop) : null);
    setEditorOpen(true);
  };

  const needle = search.trim().toLowerCase();
  const sops = (sopsQ.data ?? []).filter(
    (s) =>
      !needle ||
      s.title.toLowerCase().includes(needle) ||
      s.description.toLowerCase().includes(needle) ||
      s.category.toLowerCase().includes(needle),
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <input
          type="text"
          placeholder="Search SOPs..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input-field flex-1"
          style={{ minWidth: 200 }}
        />
        <button onClick={() => openEditor(null)} className="btn sm primary">
          <Plus className="w-4 h-4" /> New SOP
        </button>
      </div>

      {sopsQ.isError && (
        <div
          className="s-section flex items-center gap-2"
          style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)' }}
        >
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>Failed to load SOPs.</span>
          <button onClick={() => sopsQ.refetch()} className="btn sm ml-auto">Retry</button>
        </div>
      )}

      {sopsQ.isPending ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
        </div>
      ) : sops.length === 0 ? (
        <div className="card text-center py-12">
          <ListChecks className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-700 font-medium">
            {needle ? 'No SOP matches that search' : 'No SOPs have been written yet'}
          </p>
          <p className="text-gray-500 text-sm mt-1 mb-4" style={{ maxWidth: 480, margin: '4px auto 16px' }}>
            {needle
              ? 'Clear the search to see every SOP in this store.'
              : 'This page shows only procedures someone here actually wrote. It will never fill itself with sample ones.'}
          </p>
          {!needle && (
            <button onClick={() => openEditor(null)} className="btn sm primary inline-flex items-center gap-2">
              <Plus className="w-4 h-4" /> Write the first SOP
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 desktop:grid-cols-2 gap-4">
          {sops.map((sop) => (
            <div key={sop.id} className="card hover:shadow-lg transition-shadow">
              <div className="flex items-center gap-2 mb-2">
                <h3 className="text-lg font-semibold text-gray-900">{sop.title}</h3>
                <span className="px-2 py-1 bg-gray-100 text-gray-700 text-xs rounded">
                  {sop.category}
                </span>
              </div>
              <p className="text-sm text-gray-600 mb-3">{sop.description}</p>

              <div className="grid grid-cols-2 gap-3 mb-4 p-3 bg-gray-50 rounded-lg">
                <div>
                  <p className="text-xs text-gray-600">Frequency</p>
                  <p className="text-sm font-medium text-gray-900">{sop.frequency}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Est. time</p>
                  <p className="text-sm font-medium text-gray-900">{sop.estimatedTime} mins</p>
                </div>
              </div>

              <div className="mb-3">
                <p className="text-xs text-gray-600 mb-2">Steps ({sop.steps.length}):</p>
                <div className="space-y-1">
                  {sop.steps.slice(0, 3).map((step) => (
                    <div key={step.id} className="flex items-start gap-2 text-sm">
                      <span className="text-gray-500 font-medium flex-shrink-0">{step.stepNumber}.</span>
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
                  {sop.lastUpdated ? `Updated: ${new Date(sop.lastUpdated).toLocaleDateString()}` : 'Never updated'}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => openEditor(sop)}
                    className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                    aria-label="View SOP"
                    title="View SOP"
                  >
                    <Eye className="w-4 h-4 text-gray-600" />
                  </button>
                  <button
                    onClick={() => openEditor(sop)}
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
        </div>
      )}

      <SopEditorModal
        isOpen={editorOpen}
        onClose={() => {
          setEditorOpen(false);
          setEditing(null);
        }}
        initial={editing}
        onSaved={() => queryClient.invalidateQueries({ queryKey: ['tasks', 'sop-templates'] })}
      />
    </div>
  );
}

export default TasksSopPage;
