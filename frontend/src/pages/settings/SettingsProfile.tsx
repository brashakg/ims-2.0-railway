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

  const persistPreferences = async (next: { email_notifications: boolean; sms_notifications: boolean }) => {
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
    persistPreferences({ email_notifications: value, sms_notifications: smsNotifications });
  };

  const toggleSmsNotifications = () => {
    const value = !smsNotifications;
    setSmsNotifications(value);
    persistPreferences({ email_notifications: emailNotifications, sms_notifications: value });
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
              <label className="block text-sm font-medium text-gray-600 mb-1">Full Name</label>
              <input
                type="text"
                value={profileData?.full_name || user?.name || ''}
                onChange={e => setProfileData(prev => prev ? { ...prev, full_name: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Email</label>
              <input
                type="email"
                value={profileData?.email || ''}
                onChange={e => setProfileData(prev => prev ? { ...prev, email: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Phone</label>
              <input
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
                try {
                  await settingsApi.updateProfile(profileData || {});
                  toast.success('Profile updated successfully');
                } catch {
                  toast.error('Failed to update profile');
                }
              }}
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
                <input
                  type="password"
                  placeholder="Current Password"
                  className="input-field"
                  autoComplete="current-password"
                  value={pwCurrent}
                  onChange={e => setPwCurrent(e.target.value)}
                  disabled={pwSubmitting}
                />
                <input
                  type="password"
                  placeholder="New Password (min 8 chars)"
                  className="input-field"
                  autoComplete="new-password"
                  value={pwNew}
                  onChange={e => setPwNew(e.target.value)}
                  disabled={pwSubmitting}
                />
                <input
                  type="password"
                  placeholder="Confirm New Password"
                  className="input-field"
                  autoComplete="new-password"
                  value={pwConfirm}
                  onChange={e => setPwConfirm(e.target.value)}
                  disabled={pwSubmitting}
                />
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
            {emailNotifications ? (
              <ToggleRight
                className={`w-8 h-8 text-green-600 ${savingPrefs ? 'opacity-50 pointer-events-none' : 'cursor-pointer'}`}
                onClick={toggleEmailNotifications}
              />
            ) : (
              <ToggleLeft
                className={`w-8 h-8 text-gray-500 ${savingPrefs ? 'opacity-50 pointer-events-none' : 'cursor-pointer'}`}
                onClick={toggleEmailNotifications}
              />
            )}
          </div>
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
            <div>
              <p className="font-medium text-gray-900">SMS Notifications</p>
              <p className="text-sm text-gray-500">Receive SMS for urgent alerts</p>
            </div>
            {smsNotifications ? (
              <ToggleRight
                className={`w-8 h-8 text-green-600 ${savingPrefs ? 'opacity-50 pointer-events-none' : 'cursor-pointer'}`}
                onClick={toggleSmsNotifications}
              />
            ) : (
              <ToggleLeft
                className={`w-8 h-8 text-gray-500 ${savingPrefs ? 'opacity-50 pointer-events-none' : 'cursor-pointer'}`}
                onClick={toggleSmsNotifications}
              />
            )}
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

  const [businessSettings, setBusinessSettings] = useState<{
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
  } | null>(null);

  // Logo upload + inline preview. The serve endpoint needs the JWT, so we
  // fetch the bytes as a blob and render an object URL (not a raw <img src>).
  const [logoPreview, setLogoPreview] = useState<string | null>(null);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadBusiness();
  }, []);

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

  const loadBusiness = async () => {
    try {
      const businessRes = await settingsApi.getBusinessSettings().catch(() => null);
      if (businessRes) {
        setBusinessSettings(businessRes);
      }
    } catch {
      // Use defaults
    }
  };

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
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || 'Failed to upload logo');
    } finally {
      setUploadingLogo(false);
    }
  };

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
              <label className="block text-sm font-medium text-gray-600 mb-1">Company Name</label>
              <input
                type="text"
                value={businessSettings?.company_name || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, company_name: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Short Name</label>
              <input
                type="text"
                value={businessSettings?.company_short_name || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, company_short_name: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-600 mb-1">Tagline</label>
              <input
                type="text"
                value={businessSettings?.tagline || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, tagline: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Support Email</label>
              <input
                type="email"
                value={businessSettings?.support_email || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, support_email: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Support Phone</label>
              <input
                type="tel"
                value={businessSettings?.support_phone || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, support_phone: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Website</label>
              <input
                type="url"
                value={businessSettings?.website || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, website: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Primary Color</label>
              <div className="flex gap-2">
                <input
                  type="color"
                  value={businessSettings?.primary_color || '#ba8659'}
                  onChange={e => setBusinessSettings(prev => prev ? { ...prev, primary_color: e.target.value } : null)}
                  className="w-12 h-10 rounded border cursor-pointer"
                />
                <input
                  type="text"
                  value={businessSettings?.primary_color || '#ba8659'}
                  onChange={e => setBusinessSettings(prev => prev ? { ...prev, primary_color: e.target.value } : null)}
                  className="input-field flex-1"
                />
              </div>
            </div>
            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-600 mb-1">Address</label>
              <textarea
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
                await settingsApi.updateBusinessSettings(businessSettings || {});
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
