// ============================================================================
// IMS 2.0 - Login Page
// ============================================================================

import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { Eye, EyeOff, Store, AlertCircle, RefreshCw } from 'lucide-react';

export function LoginPage() {
  const { login, isLoading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');

  // Clear any stale auth data when login page mounts
  useEffect(() => {
    const token = localStorage.getItem('ims_token');
    if (token) {
      localStorage.removeItem('ims_token');
      localStorage.removeItem('ims_user');
      localStorage.removeItem('ims_active_module');
    }
  }, []);

  const handleClearCache = () => {
    if (!window.confirm('This will clear all cached data and reload the page. Continue?')) return;
    localStorage.clear();
    sessionStorage.clear();
    setError('');
    setUsername('');
    setPassword('');
    // Reload to clear any in-memory state
    window.location.reload();
  };

  const from = (location.state as { from?: { pathname: string } })?.from?.pathname || '/dashboard';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!username || !password) {
      setError('Please enter username/email and password');
      return;
    }

    try {
      // Get geolocation for staff (geo-fenced login)
      let latitude: number | undefined;
      let longitude: number | undefined;

      if (navigator.geolocation) {
        try {
          const position = await new Promise<GeolocationPosition>((resolve, reject) => {
            navigator.geolocation.getCurrentPosition(resolve, reject, {
              enableHighAccuracy: true,
              timeout: 10000,
            });
          });
          latitude = position.coords.latitude;
          longitude = position.coords.longitude;
        } catch {
          // Geolocation not available or denied - proceed anyway
        }
      }

      const response = await login({
        username,
        password,
        latitude,
        longitude,
      });

      if (response.success) {
        navigate(from, { replace: true });
      } else {
        setError(response.message || 'Login failed');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-bv-red-600 rounded-2xl mb-4">
            <Store className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">IMS 2.0</h1>
          <p className="text-gray-500 mt-1">Retail Operating System</p>
        </div>

        {/* Login Card */}
        <div className="card">
          <h2 className="text-xl font-semibold text-gray-900 mb-6">Sign In</h2>

          {/* Error message */}
          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2">
              <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Username or Email */}
            <div>
              <label htmlFor="username" className="block text-sm font-medium text-gray-700 mb-1">
                Username or Email
              </label>
              <input
                type="text"
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="input-field"
                placeholder="Enter username or email"
                autoComplete="username"
                disabled={isLoading}
              />
            </div>

            {/* Password */}
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  id="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="input-field pr-10"
                  placeholder="Enter password"
                  autoComplete="current-password"
                  disabled={isLoading}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {showPassword ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                </button>
              </div>
            </div>

            {/* Submit button */}
            <button
              type="submit"
              disabled={isLoading}
              className="btn-primary w-full py-3 flex items-center justify-center gap-2"
            >
              {isLoading ? (
                <>
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  <span>Signing in...</span>
                </>
              ) : (
                'Sign In'
              )}
            </button>
          </form>

          {/* Location notice */}
          <p className="mt-4 text-xs text-gray-500 text-center">
            Location access may be required for store staff login
          </p>
        </div>

        {/* Footer */}
        <div className="text-center mt-6 space-y-2">
          <p className="text-sm text-gray-500">
            Better Vision Opticals &amp; WizOpt
          </p>
          {/* Clear cache button for troubleshooting */}
          <button
            onClick={handleClearCache}
            className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 mx-auto"
          >
            <RefreshCw className="w-3 h-3" />
            Clear Cache
          </button>
        </div>
      </div>
    </div>
  );
}

export default LoginPage;
