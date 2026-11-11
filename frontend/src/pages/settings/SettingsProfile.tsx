// ============================================================================
// IMS 2.0 - Settings: Profile & Business Settings
// ============================================================================

import { useState, useEffect, useRef } from 'react';
import {
  User, Building2, Save, Lock,
  ToggleLeft, ToggleRight, Upload, Loader2,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api';
import { authApi } from '../../services/api/auth';
import { getErrorMessage } from '../../utils/errorHandler';
import { useLoadedSettings, settingsLoadErrorText } from '../../hooks/useLoadedSettings';
import { SettingsLoadError } from '../../components/settings/SettingsLoadError';

// A real switch (keyboard + screen reader), not a clickable icon (L1-F09).
function PrefSwitch({ label, on, busy, onToggle }: { label: string; on: boolean; busy: boolean; onToggle: () => void }) {
  const Icon = on ? ToggleRight : ToggleLeft;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={busy}
      onClick={onToggle}
      className="shrink-0 rounded disabled:opacity-50"
    >
      <Icon className={`w-8 h-8 ${on ? 'text-green-600' : 'text-gray-500'}`} />
    </button>
  );
}

// ============================================================================
// Profile Tab
// ============================================================================

export function ProfileSection() {
  const { user } = useAuth();
  const toast = useToast();

  const [profileData, setProfileData] = useState<{
    full_name: string;
    email: string;
    phone: string;
  } | null>(null);
  // Notification preferences seeded from getPreferences(); persisted via
  // settingsApi.updatePreferences. Previously the getPreferences() result was
  // ignored and the toggles had no onChange (dead controls).
  const [emailNotifications, setEmailNotifications] = useState(true);
  const [smsNotifications, setSmsNotifications] = useState(true);
  const [savingPrefs, setSavingPrefs] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [pwCurrent, setPwCurrent] = useState('');
  const [pwNew, setPwNew] = useState('');
  const [pwConfirm, setPwConfirm] = useState('');
  const [pwSubmitting, setPwSubmitting] = useState(false);

  const handleChangePassword = async () => {
    if (!pwCurrent || !pwNew || !pwConfirm) {
      toast.error('Please fill in all password fields');
      return;
    }
    if (pwNew.length < 8) {
      toast.error('New password must be at least 8 characters');
      return;
    }
    if (pwNew !== pwConfirm) {
      toast.error('New password and confirmation do not match');
      return;
    }
    setPwSubmitting(true);
    try {
      const res = await authApi.changePassword(pwCurrent, pwNew);
      if (res && res.success === false) {
        toast.error(res.message || 'Failed to change password');
        return;
      }
      toast.success('Password changed successfully');
      setPwCurrent('');
      setPwNew('');
      setPwConfirm('');
      setShowChangePassword(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to change password';
      toast.error(msg.includes('incorrect') ? 'Current password is incorrect' : msg);
    } finally {
      setPwSubmitting(false);
    }
  };

  useEffect(() => {
    loadProfile();
  }, []);

  const loadProfile = async () => {
    try {
      const [profileRes, prefsRes] = await Promise.all([
        settingsApi.getProfile().catch(() => null),
        settingsApi.getPreferences().catch(() => null),
      ]);
      if (profileRes) {
        setProfileData({
          full_name: user?.name || profileRes.full_name || '',
          email: profileRes.email || '',
          phone: profileRes.phone || '',
        });
      }
      if (prefsRes) {
        setEmailNotifications(prefsRes.email_notifications ?? true);
        setSmsNotifications(prefsRes.sms_notifications ?? true);
      }
    } catch {
      // Use defaults
    }
  };

  // Only the switch that was pressed is sent (the server merges it), so a
  // switch whose stored value never loaded is not written back as a default.
  const persistPreferences = async (next: { email_notifications?: boolean; sms_notifications?: boolean }) => {
    setSavingPrefs(true);
    try {
      await settingsApi.updatePreferences(next);
      toast.success('Preferences saved');
    } catch {
      toast.error('Failed to save preferences');
    } finally {
      setSavingPrefs(false);
    }
  };

  const toggleEmailNotifications = () => {
    const value = !emailNotifications;
    setEmailNotifications(value);
    persistPreferences({ email_notifications: value });
  };

  const toggleSmsNotifications = () => {
    const value = !smsNotifications;
    setSmsNotifications(value);
    persistPreferences({ sms_notifications: value });
  };

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">My Profile</h2>
        <div className="space-y-4">
          <div className="flex items-center gap-4 p-4 bg-gray-50 rounded-lg">
            <div className="w-16 h-16 rounded-full bg-bv-red-100 flex items-center justify-center">
              <User className="w-8 h-8 text-bv-red-600" />
            </div>
            <div>
              <h3 className="font-semibold text-gray-900">{user?.name || 'User'}</h3>
              <p className="text-sm text-gray-500">@{user?.email?.split('@')[0]}</p>
              <div className="flex gap-2 mt-1">
                {user?.roles?.map(role => (
                  <span key={role} className="text-xs bg-bv-red-100 text-bv-red-700 px-2 py-0.5 rounded">
                    {role.replace('_', ' ')}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label htmlFor="profile-full-name" className="block text-sm font-medium text-gray-600 mb-1">Full Name</label>
              <input
                id="profile-full-name"
                type="text"
                value={profileData?.full_name || user?.name || ''}
                onChange={e => setProfileData(prev => prev ? { ...prev, full_name: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="profile-email" className="block text-sm font-medium text-gray-600 mb-1">Email</label>
              <input
                id="profile-email"
                type="email"
                value={profileData?.email || ''}
                onChange={e => setProfileData(prev => prev ? { ...prev, email: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="profile-phone" className="block text-sm font-medium text-gray-600 mb-1">Phone</label>
              <input
                id="profile-phone"
                type="tel"
                value={profileData?.phone || ''}
                onChange={e => setProfileData(prev => prev ? { ...prev, phone: e.target.value } : null)}
                className="input-field"
              />
            </div>
          </div>

          <div className="flex gap-3">
            <button
              onClick={async () => {
                if (!profileData) return; // nothing loaded, nothing to save
                try {
                  await settingsApi.updateProfile(profileData);
                  toast.success('Profile updated successfully');
                } catch {
                  toast.error('Failed to update profile');
                }
              }}
              disabled={!profileData}
              className="btn-primary"
            >
              <Save className="w-4 h-4 mr-2" />
              Save Profile
            </button>
            <button
              onClick={() => setShowChangePassword(!showChangePassword)}
              className="btn-outline"
            >
              <Lock className="w-4 h-4 mr-2" />
              Change Password
            </button>
          </div>

          {showChangePassword && (
            <div className="p-4 bg-yellow-50 rounded-lg border border-yellow-200">
              <h4 className="font-medium text-gray-900 mb-3">Change Password</h4>
              <div className="space-y-3">
                <div>
                  <label htmlFor="current-password" className="block text-sm font-medium text-gray-600 mb-1">Current Password</label>
                  <input
                    id="current-password"
                    type="password"
                    className="input-field"
                    autoComplete="current-password"
                    value={pwCurrent}
                    onChange={e => setPwCurrent(e.target.value)}
                    disabled={pwSubmitting}
                  />
                </div>
                <div>
                  <label htmlFor="new-password" className="block text-sm font-medium text-gray-600 mb-1">New Password (min 8 chars)</label>
                  <input
                    id="new-password"
                    type="password"
                    className="input-field"
                    autoComplete="new-password"
                    value={pwNew}
                    onChange={e => setPwNew(e.target.value)}
                    disabled={pwSubmitting}
                  />
                </div>
                <div>
                  <label htmlFor="confirm-password" className="block text-sm font-medium text-gray-600 mb-1">Confirm New Password</label>
                  <input
                    id="confirm-password"
                    type="password"
                    className="input-field"
                    autoComplete="new-password"
                    value={pwConfirm}
                    onChange={e => setPwConfirm(e.target.value)}
                    disabled={pwSubmitting}
                  />
                </div>
                <button
                  className="btn-primary"
                  onClick={handleChangePassword}
                  disabled={pwSubmitting}
                >
                  {pwSubmitting ? 'Updating...' : 'Update Password'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Preferences</h2>
        <div className="space-y-4">
          {/* Theme — light mode only */}
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
            <div>
              <p className="font-medium text-gray-900">Email Notifications</p>
              <p className="text-sm text-gray-500">Receive email alerts for important updates</p>
            </div>
            <PrefSwitch label="Email Notifications" on={emailNotifications} busy={savingPrefs} onToggle={toggleEmailNotifications} />
          </div>
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
            <div>
              <p className="font-medium text-gray-900">SMS Notifications</p>
              <p className="text-sm text-gray-500">Receive SMS for urgent alerts</p>
            </div>
            <PrefSwitch label="SMS Notifications" on={smsNotifications} busy={savingPrefs} onToggle={toggleSmsNotifications} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Business Profile Tab
// ============================================================================

export function BusinessSection() {
  const toast = useToast();

  // The stored profile must be read before it can be edited or saved (the
  // shared settings rule, audit L1-F05): a failed read used to leave a blank
  // form whose Save sent {} over the saved company profile.
  const { data: businessSettings, status: loadStatus, reload: loadBusiness, setData: setBusinessSettings } =
    useLoadedSettings<{
      company_name: string;
      company_short_name: string;
      tagline: string;
      logo_url: string;
      primary_color: string;
      secondary_color: string;
      support_email: string;
      support_phone: string;
      website: string;
      address: string;
    }>(async () => {
      const res = await settingsApi.getBusinessSettings();
      if (!res) throw new Error('empty settings answer');
      return res;
    });

  // Logo upload + inline preview. The serve endpoint needs the JWT, so we
  // fetch the bytes as a blob and render an object URL (not a raw <img src>).
  const [logoPreview, setLogoPreview] = useState<string | null>(null);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Whenever logo_url changes, (re)load the preview blob. Revoke the old
  // object URL on cleanup to avoid leaking blobs.
  useEffect(() => {
    const url = businessSettings?.logo_url;
    if (!url) {
      setLogoPreview(null);
      return;
    }
    let revoked: string | null = null;
    let active = true;
    settingsApi
      .getLogoObjectUrl(url)
      .then((objUrl) => {
        if (active) {
          revoked = objUrl;
          setLogoPreview(objUrl);
        } else {
          URL.revokeObjectURL(objUrl);
        }
      })
      .catch(() => {
        if (active) setLogoPreview(null);
      });
    return () => {
      active = false;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [businessSettings?.logo_url]);


  const handleLogoSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset the input so re-selecting the same file fires onChange again.
    if (fileInputRef.current) fileInputRef.current.value = '';
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      toast.error('Please choose an image file (PNG, JPG, SVG, WebP)');
      return;
    }
    setUploadingLogo(true);
    try {
      const res = await settingsApi.uploadLogo(file);
      // Persist the new logo_url onto the business settings doc immediately
      // so it survives a reload (the backend also best-effort persists it).
      const next = { ...(businessSettings || {}), logo_url: res.logo_url } as NonNullable<typeof businessSettings>;
      setBusinessSettings(next);
      try {
        await settingsApi.updateBusinessSettings({ logo_url: res.logo_url });
      } catch {
        // Non-fatal: the upload already persisted server-side; the explicit
        // "Save Settings" button can still re-persist the rest of the form.
      }
      toast.success('Logo uploaded');
    } catch (err) {
      const detail = getErrorMessage(err, '');
      toast.error(detail || 'Failed to upload logo');
    } finally {
      setUploadingLogo(false);
    }
  };

  if (loadStatus === 'loading') {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        <span className="text-sm">Loading company profile…</span>
      </div>
    );
  }
  if (loadStatus === 'error' || !businessSettings) {
    return <SettingsLoadError message={settingsLoadErrorText('company profile')} onRetry={() => void loadBusiness()} />;
  }

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Company Profile</h2>
        <div className="space-y-4">
          <div className="flex items-center gap-4 p-4 bg-gray-50 rounded-lg">
            <div className="w-20 h-20 rounded-lg bg-white border-2 border-dashed border-gray-300 flex items-center justify-center overflow-hidden">
              {logoPreview ? (
                <img
                  src={logoPreview}
                  alt="Company logo"
                  className="w-full h-full object-contain"
                />
              ) : (
                <Building2 className="w-8 h-8 text-gray-500" />
              )}
            </div>
            <div>
              <p className="text-sm text-gray-500 mb-1">Company Logo</p>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/svg+xml,image/gif"
                onChange={handleLogoSelected}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadingLogo}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-bv-red-600 hover:text-bv-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {uploadingLogo ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Upload className="w-4 h-4" />
                )}
                {logoPreview ? 'Replace logo' : 'Upload new logo'}
              </button>
              <p className="text-xs text-gray-400 mt-1">PNG, JPG, SVG or WebP, up to 5 MB</p>
            </div>
          </div>

          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label htmlFor="profile-company-name" className="block text-sm font-medium text-gray-600 mb-1">Company Name</label>
              <input
                id="profile-company-name"
                type="text"
                value={businessSettings?.company_name || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, company_name: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="profile-short-name" className="block text-sm font-medium text-gray-600 mb-1">Short Name</label>
              <input
                id="profile-short-name"
                type="text"
                value={businessSettings?.company_short_name || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, company_short_name: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div className="col-span-2">
              <label htmlFor="profile-tagline" className="block text-sm font-medium text-gray-600 mb-1">Tagline</label>
              <input
                id="profile-tagline"
                type="text"
                value={businessSettings?.tagline || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, tagline: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="profile-support-email" className="block text-sm font-medium text-gray-600 mb-1">Support Email</label>
              <input
                id="profile-support-email"
                type="email"
                value={businessSettings?.support_email || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, support_email: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="profile-support-phone" className="block text-sm font-medium text-gray-600 mb-1">Support Phone</label>
              <input
                id="profile-support-phone"
                type="tel"
                value={businessSettings?.support_phone || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, support_phone: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="profile-website" className="block text-sm font-medium text-gray-600 mb-1">Website</label>
              <input
                id="profile-website"
                type="url"
                value={businessSettings?.website || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, website: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="profile-primary-color" className="block text-sm font-medium text-gray-600 mb-1">Primary Color</label>
              <div className="flex gap-2">
                <input
                  type="color"
                  aria-label="Primary Color picker"
                  value={businessSettings?.primary_color || '#ba8659'}
                  onChange={e => setBusinessSettings(prev => prev ? { ...prev, primary_color: e.target.value } : null)}
                  className="w-12 h-10 rounded border cursor-pointer"
                />
                <input
                  id="profile-primary-color"
                  type="text"
                  value={businessSettings?.primary_color || '#ba8659'}
                  onChange={e => setBusinessSettings(prev => prev ? { ...prev, primary_color: e.target.value } : null)}
                  className="input-field flex-1"
                />
              </div>
            </div>
            <div className="col-span-2">
              <label htmlFor="profile-address" className="block text-sm font-medium text-gray-600 mb-1">Address</label>
              <textarea
                id="profile-address"
                value={businessSettings?.address || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, address: e.target.value } : null)}
                rows={2}
                className="input-field"
              />
            </div>
          </div>

          <button
            onClick={async () => {
              try {
                await settingsApi.updateBusinessSettings(businessSettings);
                toast.success('Business settings saved');
              } catch {
                toast.error('Failed to save settings');
              }
            }}
            className="btn-primary"
          >
            <Save className="w-4 h-4 mr-2" />
            Save Settings
          </button>
        </div>
      </div>
    </div>
  );
}
