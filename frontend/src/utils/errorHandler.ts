// ============================================================================
// IMS 2.0 - Error Message Utilities
// ============================================================================

export interface FormattedError {
  title: string;
  message: string;
  action?: string;
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
