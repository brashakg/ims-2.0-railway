// ============================================================================
// IMS 2.0 - API Client (shared axios instance & utilities)
// ============================================================================

import axios from 'axios';
import type { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL ||
  (import.meta.env.PROD ? 'https://ims-20-railway-production.up.railway.app/api/v1' : '/api/v1');

// API URL configured from environment

// Enforce HTTPS in production - convert any HTTP URLs to HTTPS
export function getSecureApiUrl(): string {
  let url = API_BASE_URL;
  if (import.meta.env.PROD && url.startsWith('http://')) {
    url = url.replace('http://', 'https://');
  }
  return url;
}

// Retry configuration
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

// Helper function for delay
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// Check if error is retryable (only 5xx server errors and rate limiting)
// Do NOT retry network/CORS errors - they won't be fixed by retrying
const isRetryableError = (error: AxiosError): boolean => {
  // CORS errors have no response - don't retry these
  if (!error.response) {
    return false;
  }
  // Server errors (5xx) - these might be temporary
  if (error.response.status >= 500 && error.response.status < 600) {
    return true;
  }
  // Rate limiting (429) - retry with backoff
  if (error.response.status === 429) {
    return true;
  }
  // Don't retry other errors (401, 403, 404, CORS, etc.)
  return false;
};

// Create axios instance with secure URL
const api: AxiosInstance = axios.create({
  baseURL: getSecureApiUrl(),
  timeout: 10000, // 10 second timeout per request (CORS issues usually fail fast)
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor - add auth token and retry config
api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('ims_token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    // Initialize retry count
    if (config.headers) {
      config.headers['x-retry-count'] = config.headers['x-retry-count'] || '0';
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Handle final error after retries exhausted
const handleFinalError = (error: AxiosError<{ message?: string; detail?: string | Array<Record<string, unknown>> }>) => {
  if (error.response?.status === 401) {
    // Clear auth state on unauthorized
    localStorage.removeItem('ims_token');
    localStorage.removeItem('ims_user');
    window.location.href = '/login';
  }

  // Build user-friendly error message
  let message: string;

  if (!error.response) {
    // Network error
    message = 'Network error. Please check your internet connection and try again.';
  } else if (error.response.status >= 500) {
    message = 'Server error. Please try again in a moment.';
  } else {
    // Handle various API error formats (detail can be string or array)
    const rawDetail = error.response?.data?.detail;
    if (typeof rawDetail === 'string') {
      message = rawDetail;
    } else if (Array.isArray(rawDetail) && rawDetail.length > 0) {
      message = rawDetail.map((d: Record<string, unknown>) => (d.msg as string) || String(d)).join('. ');
    } else {
      message = error.response?.data?.message || error.message || 'An error occurred';
    }
  }

  return Promise.reject(new Error(message));
};

// Response interceptor - handle errors with retry logic
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<{ message?: string; detail?: string | Array<Record<string, unknown>> }>) => {
    const config = error.config;

    // Don't retry if no config or already exceeded retries
    if (!config || !config.headers) {
      return handleFinalError(error);
    }

    const retryCount = parseInt(config.headers['x-retry-count'] as string || '0', 10);

    // Check if we should retry
    if (isRetryableError(error) && retryCount < MAX_RETRIES) {
      config.headers['x-retry-count'] = String(retryCount + 1);

      // Exponential backoff: 1s, 2s, 4s
      const backoffDelay = RETRY_DELAY_MS * Math.pow(2, retryCount);
      await delay(backoffDelay);
      return api.request(config);
    }

    return handleFinalError(error);
  }
);

export default api;
