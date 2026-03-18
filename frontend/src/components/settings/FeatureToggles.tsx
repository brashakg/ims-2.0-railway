// ============================================================================
// IMS 2.0 - Feature Toggles Settings (Superadmin Only)
// ============================================================================

import { useState } from 'react';
import { Save, AlertCircle, Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

interface FeatureToggleConfig {
  id: string;
  label: string;
  description: string;
  enabled: boolean;
}

const DEFAULT_FEATURES: FeatureToggleConfig[] = [
  { id: 'pos-quick-sale', label: 'POS Quick Sale', description: 'Enable quick sale mode in POS', enabled: true },
  { id: 'eye-test-module', label: 'Eye Test Module', description: 'Enable clinical eye testing', enabled: true },
  { id: 'workshop-module', label: 'Workshop Module', description: 'Enable workshop management', enabled: true },
  { id: 'loyalty-points', label: 'Loyalty Points', description: 'Enable customer loyalty program', enabled: false },
  { id: 'split-payments', label: 'Split Payments', description: 'Allow splitting payments across methods', enabled: true },
  { id: 'credit-billing', label: 'Credit Billing', description: 'Enable credit/invoice billing', enabled: true },
  { id: 'emi-payments', label: 'EMI Payments', description: 'Enable EMI payment options', enabled: false },
  { id: 'storefront', label: 'Storefront', description: 'Enable online storefront', enabled: false },
];

interface FeatureToggleProps {
  storeId: string;
}

export function FeatureToggles({  }: FeatureToggleProps) {
  const { user } = useAuth();
  const toast = useToast();
  
  const [features, setFeatures] = useState<FeatureToggleConfig[]>(DEFAULT_FEATURES);
  const [isLoading, setIsLoading] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);

  // Only superadmins can access this
  const isSuperAdmin = user?.activeRole === 'SUPERADMIN';

  const handleToggle = (id: string) => {
    if (!isSuperAdmin) return;
    
    setFeatures(prev => prev.map(f => 
      f.id === id ? { ...f, enabled: !f.enabled } : f
    ));
    setHasChanges(true);
  };

  const handleSave = async () => {
    setIsLoading(true);
    try {
      // TODO: Call API endpoint PATCH /settings/feature-toggles/{storeId}
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      toast.success('Feature toggles updated successfully');
      setHasChanges(false);
    } catch (error) {
      toast.error('Failed to save feature toggles');
    } finally {
      setIsLoading(false);
    }
  };

  if (!isSuperAdmin) {
    return (
      <div className="flex items-center justify-center p-8 bg-gray-800 rounded-lg border border-gray-700">
        <AlertCircle className="w-5 h-5 text-yellow-500 mr-2" />
        <p className="text-gray-300">Only superadmins can manage feature toggles</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {features.map(feature => (
          <div
            key={feature.id}
            className="p-4 bg-gray-800 border border-gray-700 rounded-lg flex items-start justify-between hover:border-gray-600 transition"
          >
            <div className="flex-1">
              <h3 className="font-semibold text-white">{feature.label}</h3>
              <p className="text-sm text-gray-400 mt-1">{feature.description}</p>
            </div>
            <button
              onClick={() => handleToggle(feature.id)}
              className={clsx(
                'ml-4 relative inline-flex h-8 w-14 items-center rounded-full transition-colors flex-shrink-0',
                feature.enabled ? 'bg-blue-600' : 'bg-gray-700'
              )}
            >
              <span
                className={clsx(
                  'inline-block h-6 w-6 transform rounded-full bg-white transition-transform',
                  feature.enabled ? 'translate-x-7' : 'translate-x-1'
                )}
              />
            </button>
          </div>
        ))}
      </div>

      {hasChanges && (
        <div className="flex justify-end pt-4 border-t border-gray-700">
          <button
            onClick={handleSave}
            disabled={isLoading}
            className={clsx(
              'flex items-center gap-2 px-6 py-2 rounded-lg font-medium transition',
              isLoading
                ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                : 'bg-blue-600 text-white hover:bg-blue-700'
            )}
          >
            {isLoading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                Save Changes
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
}
