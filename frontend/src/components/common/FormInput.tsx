// ============================================================================
// IMS 2.0 - Form Input Component with Validation
// ============================================================================

import { AlertCircle } from 'lucide-react';
import clsx from 'clsx';

interface FormInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  hint?: string;
  required?: boolean;
  helperText?: string;
}

export function FormInput({
  label,
  error,
  hint,
  required,
  helperText,
  className,
  id,
  ...props
}: FormInputProps) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');

  return (
    <div className="space-y-1">
      {label && (
        <label htmlFor={inputId} className="block text-sm font-medium text-gray-700">
          {label}
          {required && <span className="text-red-500 ml-1">*</span>}
        </label>
      )}
      <input
        id={inputId}
        className={clsx(
          'w-full px-3 py-2 border rounded-lg transition-colors',
          error
            ? 'border-red-300 bg-red-50 text-gray-900 placeholder-red-300 focus:outline-none focus:ring-2 focus:ring-red-500'
            : 'border-gray-300 bg-white text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500',
          className
        )}
        {...props}
      />
      {error && (
        <div className="flex items-center gap-1 text-sm text-red-600 mt-1">
          <AlertCircle className="w-4 h-4" />
          <span>{error}</span>
        </div>
      )}
      {!error && hint && (
        <p className="text-xs text-gray-500">{hint}</p>
      )}
      {helperText && !error && (
        <p className="text-xs text-gray-600">{helperText}</p>
      )}
    </div>
  );
}

export default FormInput;
