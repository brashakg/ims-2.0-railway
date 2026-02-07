// ============================================================================
// IMS 2.0 - Error Boundary Component
// ============================================================================

import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error('Error caught by boundary:', error);
  }

  resetError = () => {
    this.setState({ hasError: false, error: null });
  };

  handleRefresh = () => {
    this.resetError();
    window.location.href = '/dashboard';
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-gray-50">
          <div className="text-center max-w-md" role="alert" aria-live="assertive">
            <h1 className="text-3xl font-bold text-gray-900 mb-4">Something went wrong</h1>
            <p className="text-gray-600 mb-6">
              The application encountered an error. Try refreshing the page or go back to the dashboard.
            </p>
            {import.meta.env.DEV && this.state.error && (
              <p className="text-xs text-red-600 mb-4 font-mono break-words">
                Error: {this.state.error.message}
              </p>
            )}
            <div className="space-y-3">
              <button
                onClick={this.handleRefresh}
                className="btn-primary w-full"
                aria-label="Navigate to dashboard"
              >
                Go to Dashboard
              </button>
              <button
                onClick={() => this.resetError()}
                className="btn-secondary w-full"
                aria-label="Retry the failed operation"
              >
                Try Again
              </button>
            </div>
            {import.meta.env.DEV && this.state.error && (
              <details className="mt-6 text-left">
                <summary className="cursor-pointer text-sm text-gray-500 hover:text-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-bv-red-600 rounded px-1">
                  Error Details (Dev Only)
                </summary>
                <pre className="mt-2 p-3 bg-gray-100 rounded text-xs overflow-auto text-red-600">
                  {this.state.error.toString()}
                </pre>
              </details>
            )}
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
