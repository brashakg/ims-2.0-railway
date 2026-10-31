// ============================================================================
// IMS 2.0 - Error Message Utilities
// ============================================================================

export interface FormattedError {
  title: string;
  message: string;
  action?: string;
}

type ErrorShape = {
  status?: number;
  detail?: unknown;
  message?: unknown;
  isAxiosError?: boolean;
  response?: { status?: number; data?: { detail?: unknown; message?: unknown } };
};

function errorShape(error: unknown): ErrorShape | undefined {
  return error && typeof error === 'object' ? error as ErrorShape : undefined;
}

/** Read the shared client's ApiError, with support for raw axios adapters. */
export function getErrorStatus(error: unknown): number | undefined {
  const value = errorShape(error);
  return value?.status ?? value?.response?.status;
}

export function getErrorDetail(error: unknown): unknown {
  const value = errorShape(error);
  if (value?.detail !== undefined) return value.detail;
  // Same barrier as buildApiError: a 5xx payload is internal (a traceback, a
  // KeyError), except the deliberate 503 safe-stop. Never reconstruct one.
  const status = value?.response?.status;
  if (status !== undefined && status >= 500 && status !== 503) return undefined;
  return value?.response?.data?.detail;
}

/**
 * The request-time RBAC middleware's developer text:
 *   "Forbidden: GET /api/v1/payroll/run/rows requires one of ADMIN"
 * A store manager must never be shown an HTTP method and a URL path, so every
 * reader in this file drops text in that shape for the caller's fallback.
 */
export function isMiddlewareText(text: string): boolean {
  return /^forbidden:/i.test(text.trim()) || text.includes('/api/v1/');
}

const usable = (text: unknown): text is string =>
  typeof text === 'string' && text.trim() !== '' && !isMiddlewareText(text);

// A JS runtime fault, a browser DOMException or a raw axios error carries
// developer text ("Cannot read properties of undefined", "Network Error",
// "Request failed with status code 500"). The shared client's ApiError and a
// plain Error our own code threw on purpose (printDocuments rethrowing the
// server's refusal) carry a sentence written for staff.
const DEVELOPER_ERRORS = [TypeError, SyntaxError, RangeError, ReferenceError, EvalError, URIError];

function messageIsForStaff(value: ErrorShape): boolean {
  if (value.isAxiosError || 'response' in value) return false;
  if (typeof DOMException !== 'undefined' && value instanceof DOMException) return false;
  return !DEVELOPER_ERRORS.some((E) => value instanceof E);
}

/** One plain-text message for every screen; structured details never reach JSX. */
export function getErrorMessage(error: unknown, fallback: string): string {
  const value = errorShape(error);
  const detail = getErrorDetail(error);
  if (usable(detail)) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { loc?: unknown[]; msg?: unknown } | null;
    if (usable(first?.msg)) {
      const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1] ?? '') : '';
      return field ? `${field}: ${first.msg}` : first.msg;
    }
  }
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const payload = detail as { message?: unknown; error?: unknown };
    const message = payload.message ?? payload.error;
    if (usable(message)) return message;
  }
  if (value && messageIsForStaff(value) && usable(value.message)) return value.message;
  if (usable(error)) return error;
  return fallback;
}

/**
 * True when the API refused the request on PERMISSIONS (HTTP 403).
 *
 * Reads the ApiError status (or a raw axios adapter), rather than
 * string-matching a message. Callers use this to show an explicit
 * "you do not have access" state instead of an empty table or a generic
 * "failed to load" -- an empty payroll table reads as "no employees were paid",
 * which is a data-integrity lie, not a permissions message.
 */
export function isForbiddenError(error: unknown): boolean {
  return getErrorStatus(error) === 403;
}

/**
 * The server's plain-English explanation for a 403, when it sent a usable one.
 *
 * The payroll router writes its details for humans ("Payroll and salary data is
 * restricted to administrators. Please ask an administrator."), so showing that
 * verbatim beats inventing a second wording on the client. Middleware text
 * (isMiddlewareText) falls back to the caller's plain-English wording.
 */
export function forbiddenDetail(error: unknown, fallback: string): string {
  const detail = getErrorDetail(error);
  return usable(detail) ? detail : fallback;
}

export function formatApiError(error: unknown): FormattedError {
  if (error instanceof Error) {
    // Check for specific error patterns
    if (error.message.includes('Network error')) {
      return {
        title: 'Network Error',
        message: 'Unable to connect to the server. Please check your internet connection.',
        action: 'Retry',
      };
    }

    if (error.message.includes('401') || error.message.includes('Unauthorized')) {
      return {
        title: 'Session Expired',
        message: 'Your session has expired. Please log in again.',
        action: 'Login',
      };
    }

    if (error.message.includes('403') || error.message.includes('Forbidden')) {
      return {
        title: 'Access Denied',
        message: 'You do not have permission to access this resource.',
      };
    }

    if (error.message.includes('404') || error.message.includes('Not Found')) {
      return {
        title: 'Not Found',
        message: 'The requested resource could not be found.',
      };
    }

    if (error.message.includes('500') || error.message.includes('Server error')) {
      return {
        title: 'Server Error',
        message: 'An error occurred on the server. Please try again later.',
      };
    }

    if (error.message.includes('Invalid username or password')) {
      return {
        title: 'Login Failed',
        message: 'Invalid username or password. Please try again.',
      };
    }

    // Generic error with the actual message
    return {
      title: 'Error',
      message: error.message || 'An unexpected error occurred',
    };
  }

  if (typeof error === 'string') {
    return {
      title: 'Error',
      message: error,
    };
  }

  return {
    title: 'Unknown Error',
    message: 'An unexpected error occurred. Please try again.',
  };
}

export function isRetryableError(error: unknown): boolean {
  if (error instanceof Error) {
    return (
      error.message.includes('Network error') ||
      error.message.includes('503') ||
      error.message.includes('500') ||
      error.message.includes('timeout')
    );
  }
  return false;
}
