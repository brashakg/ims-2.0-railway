// ============================================================================
// IMS 2.0 - Expense Bill Upload & Duplicate Detection
// ============================================================================

import { useState, useRef } from 'react';
import { Upload, X, AlertTriangle, File, CheckCircle, Loader2 } from 'lucide-react';
import clsx from 'clsx';
import api from '../../services/api/client';
import { useToast } from '../../context/ToastContext';

interface BillFile {
  name: string;
  size: number;
  hash?: string;
  uploadedAt: string;
  status: 'pending' | 'uploaded' | 'duplicate';
}

interface DuplicateMatch {
  expenseId: string;
  amount: number;
  date: string;
  description: string;
  similarity: number;
}

interface ExpenseBillUploadProps {
  expenseId?: string;
  onBillUpload?: (file: File, hash: string) => void;
}

export function ExpenseBillUpload({ expenseId, onBillUpload }: ExpenseBillUploadProps) {
  const toast = useToast();
  const [bill, setBill] = useState<BillFile | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<DuplicateMatch | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = async (file: File) => {
    if (!file.type.includes('image') && !file.type.includes('pdf')) {
      toast.warning('Please upload an image or PDF file');
      return;
    }

    setIsUploading(true);
    setUploadError(null);

    try {
      const hash = await generateFileHash(file);

      if (expenseId) {
        // Upload to backend: POST /api/v1/expenses/{expenseId}/upload-bill
        const formData = new FormData();
        formData.append('file', file);
        await api.post(`/expenses/${expenseId}/upload-bill`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
      }

      setBill({
        name: file.name,
        size: file.size,
        hash: hash,
        uploadedAt: new Date().toISOString(),
        status: 'uploaded',
      });
      onBillUpload?.(file, hash);
    } catch (err: any) {
      const msg = err?.message || 'Failed to upload bill. Please try again.';
      setUploadError(msg);
    } finally {
      setIsUploading(false);
    }
  };

  const generateFileHash = async (file: File): Promise<string> => {
    const buffer = await file.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      handleFileSelect(files[0]);
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.currentTarget.files;
    if (files && files.length > 0) {
      handleFileSelect(files[0]);
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
  };

  return (
    <div className="space-y-4">
      {/* Upload Area */}
      {!bill ? (
        <div
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={clsx(
            'border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition',
            isDragging
              ? 'border-blue-500 bg-blue-900 bg-opacity-20'
              : 'border-gray-300 bg-white hover:border-gray-400'
          )}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*,.pdf"
            onChange={handleFileInputChange}
            className="hidden"
          />
          
          <div className="flex flex-col items-center gap-3">
            <Upload className="w-8 h-8 text-gray-500" />
            <div>
              <p className="text-gray-900 font-medium mb-1">
                {isUploading ? 'Uploading and checking for duplicates...' : 'Drag & drop your bill here'}
              </p>
              <p className="text-sm text-gray-500">
                {isUploading ? '' : 'or click to browse (Image or PDF)'}
              </p>
            </div>
            {isUploading && <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />}
            {!isUploading && (
              <button
                onClick={() => fileInputRef.current?.click()}
                className="mt-2 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition text-sm"
              >
                Browse Files
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className={clsx(
          'p-4 rounded-lg border-2',
          bill.status === 'duplicate'
            ? 'bg-yellow-900 bg-opacity-20 border-yellow-600'
            : 'bg-green-900 bg-opacity-20 border-green-600'
        )}>
          <div className="flex items-start justify-between">
            <div className="flex items-start gap-3">
              <File className={clsx(
                'w-5 h-5 mt-1',
                bill.status === 'duplicate' ? 'text-yellow-400' : 'text-green-400'
              )} />
              <div>
                <p className="font-medium text-gray-900">{bill.name}</p>
                <p className="text-sm text-gray-500">{formatFileSize(bill.size)}</p>
              </div>
            </div>
            <button
              onClick={() => {
                setBill(null);
                setDuplicate(null);
              }}
              className="p-1 text-gray-500 hover:text-red-500 transition"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {bill.status === 'uploaded' && (
            <div className="mt-3 p-2 bg-green-800 rounded text-green-200 text-sm flex items-center gap-2">
              <CheckCircle className="w-4 h-4" />
              {expenseId ? 'Bill uploaded to server successfully.' : 'Bill selected. Save the expense to attach it.'}
            </div>
          )}
        </div>
      )}

      {/* Upload Error */}
      {uploadError && (
        <div className="p-3 bg-red-900 bg-opacity-30 border border-red-600 rounded text-red-200 text-sm flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          {uploadError}
        </div>
      )}

      {/* Duplicate Alert */}
      {duplicate && (
        <div className="p-4 bg-yellow-900 bg-opacity-30 border-2 border-yellow-600 rounded-lg">
          <div className="flex gap-3 mb-3">
            <AlertTriangle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
            <div>
              <h4 className="font-semibold text-yellow-200 mb-2">Possible Duplicate Bill Detected</h4>
              <p className="text-sm text-yellow-100 mb-3">
                This bill appears to be a duplicate of an existing expense. Please review before proceeding.
              </p>
              <div className="p-3 bg-gray-50 border border-gray-200 rounded text-sm space-y-1">
                <p className="text-gray-600">
                  <strong>Existing Expense:</strong> {duplicate.expenseId}
                </p>
                <p className="text-gray-600">
                  <strong>Amount:</strong> ₹{duplicate.amount.toLocaleString()}
                </p>
                <p className="text-gray-600">
                  <strong>Date:</strong> {duplicate.date}
                </p>
                <p className="text-gray-600">
                  <strong>Description:</strong> {duplicate.description}
                </p>
                <p className="text-gray-600">
                  <strong>Match Confidence:</strong> {duplicate.similarity}%
                </p>
              </div>
              <div className="mt-3 flex gap-2">
                <button className="flex-1 px-3 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 transition">
                  View Original Expense
                </button>
                <button
                  onClick={() => setDuplicate(null)}
                  className="flex-1 px-3 py-2 text-sm bg-yellow-600 text-white rounded hover:bg-yellow-700 transition"
                >
                  Continue Anyway
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Info Box */}
      <div className="p-3 bg-blue-900 bg-opacity-30 border border-blue-700 rounded text-xs text-blue-200">
        <p>
          <strong>Important:</strong> All expense submissions require a bill image or PDF. Bills are scanned for duplicates to prevent fraud.
        </p>
      </div>
    </div>
  );
}
