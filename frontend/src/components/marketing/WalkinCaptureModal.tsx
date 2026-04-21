// ============================================================================
// IMS 2.0 - Walk-in Capture Modal
// ============================================================================

import { useState } from 'react';
import { X, UserPlus, Phone, Tag, FileText } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { marketingApi } from '../../services/api/marketing';

interface WalkinCaptureModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const INTERESTS = [
  { value: 'frames', label: 'Frames / Eyeglasses' },
  { value: 'sunglasses', label: 'Sunglasses' },
  { value: 'lenses', label: 'Lenses / Contact Lenses' },
  { value: 'eye_test', label: 'Eye Test' },
  { value: 'other', label: 'Other / Browsing' },
];

export function WalkinCaptureModal({ isOpen, onClose }: WalkinCaptureModalProps) {
  const { user } = useAuth();
  const toast = useToast();
  const [phone, setPhone] = useState('');
  const [name, setName] = useState('');
  const [interest, setInterest] = useState('frames');
  const [notes, setNotes] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async () => {
    if (!phone || phone.length !== 10) {
      toast.error('Please enter a valid 10-digit phone number');
      return;
    }

    setIsSubmitting(true);
    try {
      await marketingApi.createWalkin({
        phone,
        name: name || undefined,
        interest,
        notes: notes || undefined,
        store_id: user?.activeStoreId,
      });
      toast.success('Walk-in registered! Follow-up created for tomorrow.');
      setPhone('');
      setName('');
      setInterest('frames');
      setNotes('');
      onClose();
    } catch {
      toast.error('Failed to register walk-in');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b">
          <div className="flex items-center gap-2">
            <UserPlus className="w-5 h-5 text-bv-red-600" />
            <h2 className="text-lg font-semibold">Register Walk-in</h2>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded-lg">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              <Phone className="w-4 h-4 inline mr-1" />
              Phone Number *
            </label>
            <input
              type="tel"
              value={phone}
              onChange={e => setPhone(e.target.value.replace(/\D/g, '').slice(0, 10))}
              placeholder="10-digit mobile number"
              className="input-field"
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Optional"
              className="input-field"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              <Tag className="w-4 h-4 inline mr-1" />
              Interest
            </label>
            <select value={interest} onChange={e => setInterest(e.target.value)} className="input-field">
              {INTERESTS.map(i => (
                <option key={i.value} value={i.value}>{i.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              <FileText className="w-4 h-4 inline mr-1" />
              Notes
            </label>
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
            {isSubmitting ? 'Registering...' : 'Register Walk-in'}
          </button>
        </div>
      </div>
    </div>
  );
}
