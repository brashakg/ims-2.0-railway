// ============================================================================
// IMS 2.0 - Walkout Record Modal
// ============================================================================

import { useState } from 'react';
import { X, AlertTriangle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { marketingApi } from '../../services/api/marketing';

interface WalkoutRecordModalProps {
  isOpen: boolean;
  onClose: () => void;
  customerId: string;
  customerName: string;
}

const REASONS = [
  { value: 'price', label: 'Price too high' },
  { value: 'style', label: 'Could not find right style' },
  { value: 'undecided', label: 'Still deciding' },
  { value: 'comparison', label: 'Wants to compare elsewhere' },
  { value: 'other', label: 'Other' },
];

export function WalkoutRecordModal({ isOpen, onClose, customerId, customerName }: WalkoutRecordModalProps) {
  const { user } = useAuth();
  const toast = useToast();
  const [framesTried, setFramesTried] = useState('');
  const [reason, setReason] = useState('undecided');
  const [notes, setNotes] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async () => {
    setIsSubmitting(true);
    try {
      const frames = framesTried.split(',').map(f => f.trim()).filter(Boolean);
      await marketingApi.recordWalkout(customerId, {
        frames_tried: frames,
        reason,
        notes: notes || undefined,
        store_id: user?.activeStoreId,
      });
      toast.success('Walkout recorded. Recovery message will be sent automatically.');
      onClose();
    } catch {
      toast.error('Failed to record walkout');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-amber-500" />
            <h2 className="text-lg font-semibold">Record Walkout</h2>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded-lg">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
            <p className="text-sm text-amber-800">
              Recording walkout for <strong>{customerName}</strong>. A recovery message with a special offer will be sent automatically.
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Frames Tried</label>
            <input
              type="text"
              value={framesTried}
              onChange={e => setFramesTried(e.target.value)}
              placeholder="e.g. Ray-Ban Aviator, Oakley Radar"
              className="input-field"
            />
            <p className="text-xs text-gray-500 mt-1">Separate multiple frames with commas</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Reason for Leaving</label>
            <select value={reason} onChange={e => setReason(e.target.value)} className="input-field">
              {REASONS.map(r => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Notes</label>
            <textarea
              value={notes}
              onChange={e => setNotes(e.target.value)}
              placeholder="Any additional notes..."
              className="input-field"
              rows={2}
            />
          </div>
        </div>

        <div className="flex gap-2 p-4 border-t">
          <button onClick={onClose} className="btn-secondary flex-1">Cancel</button>
          <button onClick={handleSubmit} disabled={isSubmitting} className="btn-primary flex-1">
            {isSubmitting ? 'Recording...' : 'Record & Schedule Recovery'}
          </button>
        </div>
      </div>
    </div>
  );
}
