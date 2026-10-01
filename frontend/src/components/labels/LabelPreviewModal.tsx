// ============================================================================
// IMS 2.0 - Label Preview + Print modal
// ============================================================================
// Shows a live HTML preview of a thermal label (the same HTML used for the
// print-window fallback) and a Print button that routes through QZ Tray
// (silent raw ZPL) or falls back to an HTML print window. Fail-soft.

import { useEffect, useMemo, useState } from 'react';
import { Printer, X } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { labelsApi } from '../../services/api/labels';
import { buildJobLabel, labelBaseCss } from './labelTemplates';
import type { JobLabelData } from './labelTemplates';
import { printJobLabel } from './printLabel';

// Workshop JOB labels only. Stock (unit) labels: UnitLabelsModal + unitLabel.ts.
export type LabelModalSpec = { kind: 'job'; jobId: string; type: 'traveler' | 'stage' | 'ready' };

interface LabelPreviewModalProps {
  spec: LabelModalSpec;
  onClose: () => void;
  /** Optional fallback payload so a preview renders even if fetch fails. */
  fallbackJob?: Partial<JobLabelData>;
}

export function LabelPreviewModal({ spec, onClose, fallbackJob }: LabelPreviewModalProps) {
  const toast = useToast();
  const [jobData, setJobData] = useState<JobLabelData | null>(null);
  const [loading, setLoading] = useState(true);
  const [printing, setPrinting] = useState(false);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      try {
        const d = await labelsApi.getJobLabel(spec.jobId, spec.type);
        if (active) {
          // Merge the active-store fallback UNDER the backend payload so the
          // issuing-store identity is always present even when the label
          // lookup returns a thin payload (no hardcoded brand fallback).
          const base = (fallbackJob || {}) as Partial<JobLabelData>;
          setJobData(
            d?.job_id
              ? ({ ...base, ...d } as JobLabelData)
              : ({ job_id: spec.jobId, ...base } as JobLabelData),
          );
        }
      } catch {
        if (active) {
          setJobData({ job_id: spec.jobId, ...(fallbackJob || {}) } as JobLabelData);
        }
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec]);

  const built = useMemo(
    () => (jobData ? buildJobLabel(spec.type, jobData) : null),
    [spec, jobData],
  );

  const previewSrcDoc = useMemo(() => {
    if (!built) return '';
    return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${labelBaseCss(
      built.widthMm,
      built.heightMm,
    )} body{padding:8px;background:#fff}</style></head><body>${built.html}</body></html>`;
  }, [built]);

  const handlePrint = async () => {
    setPrinting(true);
    try {
      const result = await printJobLabel(spec.jobId, spec.type, fallbackJob);
      if (result.method === 'qz') toast.success(result.message);
      else if (result.method === 'html') toast.info(result.message);
      else toast.error(result.message);
    } catch {
      toast.error('Print failed.');
    } finally {
      setPrinting(false);
    }
  };

  const title =
    spec.type === 'traveler'
      ? 'Work Order Label'
      : spec.type === 'ready'
        ? 'Ready / Pickup Label'
        : 'Stage Sticker';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[70] p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h2 className="text-lg font-bold text-gray-900">{title}</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={handlePrint}
              disabled={loading || printing}
              className="btn-primary text-sm flex items-center gap-2 disabled:opacity-50"
            >
              <Printer className="w-4 h-4" />
              {printing ? 'Printing...' : 'Print'}
            </button>
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
        <div className="p-4">
          {loading ? (
            <div className="text-center text-gray-500 py-8">Loading label...</div>
          ) : (
            <div className="flex justify-center bg-gray-50 rounded-lg border border-gray-200 p-3">
              <iframe
                title="label-preview"
                srcDoc={previewSrcDoc}
                className="w-full bg-white"
                style={{ height: built ? `${built.heightMm * 4 + 60}px` : '200px', border: 'none' }}
              />
            </div>
          )}
          <p className="mt-3 text-xs text-gray-500 text-center">
            Prints silently via QZ Tray when configured; otherwise opens a print window.
          </p>
        </div>
      </div>
    </div>
  );
}

export default LabelPreviewModal;
