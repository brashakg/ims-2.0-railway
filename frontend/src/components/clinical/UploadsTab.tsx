// ============================================================================
// IMS 2.0 - Uploads Tab Component
// ============================================================================

import { Camera, FileText, Trash2 } from 'lucide-react';
import type { UploadedFile } from './eyeTestTypes';

interface UploadsTabProps {
  uploads: UploadedFile[];
  onUpload: (event: React.ChangeEvent<HTMLInputElement>) => void;
  onRemove: (id: string) => void;
}

export function UploadsTab({ uploads, onUpload, onRemove }: UploadsTabProps) {
  return (
    <div className="space-y-4">
      <div className="card">
        <h4 className="font-medium text-gray-800 mb-4">Upload Previous Prescription / Documents</h4>
        <div className="border-2 border-dashed border-gray-300 rounded-lg p-12 text-center bg-gray-50">
          <input
            type="file"
            id="file-upload"
            className="hidden"
            multiple
            accept="image/*,.pdf"
            onChange={onUpload}
          />
          <label
            htmlFor="file-upload"
            className="cursor-pointer flex flex-col items-center gap-3"
          >
            <Camera className="w-12 h-12 text-gray-500" />
            <span className="text-gray-600 font-medium">Click to upload files</span>
            <span className="text-sm text-gray-500">PNG, JPG, PDF up to 10MB</span>
          </label>
        </div>

        {uploads.length > 0 && (
          <div className="mt-6 space-y-2">
            <h4 className="text-sm font-medium text-gray-700">Uploaded Files</h4>
            <div className="grid grid-cols-2 gap-3">
              {uploads.map(file => (
                <div
                  key={file.id}
                  className="flex items-center justify-between p-3 bg-gray-50 rounded-lg border border-gray-200"
                >
                  <div className="flex items-center gap-3">
                    <FileText className="w-5 h-5 text-gray-500" />
                    <div>
                      <p className="text-sm font-medium text-gray-900 truncate max-w-[150px]">{file.name}</p>
                      <p className="text-xs text-gray-500">
                        {(file.size / 1024).toFixed(1)} KB
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => onRemove(file.id)}
                    className="p-1 text-gray-500 hover:text-red-600 transition-colors"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
