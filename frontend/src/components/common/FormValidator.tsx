// ============================================================================
// IMS 2.0 - Form Validation Component
// ============================================================================
// Real-time field validation with error messages and visual feedback

import { useState, useCallback } from 'react';
import { AlertCircle, CheckCircle2 } from 'lucide-react';
import clsx from 'clsx';

export type ValidationRule = 'required' | 'email' | 'phone' | 'url' | 'number' | 'minLength' | 'maxLength' | 'pattern' | 'match';

export interface ValidationConfig {
  rule: ValidationRule;
  value?: any;
  message: string;
}

export interface FieldError {
  field: string;
  message: string;
  rule: ValidationRule;
}

// Validation functions
const validators = {
  required: (value: any) => value !== undefined && value !== null && value !== '',
  email: (value: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value),
  phone: (value: string) => /^[\d\s\-\+\(\)]{10,}$/.test(value),
  url: (value: string) => {
    try {
      new URL(value);
      return true;
    } catch {
      return false;
    }
  },
  number: (value: any) => !isNaN(value) && value !== '',
  minLength: (value: string, length: number) => value.length >= length,
  maxLength: (value: string, length: number) => value.length <= length,
  pattern: (value: string, pattern: RegExp) => pattern.test(value),
  match: (value: any, matchValue: any) => value === matchValue,
};

export function useFormValidator(fieldConfigs: Record<string, ValidationConfig[]>) {
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const [values, setValues] = useState<Record<string, any>>({});

  const validateField = useCallback((field: string, value: any) => {
    const configs = fieldConfigs[field];
    if (!configs) return [];

    const fieldErrors: FieldError[] = [];

    configs.forEach(config => {
      let isValid = true;

      switch (config.rule) {
        case 'required':
          isValid = validators.required(value);
          break;
        case 'email':
          isValid = !value || validators.email(value);
          break;
        case 'phone':
          isValid = !value || validators.phone(value);
          break;
        case 'url':
          isValid = !value || validators.url(value);
          break;
        case 'number':
          isValid = !value || validators.number(value);
          break;
        case 'minLength':
          isValid = !value || validators.minLength(value, config.value);
          break;
        case 'maxLength':
          isValid = !value || validators.maxLength(value, config.value);
          break;
        case 'pattern':
          isValid = !value || validators.pattern(value, config.value);
          break;
        case 'match':
          isValid = !value || validators.match(value, config.value);
          break;
      }

      if (!isValid) {
        fieldErrors.push({
          field,
          message: config.message,
          rule: config.rule,
        });
      }
    });

    return fieldErrors;
  }, [fieldConfigs]);

  const validateAll = useCallback((allValues: Record<string, any>) => {
    const allErrors: FieldError[] = [];

    Object.keys(fieldConfigs).forEach(field => {
      const fieldErrors = validateField(field, allValues[field]);
      allErrors.push(...fieldErrors);
    });

    setErrors(allErrors);
    return allErrors.length === 0;
  }, [fieldConfigs, validateField]);

  const handleFieldChange = (field: string, value: any) => {
    setValues(prev => ({ ...prev, [field]: value }));

    if (touched.has(field)) {
      const fieldErrors = validateField(field, value);
      setErrors(prev => {
        const filtered = prev.filter(e => e.field !== field);
        return [...filtered, ...fieldErrors];
      });
    }
  };

  const handleFieldBlur = (field: string) => {
    setTouched(prev => new Set([...prev, field]));
    const fieldErrors = validateField(field, values[field]);
    setErrors(prev => {
      const filtered = prev.filter(e => e.field !== field);
      return [...filtered, ...fieldErrors];
    });
  };

  const getFieldErrors = (field: string) => errors.filter(e => e.field === field);
  const isFieldValid = (field: string) => getFieldErrors(field).length === 0;
  const isFieldTouched = (field: string) => touched.has(field);

  return {
    values,
    errors,
    touched,
    validateField,
    validateAll,
    handleFieldChange,
    handleFieldBlur,
    getFieldErrors,
    isFieldValid,
    isFieldTouched,
    setValues,
    setTouched,
    setErrors,
  };
}

/**
 * FormInput component with integrated validation
 */
interface FormInputProps {
  name: string;
  label: string;
  type?: string;
  value: any;
  onChange: (value: any) => void;
  onBlur?: () => void;
  error?: string;
  touched?: boolean;
  placeholder?: string;
  required?: boolean;
  disabled?: boolean;
  helperText?: string;
}

export function FormInput({
  name,
  label,
  type = 'text',
  value,
  onChange,
  onBlur,
  error,
  touched,
  placeholder,
  required,
  disabled,
  helperText,
}: FormInputProps) {
  const hasError = touched && !!error;

  return (
    <div className="mb-4">
      <label htmlFor={name} className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
        {label}
        {required && <span className="text-red-600" aria-label="required">*</span>}
      </label>
      <div className="relative">
        <input
          id={name}
          name={name}
          type={type}
          value={value || ''}
          onChange={e => onChange(e.target.value)}
          onBlur={onBlur}
          placeholder={placeholder}
          disabled={disabled}
          aria-invalid={hasError}
          aria-describedby={hasError ? `${name}-error` : helperText ? `${name}-helper` : undefined}
          className={clsx(
            'w-full px-3 py-2 rounded-lg border transition-colors focus:outline-none focus:ring-2',
            hasError
              ? 'border-red-300 bg-red-50 text-gray-900 focus:ring-red-500 dark:bg-red-900/20 dark:border-red-700'
              : 'border-gray-300 bg-white text-gray-900 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white',
            disabled && 'opacity-50 cursor-not-allowed'
          )}
        />
        {hasError && <AlertCircle className="absolute right-3 top-3 w-5 h-5 text-red-600" />}
        {touched && !error && <CheckCircle2 className="absolute right-3 top-3 w-5 h-5 text-green-600" />}
      </div>
      {hasError && (
        <p id={`${name}-error`} className="mt-1 text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
      {helperText && !hasError && (
        <p id={`${name}-helper`} className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          {helperText}
        </p>
      )}
    </div>
  );
}

/**
 * Form validation error summary
 */
interface FormErrorSummaryProps {
  errors: FieldError[];
  onErrorClick?: (field: string) => void;
}

export function FormErrorSummary({ errors, onErrorClick }: FormErrorSummaryProps) {
  if (errors.length === 0) return null;

  return (
    <div className="p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 mb-6">
      <h3 className="font-semibold text-red-900 dark:text-red-300 mb-3 flex items-center gap-2">
        <AlertCircle className="w-5 h-5" />
        Please fix the following errors:
      </h3>
      <ul className="space-y-2">
        {errors.map((error, i) => (
          <li key={i}>
            <button
              onClick={() => onErrorClick?.(error.field)}
              className="text-sm text-red-700 dark:text-red-300 hover:underline text-left"
            >
              <strong>{error.field}:</strong> {error.message}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
