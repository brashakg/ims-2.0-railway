// ============================================================================
// IMS 2.0 — 4-version prescription editor (May 2026)
// ============================================================================
// Optometrist-facing modal that captures the four Rx states for a visit:
//   - before_testing : auto-refractor / customer's existing Rx
//   - after_testing  : subjective refraction
//   - manual         : manual override (e.g. clip-on adjustments)
//   - final          : optometrist sign-off; mirrored to top-level on
//                       finalize so existing POS / Order code keeps working
// Backend: GET /prescriptions/{id}/versions, PATCH /prescriptions/{id}/version/{name},
// POST /prescriptions/{id}/finalize.

import { useEffect, useState } from 'react';
import { X, Eye, Save, Lock, AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';
import {
  prescriptionApi,
  type PrescriptionEyeData,
  type PrescriptionVersionData,
} from '../../services/api/sales';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

type VersionName = 'before_testing' | 'after_testing' | 'manual' | 'final';

const VERSION_TABS: Array<{
  name: VersionName;
  label: string;
  hint: string;
  defaultSource: string;
}> = [
  { name: 'before_testing', label: 'Before testing', hint: 'Auto-refractor / customer’s existing Rx', defaultSource: 'auto_ref' },
  { name: 'after_testing', label: 'After testing', hint: 'Subjective refraction by optometrist', defaultSource: 'subjective_refraction' },
  { name: 'manual', label: 'Manual override', hint: 'Optometrist tweaks (e.g. clip-on)', defaultSource: 'manual_override' },
  { name: 'final', label: 'Final', hint: 'Sign-off — mirrors to billed Rx', defaultSource: 'optometrist_signoff' },
];

const emptyEye = (): PrescriptionEyeData => ({
  sphere: null,
  cylinder: null,
  axis: null,
  addition: null,
  va: null,
});

const emptyVersion = (source: string): PrescriptionVersionData => ({
  right_eye: emptyEye(),
  left_eye: emptyEye(),
  pd: null,
  source,
  override_reason: null,
  signed_off_by: null,
});

interface Props {
  prescriptionId: string;
  isOpen: boolean;
  onClose: () => void;
  onFinalized?: () => void;
}

export function PrescriptionVersionsEditor({
  prescriptionId,
  isOpen,
  onClose,
  onFinalized,
}: Props) {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const canFinalize = hasRole(['OPTOMETRIST', 'SUPERADMIN', 'ADMIN']);

  const [activeTab, setActiveTab] = useState<VersionName>('before_testing');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [status, setStatus] = useState<'in_progress' | 'finalized'>('in_progress');
  const [finalizedAt, setFinalizedAt] = useState<string | undefined>(undefined);
  const [versions, setVersions] = useState<Record<VersionName, PrescriptionVersionData>>({
    before_testing: emptyVersion('auto_ref'),
    after_testing: emptyVersion('subjective_refraction'),
    manual: emptyVersion('manual_override'),
    final: emptyVersion('optometrist_signoff'),
  });

  // Load on open
  useEffect(() => {
    if (!isOpen || !prescriptionId) return;
    let cancelled = false;
    setLoading(true);
    prescriptionApi
      .getVersions(prescriptionId)
      .then((r) => {
        if (cancelled) return;
        setStatus(r.status);
        setFinalizedAt(r.finalized_at);
        setVersions({
          before_testing: r.versions.before_testing || emptyVersion('auto_ref'),
          after_testing: r.versions.after_testing || emptyVersion('subjective_refraction'),
          manual: r.versions.manual || emptyVersion('manual_override'),
          final: r.versions.final || emptyVersion('optometrist_signoff'),
        });
      })
      .catch(() => {
        if (!cancelled) toast.error('Failed to load prescription versions');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, prescriptionId]);

  if (!isOpen) return null;

  const current = versions[activeTab];
  const locked = status === 'finalized';

  const setEyeField = (
    eye: 'right_eye' | 'left_eye',
    field: keyof PrescriptionEyeData,
    raw: string,
  ) => {
    const value: string | number | null =
      field === 'va' ? raw || null : raw === '' ? null : Number.isFinite(parseFloat(raw)) ? parseFloat(raw) : null;
    setVersions((vs) => ({
      ...vs,
      [activeTab]: {
        ...vs[activeTab],
        [eye]: {
          ...(vs[activeTab][eye] || emptyEye()),
          [field]: value,
        },
      },
    }));
  };

  const setVersionField = <K extends keyof PrescriptionVersionData>(
    field: K,
    value: PrescriptionVersionData[K],
  ) => {
    setVersions((vs) => ({
      ...vs,
      [activeTab]: { ...vs[activeTab], [field]: value },
    }));
  };

  const handleSaveCurrent = async () => {
    if (locked) return;
    setSaving(true);
    try {
      const v = versions[activeTab];
      const payload: Partial<PrescriptionVersionData> = {
        right_eye: v.right_eye,
        left_eye: v.left_eye,
        pd: v.pd,
        source: v.source,
        override_reason: v.override_reason,
      };
      if (activeTab === 'final') {
        payload.signed_off_by = v.signed_off_by || user?.name || user?.id;
      }
      await prescriptionApi.patchVersion(prescriptionId, activeTab, payload);
      toast.success(`Saved ${activeTab.replace('_', ' ')}`);
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Save failed';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleFinalize = async () => {
    if (!canFinalize) {
      toast.error('Optometrist or admin role required to finalize');
      return;
    }
    if (!confirm('Finalize this prescription? It cannot be edited after this.')) return;
    setFinalizing(true);
    try {
      await prescriptionApi.finalizePrescription(prescriptionId);
      toast.success('Prescription finalized');
      setStatus('finalized');
      setFinalizedAt(new Date().toISOString());
      if (onFinalized) onFinalized();
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Finalize failed';
      toast.error(msg);
    } finally {
      setFinalizing(false);
    }
  };

  // The `final` tab needs both eyes + a signed_off_by to be ready
  const finalReady = (() => {
    const f = versions.final;
    const hasRe = !!(f.right_eye && (f.right_eye.sphere !== null || f.right_eye.cylinder !== null));
    const hasLe = !!(f.left_eye && (f.left_eye.sphere !== null || f.left_eye.cylinder !== null));
    return hasRe && hasLe;
  })();

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Eye className="w-5 h-5 text-blue-600" />
            <div>
              <h2 className="font-semibold text-gray-900">Prescription versions</h2>
              <p className="text-xs text-gray-500">
                {locked ? (
                  <span className="text-green-700 inline-flex items-center gap-1">
                    <Lock className="w-3 h-3" />
                    Finalized {finalizedAt ? new Date(finalizedAt).toLocaleString('en-IN') : ''}
                  </span>
                ) : (
                  'Capture all four states; finalize to lock.'
                )}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 text-gray-500 hover:bg-gray-100 rounded">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-200 bg-gray-50 overflow-x-auto">
          {VERSION_TABS.map((t) => {
            const v = versions[t.name];
            const filled = !!(
              v.right_eye?.sphere !== null ||
              v.left_eye?.sphere !== null ||
              v.pd !== null
            );
            const isActive = activeTab === t.name;
            return (
              <button
                key={t.name}
                onClick={() => setActiveTab(t.name)}
                className={clsx(
                  'flex-shrink-0 px-4 py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2',
                  isActive
                    ? 'border-bv-red-600 text-bv-red-700 bg-white'
                    : 'border-transparent text-gray-600 hover:text-gray-900',
                )}
              >
                {t.label}
                {filled ? (
                  <CheckCircle2 className="w-4 h-4 text-green-600" />
                ) : (
                  <span className="w-2 h-2 rounded-full bg-gray-300" />
                )}
              </button>
            );
          })}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
            </div>
          ) : (
            <>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
                <p className="font-medium">{VERSION_TABS.find((t) => t.name === activeTab)?.label}</p>
                <p className="text-xs text-blue-700 mt-0.5">
                  {VERSION_TABS.find((t) => t.name === activeTab)?.hint}
                </p>
              </div>

              {/* Eye grid */}
              {(['right_eye', 'left_eye'] as const).map((eyeKey) => {
                const eye = current[eyeKey] || emptyEye();
                return (
                  <div key={eyeKey} className="border border-gray-200 rounded-lg p-3">
                    <p className="font-medium text-gray-900 mb-2">
                      {eyeKey === 'right_eye' ? 'Right Eye (OD)' : 'Left Eye (OS)'}
                    </p>
                    <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                      <FieldBlock label="SPH" disabled={locked}>
                        <input
                          type="number"
                          step={0.25}
                          value={eye.sphere ?? ''}
                          disabled={locked}
                          onChange={(e) => setEyeField(eyeKey, 'sphere', e.target.value)}
                          className="w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50"
                        />
                      </FieldBlock>
                      <FieldBlock label="CYL" disabled={locked}>
                        <input
                          type="number"
                          step={0.25}
                          value={eye.cylinder ?? ''}
                          disabled={locked}
                          onChange={(e) => setEyeField(eyeKey, 'cylinder', e.target.value)}
                          className="w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50"
                        />
                      </FieldBlock>
                      <FieldBlock label="AXIS" disabled={locked}>
                        <input
                          type="number"
                          step={1}
                          min={1}
                          max={180}
                          value={eye.axis ?? ''}
                          disabled={locked}
                          onChange={(e) => setEyeField(eyeKey, 'axis', e.target.value)}
                          className="w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50"
                        />
                      </FieldBlock>
                      <FieldBlock label="ADD" disabled={locked}>
                        <input
                          type="number"
                          step={0.25}
                          value={eye.addition ?? ''}
                          disabled={locked}
                          onChange={(e) => setEyeField(eyeKey, 'addition', e.target.value)}
                          className="w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50"
                        />
                      </FieldBlock>
                      <FieldBlock label="VA" disabled={locked}>
                        <input
                          type="text"
                          placeholder="6/6"
                          value={eye.va ?? ''}
                          disabled={locked}
                          onChange={(e) => setEyeField(eyeKey, 'va', e.target.value)}
                          className="w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50"
                        />
                      </FieldBlock>
                    </div>
                  </div>
                );
              })}

              {/* PD + override reason */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <FieldBlock label="PD (mm)" disabled={locked}>
                  <input
                    type="number"
                    step={0.5}
                    value={current.pd ?? ''}
                    disabled={locked}
                    onChange={(e) =>
                      setVersionField('pd', e.target.value === '' ? null : parseFloat(e.target.value) || 0)
                    }
                    className="w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50"
                  />
                </FieldBlock>
                <FieldBlock label="Source" disabled={locked}>
                  <input
                    type="text"
                    value={current.source ?? ''}
                    disabled={locked}
                    onChange={(e) => setVersionField('source', e.target.value)}
                    className="w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50"
                  />
                </FieldBlock>
                {activeTab === 'manual' && (
                  <FieldBlock label="Override reason" disabled={locked}>
                    <input
                      type="text"
                      value={current.override_reason ?? ''}
                      disabled={locked}
                      onChange={(e) => setVersionField('override_reason', e.target.value)}
                      placeholder="Why manual?"
                      className="w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50"
                    />
                  </FieldBlock>
                )}
                {activeTab === 'final' && (
                  <FieldBlock label="Signed off by" disabled={locked}>
                    <input
                      type="text"
                      value={current.signed_off_by ?? user?.name ?? ''}
                      disabled={locked}
                      onChange={(e) => setVersionField('signed_off_by', e.target.value)}
                      className="w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50"
                    />
                  </FieldBlock>
                )}
              </div>

              {activeTab === 'final' && !finalReady && !locked && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-start gap-2 text-sm text-amber-800">
                  <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  <p>Both eyes need at least sphere or cylinder before you can finalize.</p>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-gray-200 bg-gray-50 flex items-center justify-end gap-2">
          {!locked && (
            <>
              <button
                type="button"
                onClick={handleSaveCurrent}
                disabled={saving || loading}
                className="px-3 py-2 bg-bv-red-600 hover:bg-bv-red-700 text-white text-sm font-semibold rounded flex items-center gap-2 disabled:opacity-50"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                Save {VERSION_TABS.find((t) => t.name === activeTab)?.label.toLowerCase()}
              </button>
              {activeTab === 'final' && (
                <button
                  type="button"
                  onClick={handleFinalize}
                  disabled={!finalReady || finalizing || !canFinalize}
                  className="px-3 py-2 bg-green-600 hover:bg-green-700 text-white text-sm font-semibold rounded flex items-center gap-2 disabled:opacity-50"
                >
                  {finalizing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                  Finalize prescription
                </button>
              )}
            </>
          )}
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function FieldBlock({
  label,
  disabled,
  children,
}: {
  label: string;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className={clsx('block text-xs font-medium mb-1', disabled ? 'text-gray-400' : 'text-gray-600')}>
        {label}
      </label>
      {children}
    </div>
  );
}

export default PrescriptionVersionsEditor;
