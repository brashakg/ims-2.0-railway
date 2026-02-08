// ============================================================================
// IMS 2.0 - Form Input Component with Validation & Extended Features
// ============================================================================
// Standardized form component consolidating 50+ inline inputs across codebase

import React, { useState, useCallback } from 'react';
import { AlertCircle, Eye, EyeOff, Check } from 'lucide-react';
import clsx from 'clsx';
export type InputType = 'text' | 'email' | 'password' | 'number' | 'tel' | 'url' | 'search' | 'date' | 'time' | 'datetime-local';
export type InputSize = 'sm' | 'md' | 'lg';
export type ValidationRule = 'email' | 'phone' | 'gst' | 'ean13' | 'upc' | 'code128' | 'positiveNumber' | 'percentage' | 'url' | 'creditCard' | 'date' | 'required' | 'minLength' | 'maxLength' | 'pattern';

export interface FormInputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  /** Label text for the input */
  label?: string;
  /** Error message to display */
  error?: string;
  /** Hint text shown below input when no error */
  hint?: string;
  /** Whether field is required */
  required?: boolean;
  /** Helper text for additional context */
  helperText?: string;
  /** Input size preset */
  size?: InputSize;
  /** Icon component to display before input (prefix) */
  icon?: React.ComponentType<{ className?: string }>;
  /** Icon component to display after input (suffix) */
  trailingIcon?: React.ComponentType<{ className?: string }>;
  /** Validation rule to apply (automatically sets error message) */
  validationRule?: ValidationRule;
  /** Whether to show success indicator when valid */
  showSuccess?: boolean;
  /** Custom validation function */
  validate?: (value: string) => string | null;
  /** Character limit for input */
  maxChars?: number;
  /** Show character counter */
  showCharCount?: boolean;
  /** Disable input */
  disabled?: boolean;
  /** Show as read-only */
  readOnly?: boolean;
  /** Custom container className */
  containerClassName?: string;
  /** Custom label className */
  labelClassName?: string;
  /** Custom input className */
  inputClassName?: string;
}

export function FormInput({
  label,
  error,
  hint,
  required,
  helperText,
  size = 'md',
  icon: Icon,
  trailingIcon: TrailingIcon,
  validationRule,
  showSuccess = false,
  validate,
  maxChars,
  showCharCount = false,
  disabled = false,
  readOnly = false,
  containerClassName,
  labelClassName,
  inputClassName,
  type = 'text',
  id,
  value,
  onChange,
  onBlur,
  className,
  ...props
}: FormInputProps) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  const [isPasswordVisible, setIsPasswordVisible] = useState(false);
  const [hasError, setHasError] = useState(!!error);
  const [charCount, setCharCount] = useState((value as string)?.length || 0);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const newValue = e.target.value;
      setCharCount(newValue.length);

      // Apply custom validation only
      if (validate) {
        const validationError = validate(newValue);
        setHasError(!!validationError);
      }

      onChange?.(e);
    },
    [validate, onChange]
  );

  const handleBlur = useCallback(
    (e: React.FocusEvent<HTMLInputElement>) => {
      const value = e.target.value;
      if (required && !value) {
        setHasError(true);
      }
      onBlur?.(e);
    },
    [required, onBlur]
  );

  const isValid = !hasError && (value as string)?.length > 0 && !error;
  const displayError = error || (hasError && validationRule ? 'Invalid input' : '');

  // Size classes
  const sizeClasses = {
    sm: 'px-2 py-1 text-xs',
    md: 'px-3 py-2 text-sm',
    lg: 'px-4 py-3 text-base',
  };

  const baseInputClasses = clsx(
    'w-full border rounded-lg transition-colors',
    'dark:border-gray-700 dark:bg-gray-800 dark:text-white dark:placeholder-gray-500',
    'focus:outline-none focus:ring-2 focus:ring-offset-0',
    sizeClasses[size],
    disabled && 'opacity-50 cursor-not-allowed',
    readOnly && 'bg-gray-50 dark:bg-gray-900/50 cursor-default',
    displayError
      ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 text-gray-900 dark:text-white placeholder-red-300 dark:placeholder-red-500 focus:ring-red-500 dark:focus:ring-red-600'
      : isValid && showSuccess
        ? 'border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-900/20 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 focus:ring-green-500 dark:focus:ring-green-600'
        : 'border-gray-300 bg-white text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 focus:ring-blue-500 dark:focus:ring-blue-600'
  );

  return (
    <div className={clsx('space-y-2', containerClassName)}>
      {label && (
        <label
          htmlFor={inputId}
          className={clsx(
            'block text-sm font-medium text-gray-700 dark:text-gray-300',
            labelClassName
          )}
        >
          {label}
          {required && <span className="text-red-500 dark:text-red-400 ml-1" aria-label="required">*</span>}
        </label>
      )}

      <div className="relative group">
        {Icon && (
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500 pointer-events-none">
            <Icon className="w-4 h-4" />
          </div>
        )}

        <input
          id={inputId}
          type={type === 'password' && isPasswordVisible ? 'text' : type}
          disabled={disabled}
          readOnly={readOnly}
          maxLength={maxChars}
          value={value}
          onChange={handleChange}
          onBlur={handleBlur}
          className={clsx(
            baseInputClasses,
            Icon && 'pl-9',
            (TrailingIcon || type === 'password' || isValid) && 'pr-9',
            inputClassName,
            className
          )}
          aria-label={label}
          aria-required={required}
          aria-invalid={!!displayError}
          aria-describedby={displayError ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined}
          {...props}
        />

        {/* Suffix Icon / Actions */}
        <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-1">
          {type === 'password' && (value as string)?.length > 0 && (
            <button
              type="button"
              onClick={() => setIsPasswordVisible(!isPasswordVisible)}
              className="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 transition-colors"
              aria-label={isPasswordVisible ? 'Hide password' : 'Show password'}
              aria-pressed={isPasswordVisible}
            >
              {isPasswordVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          )}
          {isValid && showSuccess && !type.includes('password') && (
            <Check className="w-4 h-4 text-green-600 dark:text-green-400" />
          )}
          {TrailingIcon && type !== 'password' && (
            <TrailingIcon className="w-4 h-4 text-gray-400 dark:text-gray-500 pointer-events-none" />
          )}
        </div>
      </div>

      {/* Helper Text / Error / Hint */}
      <div className="min-h-[1rem] text-xs space-y-0.5">
        {displayError && (
          <div
            id={`${inputId}-error`}
            className="flex items-center gap-1 text-red-600 dark:text-red-400"
            role="alert"
            aria-live="polite"
          >
            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
            <span>{displayError}</span>
          </div>
        )}
        {!displayError && hint && (
          <p id={`${inputId}-hint`} className="text-gray-500 dark:text-gray-400">
            {hint}
          </p>
        )}
        {helperText && !displayError && (
          <p className="text-gray-600 dark:text-gray-400">{helperText}</p>
        )}
      </div>

      {/* Character Counter */}
      {showCharCount && maxChars && (
        <div className="flex justify-end text-xs text-gray-500 dark:text-gray-400">
          {charCount} / {maxChars}
        </div>
      )}
    </div>
  );
}

/**
 * Select Input - for dropdown selections
 * Consolidates <select> elements scattered throughout codebase
 */
export interface FormSelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  label?: string;
  error?: string;
  hint?: string;
  required?: boolean;
  options: Array<{ value: string; label: string; disabled?: boolean }>;
  size?: InputSize;
  icon?: React.ComponentType<{ className?: string }>;
  disabled?: boolean;
  containerClassName?: string;
  labelClassName?: string;
  selectClassName?: string;
}

export function FormSelect({
  label,
  error,
  hint,
  required,
  options,
  size = 'md',
  icon: Icon,
  disabled = false,
  containerClassName,
  labelClassName,
  selectClassName,
  id,
  className,
  ...props
}: FormSelectProps) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');

  const sizeClasses = {
    sm: 'px-2 py-1 text-xs',
    md: 'px-3 py-2 text-sm',
    lg: 'px-4 py-3 text-base',
  };

  return (
    <div className={clsx('space-y-2', containerClassName)}>
      {label && (
        <label htmlFor={inputId} className={clsx('block text-sm font-medium text-gray-700 dark:text-gray-300', labelClassName)}>
          {label}
          {required && <span className="text-red-500 dark:text-red-400 ml-1">*</span>}
        </label>
      )}

      <div className="relative">
        {Icon && <div className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500 pointer-events-none">
          <Icon className="w-4 h-4" />
        </div>}

        <select
          id={inputId}
          disabled={disabled}
          className={clsx(
            'w-full border rounded-lg transition-colors appearance-none',
            'dark:border-gray-700 dark:bg-gray-800 dark:text-white',
            'focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-600',
            sizeClasses[size],
            error ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 text-red-900 dark:text-red-100' : 'border-gray-300 bg-white dark:bg-gray-800 text-gray-900 dark:text-white',
            Icon && 'pl-9',
            'pr-9',
            selectClassName,
            className
          )}
          aria-label={label}
          aria-required={required}
          aria-invalid={!!error}
          {...props}
        >
          <option value="">Select {label?.toLowerCase()}</option>
          {options.map(opt => (
            <option key={opt.value} value={opt.value} disabled={opt.disabled}>
              {opt.label}
            </option>
          ))}
        </select>

        <div className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500 pointer-events-none">
          <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
          </svg>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-1 text-sm text-red-600 dark:text-red-400">
          <AlertCircle className="w-4 h-4" />
          <span>{error}</span>
        </div>
      )}
      {!error && hint && <p className="text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
    </div>
  );
}

/**
 * Textarea Input - for multi-line text
 * Consolidates <textarea> elements scattered throughout codebase
 */
export interface FormTextareaProps extends Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'size'> {
  label?: string;
  error?: string;
  hint?: string;
  required?: boolean;
  size?: 'sm' | 'md' | 'lg';
  showCharCount?: boolean;
  containerClassName?: string;
  labelClassName?: string;
  textareaClassName?: string;
}

export function FormTextarea({
  label,
  error,
  hint,
  required,
  size = 'md',
  showCharCount = false,
  containerClassName,
  labelClassName,
  textareaClassName,
  id,
  value,
  onChange,
  maxLength,
  className,
  ...props
}: FormTextareaProps) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  const [charCount, setCharCount] = useState((value as string)?.length || 0);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setCharCount(e.target.value.length);
    onChange?.(e);
  };

  const rows = { sm: 3, md: 4, lg: 6 }[size];

  return (
    <div className={clsx('space-y-2', containerClassName)}>
      {label && (
        <label htmlFor={inputId} className={clsx('block text-sm font-medium text-gray-700 dark:text-gray-300', labelClassName)}>
          {label}
          {required && <span className="text-red-500 dark:text-red-400 ml-1">*</span>}
        </label>
      )}

      <textarea
        id={inputId}
        rows={rows}
        maxLength={maxLength}
        value={value}
        onChange={handleChange}
        className={clsx(
          'w-full px-3 py-2 border rounded-lg transition-colors resize-none',
          'dark:border-gray-700 dark:bg-gray-800 dark:text-white dark:placeholder-gray-500',
          'focus:outline-none focus:ring-2',
          error
            ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 text-red-900 dark:text-red-100 focus:ring-red-500 dark:focus:ring-red-600'
            : 'border-gray-300 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-blue-500 dark:focus:ring-blue-600',
          textareaClassName,
          className
        )}
        aria-label={label}
        aria-required={required}
        aria-invalid={!!error}
        {...props}
      />

      {error && (
        <div className="flex items-center gap-1 text-sm text-red-600 dark:text-red-400">
          <AlertCircle className="w-4 h-4" />
          <span>{error}</span>
        </div>
      )}
      {!error && hint && <p className="text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
      {showCharCount && maxLength && (
        <div className="flex justify-end text-xs text-gray-500 dark:text-gray-400">
          {charCount} / {maxLength}
        </div>
      )}
    </div>
  );
}

export default FormInput;
