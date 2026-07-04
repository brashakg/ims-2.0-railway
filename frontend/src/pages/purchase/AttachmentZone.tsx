// ============================================================================
// IMS 2.0 - GRN attachment upload zone (Purchase P1 / S3)
// ============================================================================
// Extracted verbatim from GoodsReceiptCockpit.tsx (procurement Phase 2) so the
// express-receive panel and the classic two-step receive form share ONE
// upload UI for the mandatory no-paper-no-stock document gate.

import { useRef } from 'react';
import { Upload, X, CheckCircle2, Loader2 } from 'lucide-react';
import type { UploadDocResult } from '../../services/api/grnCockpit';

export interface AttachmentZoneProps {
  uploaded: UploadDocResult | null;
  uploading: boolean;
  onSelect: (file: File) => void;
  onRemove: () => void;
}

export function AttachmentZone({ uploaded, uploading, onSelect, onRemove }: AttachmentZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) onSelect(file);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) onSelect(file);
    // Reset so the same file can be re-selected after remove
    e.target.value = '';
  };

  if (uploaded && uploaded.file_id) {
    return (
      <div
        className="flex items-center gap-3 rounded-lg px-4 py-3"
        style={{
          border: '1.5px solid var(--ok)',
          background: 'rgba(34,197,94,0.06)',
        }}
      >
        <CheckCircle2 className="w-5 h-5 flex-shrink-0" style={{ color: 'var(--ok)' }} />
        <div className="flex-1 min-w-0">
          <p
            className="text-sm font-medium truncate"
            style={{ color: 'var(--ink)' }}
          >
            {uploaded.filename}
          </p>
          <p className="text-xs" style={{ color: 'var(--ink-4)' }}>
            {(uploaded.size / 1024).toFixed(0)} KB &middot;{' '}
            {uploaded.persisted ? 'Saved to file store' : 'Buffered (not yet persisted)'}
          </p>
        </div>
        <button
          type="button"
          className="btn sm"
          onClick={onRemove}
          title="Remove and upload a different file"
        >
          <X className="w-3.5 h-3.5" />
          Replace
        </button>
      </div>
    );
  }

  return (
    <div
      className="rounded-lg flex flex-col items-center justify-center gap-2 cursor-pointer transition-colors"
      style={{
        border: '2px dashed var(--line)',
        minHeight: 100,
        padding: '20px 16px',
      }}
      onClick={() => inputRef.current?.click()}
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
      aria-label="Upload vendor invoice or challan"
    >
      <input
        ref={inputRef}
        type="file"
        accept="image/*,application/pdf"
        onChange={handleChange}
        style={{ display: 'none' }}
      />
      {uploading ? (
        <>
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--bv)' }} />
          <span className="text-sm" style={{ color: 'var(--ink-3)' }}>
            Uploading…
          </span>
        </>
      ) : (
        <>
          <Upload className="w-6 h-6" style={{ color: 'var(--ink-4)' }} />
          <span className="text-sm font-medium" style={{ color: 'var(--ink-2)' }}>
            Drag &amp; drop or click to upload
          </span>
          <span className="text-xs" style={{ color: 'var(--ink-4)' }}>
            Vendor invoice or delivery challan &middot; Image or PDF &middot; max 25 MB
          </span>
        </>
      )}
    </div>
  );
}

export default AttachmentZone;
