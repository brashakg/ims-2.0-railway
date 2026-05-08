// ============================================================================
// IMS 2.0 — Handoff Upload Modal
// ============================================================================
// "Send file" — sender side of the handoff feature. Picks a file (image
// or PDF, ≤25 MB), assigns to one or more eligible recipients, sets a
// 3-30 day TTL, optional title + description.

import { useEffect, useRef, useState } from 'react';
import {
  X,
  Upload,
  Loader2,
  FileText,
  Image as ImageIcon,
  AlertCircle,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { handoffsApi, type EligibleRecipient } from '../../services/api/handoffs';
import RecipientPicker from './RecipientPicker';

interface HandoffUploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSent?: () => void; // parent refreshes list after success
}

const ACCEPTED_MIME = [
  'image/jpeg',
  'image/jpg',
  'image/png',
  'image/heic',
  'image/heif',
  'image/webp',
  'application/pdf',
];
const ACCEPT_ATTR = 'image/*,application/pdf';
const MAX_BYTES = 25 * 1024 * 1024; // 25 MB — must match backend cap

const MIN_VALIDITY = 3;
const MAX_VALIDITY = 30;
const DEFAULT_VALIDITY = 7;

const TITLE_MIN = 2;
const TITLE_MAX = 120;
const DESC_MAX = 200;

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function HandoffUploadModal({ isOpen, onClose, onSent }: HandoffUploadModalProps) {
  const toast = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [recipients, setRecipients] = useState<EligibleRecipient[]>([]);
  const [validityDays, setValidityDays] = useState(DEFAULT_VALIDITY);
  const [isSending, setIsSending] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);

  // Reset state on open
  useEffect(() => {
    if (isOpen) {
      setFile(null);
      setTitle('');
      setDescription('');
      setRecipients([]);
      setValidityDays(DEFAULT_VALIDITY);
      setIsSending(false);
      setFileError(null);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleFilePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    setFileError(null);
    if (!f) {
      setFile(null);
      return;
    }
    if (!ACCEPTED_MIME.includes(f.type)) {
      setFileError(`Unsupported file type (${f.type || 'unknown'}). Accepts images and PDFs only.`);
      setFile(null);
      return;
    }
    if (f.size > MAX_BYTES) {
      setFileError(`File too large (${humanSize(f.size)}). Maximum is 25 MB.`);
      setFile(null);
      return;
    }
    if (f.size === 0) {
      setFileError('File is empty.');
      setFile(null);
      return;
    }
    setFile(f);
    // Pre-fill title with the filename minus extension if user hasn't typed one
    if (!title.trim()) {
      const cleanName = f.name.replace(/\.[^.]+$/, '').slice(0, TITLE_MAX);
      setTitle(cleanName);
    }
  };

  const titleTrimmed = title.trim();
  const titleValid = titleTrimmed.length >= TITLE_MIN && titleTrimmed.length <= TITLE_MAX;
  const recipientsValid = recipients.length >= 1;
  const validityValid = validityDays >= MIN_VALIDITY && validityDays <= MAX_VALIDITY;
  const canSubmit = !!file && titleValid && recipientsValid && validityValid && !isSending;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || !file) return;
    setIsSending(true);
    try {
      await handoffsApi.upload(
        file,
        titleTrimmed,
        recipients.map((r) => r.user_id),
        validityDays,
        description.trim() || undefined,
      );
      toast.success(
        `Sent to ${recipients.length} recipient${recipients.length === 1 ? '' : 's'}.`,
      );
      onSent?.();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to send handoff.');
    } finally {
      setIsSending(false);
    }
  };

  const isPdf = file?.type === 'application/pdf';

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 sticky top-0 bg-white z-10">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Send a file</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Hand off a scan or PDF to a Store Manager, Accountant, Admin or Superadmin.
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-gray-500 hover:text-gray-600 transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-4 space-y-5">
          {/* File picker */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              File <span className="text-red-500">*</span>
            </label>
            {!file ? (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="w-full flex flex-col items-center justify-center gap-2 px-4 py-6 border-2 border-dashed border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50 transition-colors"
              >
                <Upload className="w-6 h-6 text-gray-500" />
                <span className="text-sm font-medium">Click to choose a file</span>
                <span className="text-xs text-gray-500">
                  JPG · PNG · HEIC · WEBP · PDF, up to 25 MB
                </span>
              </button>
            ) : (
              <div className="flex items-center gap-3 px-3 py-3 border border-gray-200 rounded-lg bg-gray-50">
                <span className="flex-shrink-0 w-9 h-9 rounded-md bg-white border border-gray-200 flex items-center justify-center text-gray-600">
                  {isPdf ? <FileText className="w-5 h-5" /> : <ImageIcon className="w-5 h-5" />}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">{file.name}</p>
                  <p className="text-xs text-gray-500">
                    {humanSize(file.size)} · {file.type || 'unknown'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setFile(null);
                    if (fileInputRef.current) fileInputRef.current.value = '';
                  }}
                  className="text-xs text-red-600 hover:text-red-700"
                >
                  Replace
                </button>
              </div>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPT_ATTR}
              onChange={handleFilePick}
              className="hidden"
            />
            {fileError && (
              <p className="text-xs text-red-600 mt-1.5 flex items-center gap-1">
                <AlertCircle className="w-3.5 h-3.5" />
                {fileError}
              </p>
            )}
          </div>

          {/* Title */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Title <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value.slice(0, TITLE_MAX))}
              placeholder="e.g. Vendor invoice — Vincent Chase Apr"
              className="input-field"
              minLength={TITLE_MIN}
              maxLength={TITLE_MAX}
              required
            />
            <p className="text-xs text-gray-500 mt-1">
              {titleTrimmed.length}/{TITLE_MAX} characters
            </p>
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Note for the recipient <span className="text-gray-500 font-normal">(optional)</span>
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value.slice(0, DESC_MAX))}
              placeholder="Add a one-liner so the recipient knows what to do."
              rows={2}
              className="input-field resize-none"
              maxLength={DESC_MAX}
            />
            <p className="text-xs text-gray-500 mt-1">
              {description.length}/{DESC_MAX} characters
            </p>
          </div>

          {/* Recipients */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Recipients <span className="text-red-500">*</span>
            </label>
            <RecipientPicker selected={recipients} onChange={setRecipients} />
            {!recipientsValid && (
              <p className="text-xs text-gray-500 mt-1">Pick at least one recipient.</p>
            )}
          </div>

          {/* Validity */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Auto-expire after{' '}
              <span className="font-semibold">{validityDays} day{validityDays === 1 ? '' : 's'}</span>
            </label>
            <input
              type="range"
              min={MIN_VALIDITY}
              max={MAX_VALIDITY}
              step={1}
              value={validityDays}
              onChange={(e) => setValidityDays(Number(e.target.value))}
              className="w-full accent-bv-red-600"
            />
            <div className="flex justify-between text-xs text-gray-500 mt-1">
              <span>{MIN_VALIDITY} days</span>
              <span>{MAX_VALIDITY} days</span>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              The card and file are removed automatically once this expires.
            </p>
          </div>
        </form>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-4 border-t border-gray-200 bg-gray-50">
          <button type="button" onClick={onClose} className="btn-outline" disabled={isSending}>
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="btn-primary flex items-center gap-2 disabled:opacity-50"
          >
            {isSending && <Loader2 className="w-4 h-4 animate-spin" />}
            {isSending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default HandoffUploadModal;
