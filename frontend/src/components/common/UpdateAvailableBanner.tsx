// ============================================================================
// IMS 2.0 - "A new version is available" refresh banner
// ============================================================================
// A small, non-intrusive, light-theme bar that appears at the top of the app
// shell when a newer frontend build has been deployed than the one running in
// this tab (see useUpdateAvailable). The user can Refresh (reload to the new
// build) or dismiss it. Once dismissed it stays hidden for the rest of the
// session and won't nag again until another, even-newer build appears.
//
// Fail-soft by construction: the underlying hook swallows all errors, so a
// network blip never surfaces a banner and never throws.

import { useState } from 'react';
import { RefreshCw, X } from 'lucide-react';
import { useUpdateAvailable } from '../../hooks/useUpdateAvailable';

export function UpdateAvailableBanner() {
  const { updateAvailable } = useUpdateAvailable();
  const [dismissed, setDismissed] = useState(false);

  if (!updateAvailable || dismissed) return null;

  const reload = () => {
    try {
      window.location.reload();
    } catch {
      /* never let a reload error wedge the UI */
    }
  };

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        background: 'var(--info-50, #e5eef9)',
        borderBottom: '1px solid var(--info, #1e5eb3)',
        padding: '8px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        fontSize: 13,
        color: 'var(--info, #1e5eb3)',
        position: 'sticky',
        top: 52,
        zIndex: 60,
      }}
    >
      <RefreshCw style={{ width: 16, height: 16, flexShrink: 0 }} />
      <span style={{ fontWeight: 600 }}>A new version is available.</span>
      <button
        type="button"
        onClick={reload}
        style={{
          marginLeft: 4,
          padding: '3px 12px',
          fontSize: 12.5,
          fontWeight: 600,
          color: '#ffffff',
          background: 'var(--info, #1e5eb3)',
          border: 'none',
          borderRadius: 6,
          cursor: 'pointer',
        }}
      >
        Refresh
      </button>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss update notice"
        title="Dismiss"
        style={{
          marginLeft: 'auto',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 24,
          height: 24,
          color: 'var(--info, #1e5eb3)',
          background: 'transparent',
          border: 'none',
          borderRadius: 6,
          cursor: 'pointer',
        }}
      >
        <X style={{ width: 16, height: 16 }} />
      </button>
    </div>
  );
}

export default UpdateAvailableBanner;
