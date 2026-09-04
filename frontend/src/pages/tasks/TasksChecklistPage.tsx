// ============================================================================
// IMS 2.0 - /tasks/checklists  (URL DELIBERATELY UNCHANGED - the Hub links it)
// ============================================================================
// The daily SOP checklist: the thing that proves a shop opened correctly.
// It used to be one of three rival tabs on TasksDashboard, and it was in no
// menu at all. Now it is this page, and only this, at the address it already
// had.
//
// OWNER RULING 2026-09-03 - no invented procedures. When no checklist is
// configured this page says so and offers to add one. It does NOT offer to
// plant a pre-written "starter set": the old empty state's "Create starter
// checklists" button wrote three fabricated procedures straight into the live
// database, including "Check cash register float (Rs 5,000)" and "Lock cash in
// safe (retain Rs 5,000)". In a live shop a staff member cannot tell that from
// policy the owner wrote. The owner supplies the real procedures; this page
// leaves a clean place to put them.

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, ListChecks, Loader2, Plus } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { tasksApi } from '../../services/api';
import { canSeeTeamTasks } from './taskRoles';
import { useSopChecklist, useSopTemplates } from './tasksQueries';

export function TasksChecklistPage() {
  const { user } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const templatesQ = useSopTemplates(user?.activeStoreId);
  // Daily checklists = DAILY (or unspecified-frequency) active templates.
  const daily = (templatesQ.data ?? []).filter((t) => !t.frequency || t.frequency === 'DAILY');

  const [templateId, setTemplateId] = useState<string>('');
  useEffect(() => {
    if (daily.length === 0) {
      setTemplateId('');
      return;
    }
    if (!daily.some((t) => t.id === templateId)) setTemplateId(daily[0].id);
  }, [daily, templateId]);

  const checklistQ = useSopChecklist(templateId || undefined);
  const checklist = checklistQ.data;
  const canManageSops = canSeeTeamTasks(user?.roles);

  const toggle = async (stepNumber: number, completed: boolean) => {
    if (!templateId) return;
    try {
      const data = await tasksApi.toggleSopChecklistItem({
        template_id: templateId,
        step_number: stepNumber,
        completed,
      });
      queryClient.setQueryData(['tasks', 'sop-checklist', templateId], data);
    } catch {
      toast.error('Failed to update checklist');
    }
  };

  if (templatesQ.isPending) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
      </div>
    );
  }

  if (templatesQ.isError) {
    return (
      <div
        className="s-section flex items-center gap-2"
        style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)' }}
      >
        <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
        <span style={{ color: 'var(--err)' }}>Failed to load checklists.</span>
        <button onClick={() => templatesQ.refetch()} className="btn sm ml-auto">Retry</button>
      </div>
    );
  }

  if (daily.length === 0) {
    return (
      <div className="card text-center py-12">
        <ListChecks className="w-12 h-12 text-gray-300 mx-auto mb-3" />
        <p className="text-gray-700 font-medium">No daily checklist is set up for this store</p>
        <p className="text-gray-500 text-sm mt-1 mb-4" style={{ maxWidth: 460, margin: '4px auto 16px' }}>
          Nothing is shown here until someone writes the store's real opening,
          closing or stock-count procedure. This page will never invent one.
        </p>
        {canManageSops ? (
          <button onClick={() => navigate('/tasks/sops')} className="btn sm primary inline-flex items-center gap-2">
            <Plus className="w-4 h-4" /> Add a checklist
          </button>
        ) : (
          <p className="text-gray-500 text-sm">Ask your store manager to add one.</p>
        )}
      </div>
    );
  }

  return (
    <div className="card">
      {/* Template selector */}
      <div className="mb-6 flex gap-3 flex-wrap">
        {daily.map((t) => (
          <button
            key={t.id}
            onClick={() => setTemplateId(t.id)}
            className={clsx(
              'px-4 py-2 rounded-lg font-medium transition-colors',
              templateId === t.id
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200',
            )}
          >
            {t.title}
          </button>
        ))}
      </div>

      {checklistQ.isPending ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
        </div>
      ) : !checklist ? (
        <div className="text-center py-10 text-gray-500 text-sm">Select a checklist above.</div>
      ) : (
        <>
          <div className="mb-6">
            <div className="flex items-center justify-between mb-2">
              <p className="text-gray-600 font-medium">
                Progress
                {checklist.status === 'COMPLETED' && (
                  <span className="ml-2 text-xs px-2 py-0.5 rounded bg-green-100 text-green-700">Done</span>
                )}
              </p>
              <p className="text-sm text-gray-500">
                {checklist.progress.done} of {checklist.progress.total} completed · {checklist.date}
              </p>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className="bg-green-500 h-2 rounded-full transition-all duration-300"
                style={{ width: `${checklist.progress.percent}%` }}
              />
            </div>
          </div>

          <div className="space-y-3">
            {checklist.items.map((item) => (
              <label
                key={item.step_number}
                className="flex items-start gap-3 p-3 rounded-lg hover:bg-gray-100 transition-colors cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={item.completed}
                  onChange={() => toggle(item.step_number, !item.completed)}
                  className="mt-1 w-5 h-5 rounded border-gray-200 text-green-500 cursor-pointer"
                />
                <span className="flex-1">
                  <span
                    className={clsx(
                      'text-sm',
                      item.completed ? 'line-through text-gray-500' : 'text-gray-600',
                    )}
                  >
                    {item.instruction}
                  </span>
                  {item.warning && (
                    <span className="block text-xs text-orange-600 mt-0.5">{item.warning}</span>
                  )}
                </span>
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default TasksChecklistPage;
