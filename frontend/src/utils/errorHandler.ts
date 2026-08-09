// ============================================================================
// IMS 2.0 - Error Message Utilities
// ============================================================================

export interface FormattedError {
  title: string;
  message: string;
  action?: string;
}

/**
 * True when the API refused the request on PERMISSIONS (HTTP 403).
 *
 * Reads the axios error shape the pages actually receive, rather than
 * string-matching a message. Callers use this to show an explicit
 * "you do not have access" state instead of an empty table or a generic
 * "failed to load" -- an empty payroll table reads as "no employees were paid",
 * which is a data-integrity lie, not a permissions message.
 */
export function isForbiddenError(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 403;
}

/**
 * The server's plain-English explanation for a 403, when it sent a usable one.
 *
 * The payroll router writes its details for humans ("Payroll and salary data is
 * restricted to administrators. Please ask an administrator."), so showing that
 * verbatim beats inventing a second wording on the client.
 *
 * BUT the request-time RBAC middleware answers first when a role is denied by
 * the policy table, and its body is developer text:
 *   "Forbidden: GET /api/v1/payroll/run/rows requires one of ADMIN"
 * A store manager must never be shown an HTTP method and a URL path. Details in
 * that shape are dropped in favour of the caller's plain-English fallback.
 */
export function forbiddenDetail(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data
    ?.detail;
  if (typeof detail !== 'string' || !detail.trim()) return fallback;
  const looksLikeMiddlewareText = /^forbidden:/i.test(detail.trim()) || detail.includes('/api/v1/');
  return looksLikeMiddlewareText ? fallback : detail;
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

export function getErrorDescription(status: number): string {
  const descriptions: Record<number, string> = {
    400: 'Bad request. Please check your input.',
    401: 'Session expired. Please log in again.',
    403: 'You do not have permission to access this resource.',
    404: 'The requested resource was not found.',
    429: 'Too many requests. Please wait and try again.',
    500: 'Server error. Please try again later.',
    503: 'Service unavailable. The server is temporarily down.',
  };

  return descriptions[status] || 'An error occurred. Please try again.';
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
