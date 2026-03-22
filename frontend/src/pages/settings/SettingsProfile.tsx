// ============================================================================
// IMS 2.0 - Settings: Profile & Business Settings
// ============================================================================

import { useState, useEffect } from 'react';
import {
  User, Building2, Save, Lock,
  ToggleLeft, ToggleRight,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api';

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
  const [showChangePassword, setShowChangePassword] = useState(false);

  useEffect(() => {
    loadProfile();
  }, []);

  const loadProfile = async () => {
    try {
      const [profileRes] = await Promise.all([
        settingsApi.getProfile().catch(() => null),
        settingsApi.getPreferences().catch(() => ({})),
      ]);
      if (profileRes) {
        setProfileData({
          full_name: user?.name || profileRes.full_name || '',
          email: profileRes.email || '',
          phone: profileRes.phone || '',
        });
      }
    } catch {
      // Use defaults
    }
  };

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-white mb-4">My Profile</h2>
        <div className="space-y-4">
          <div className="flex items-center gap-4 p-4 bg-gray-900 rounded-lg">
            <div className="w-16 h-16 rounded-full bg-bv-gold-100 flex items-center justify-center">
              <User className="w-8 h-8 text-bv-gold-600" />
            </div>
            <div>
              <h3 className="font-semibold text-white">{user?.name || 'User'}</h3>
              <p className="text-sm text-gray-400">@{user?.email?.split('@')[0]}</p>
              <div className="flex gap-2 mt-1">
                {user?.roles?.map(role => (
                  <span key={role} className="text-xs bg-bv-gold-100 text-bv-gold-700 px-2 py-0.5 rounded">
                    {role.replace('_', ' ')}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Full Name</label>
              <input
                type="text"
                value={profileData?.full_name || user?.name || ''}
                onChange={e => setProfileData(prev => prev ? { ...prev, full_name: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Email</label>
              <input
                type="email"
                value={profileData?.email || ''}
                onChange={e => setProfileData(prev => prev ? { ...prev, email: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Phone</label>
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
              <h4 className="font-medium text-white mb-3">Change Password</h4>
              <div className="space-y-3">
                <input type="password" placeholder="Current Password" className="input-field" />
                <input type="password" placeholder="New Password (min 8 chars)" className="input-field" />
                <input type="password" placeholder="Confirm New Password" className="input-field" />
                <button className="btn-primary">Update Password</button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold text-white mb-4">Preferences</h2>
        <div className="space-y-4">
          {/* Theme — light mode only */}
          <div className="flex items-center justify-between p-3 bg-gray-900 rounded-lg">
            <div>
              <p className="font-medium text-white">Email Notifications</p>
              <p className="text-sm text-gray-400">Receive email alerts for important updates</p>
            </div>
            <ToggleRight className="w-8 h-8 text-green-600 cursor-pointer" />
          </div>
          <div className="flex items-center justify-between p-3 bg-gray-900 rounded-lg">
            <div>
              <p className="font-medium text-white">SMS Notifications</p>
              <p className="text-sm text-gray-400">Receive SMS for urgent alerts</p>
            </div>
            <ToggleLeft className="w-8 h-8 text-gray-400 cursor-pointer" />
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

  useEffect(() => {
    loadBusiness();
  }, []);

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

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-white mb-4">Company Profile</h2>
        <div className="space-y-4">
          <div className="flex items-center gap-4 p-4 bg-gray-900 rounded-lg">
            <div className="w-20 h-20 rounded-lg bg-gray-800 border-2 border-dashed border-gray-300 flex items-center justify-center cursor-pointer hover:border-bv-gold-500">
              <Building2 className="w-8 h-8 text-gray-400" />
            </div>
            <div>
              <p className="text-sm text-gray-400">Company Logo</p>
              <button className="text-sm text-bv-gold-600 hover:underline">Upload new logo</button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Company Name</label>
              <input
                type="text"
                value={businessSettings?.company_name || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, company_name: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Short Name</label>
              <input
                type="text"
                value={businessSettings?.company_short_name || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, company_short_name: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-300 mb-1">Tagline</label>
              <input
                type="text"
                value={businessSettings?.tagline || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, tagline: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Support Email</label>
              <input
                type="email"
                value={businessSettings?.support_email || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, support_email: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Support Phone</label>
              <input
                type="tel"
                value={businessSettings?.support_phone || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, support_phone: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Website</label>
              <input
                type="url"
                value={businessSettings?.website || ''}
                onChange={e => setBusinessSettings(prev => prev ? { ...prev, website: e.target.value } : null)}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Primary Color</label>
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
              <label className="block text-sm font-medium text-gray-300 mb-1">Address</label>
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
