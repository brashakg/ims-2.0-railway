// ============================================================================
// IMS 2.0 - Session Expiry Hook
// ============================================================================

import { useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';

const SESSION_WARNING_THRESHOLD = 5 * 60 * 1000; // 5 minutes
const CHECK_INTERVAL = 10 * 1000; // Check every 10 seconds

export function useSessionExpiry() {
  const { logout } = useAuth();
  const [showWarning, setShowWarning] = useState(false);
  const [timeRemaining, setTimeRemaining] = useState<number | null>(null);

  useEffect(() => {
    let intervalId: ReturnType<typeof setInterval> | null = null;

    const checkExpiry = () => {
      const token = localStorage.getItem('ims_token');
      const loginTime = localStorage.getItem('ims_login_time');

      if (!token || !loginTime) {
        return;
      }

      // Token expires 480 minutes (8 hours) after login
      const expiryTime = parseInt(loginTime) + 480 * 60 * 1000;
      const now = Date.now();
      const remaining = expiryTime - now;

      if (remaining <= 0) {
        // Token has expired
        logout();
        return;
      }

      setTimeRemaining(remaining);

      // Show warning if less than 5 minutes remaining
      if (remaining <= SESSION_WARNING_THRESHOLD) {
        setShowWarning(true);
      } else {
        setShowWarning(false);
      }
    };

    // Check immediately and then periodically
    checkExpiry();
    intervalId = setInterval(checkExpiry, CHECK_INTERVAL);

    return () => {
      if (intervalId) clearInterval(intervalId);
    };
  }, [logout]);

  const extendSession = () => {
    // Save current login time to extend the session
    localStorage.setItem('ims_login_time', Date.now().toString());
    setShowWarning(false);
  };

  return { showWarning, timeRemaining, extendSession };
}

export default useSessionExpiry;
