// ============================================================================
// IMS 2.0 - Contact Lens Fitting Page
// ============================================================================
// Manage contact lens fittings and prescriptions

import { useState } from 'react';
import {
  Eye,
  User,
  Search,
  Plus,
  Calendar,
  AlertCircle,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';

export function ContactLensFittingPage() {
  const toast = useToast();
  const [searchQuery, setSearchQuery] = useState('');
  const showDevMessage = () => toast.info('Contact lens fitting module is under development');

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Contact Lens Fitting</h1>
          <p className="text-gray-500">Manage contact lens prescriptions and fittings</p>
        </div>
        <button onClick={showDevMessage} className="btn-primary flex items-center gap-2">
          <Plus className="w-4 h-4" />
          New Fitting
        </button>
      </div>

      {/* Info Banner */}
      <div className="card bg-blue-50 border-blue-200">
        <div className="flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
          <div className="text-blue-900">
            <p className="font-medium mb-1">Contact Lens Fitting Module</p>
            <p className="text-sm text-blue-700">
              This module is under development. Contact lens fitting records will include:
            </p>
            <ul className="text-sm text-blue-700 mt-2 ml-4 list-disc space-y-1">
              <li>Base curve (BC) and diameter measurements</li>
              <li>Contact lens power specifications</li>
              <li>Trial lens fitting notes</li>
              <li>Brand and product recommendations</li>
              <li>Follow-up appointment scheduling</li>
              <li>Replacement cycle tracking</li>
            </ul>
          </div>
        </div>
      </div>

      {/* Search */}
      <div className="card">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="input-field pl-10"
            placeholder="Search by patient name or phone..."
          />
        </div>
      </div>

      {/* Empty State */}
      <div className="card text-center py-16">
        <div className="w-20 h-20 bg-purple-100 rounded-full flex items-center justify-center mx-auto mb-4">
          <Eye className="w-10 h-10 text-purple-600" />
        </div>
        <h3 className="text-lg font-medium text-gray-900 mb-2">No Contact Lens Fittings Yet</h3>
        <p className="text-gray-500 mb-6">
          Start by creating a new contact lens fitting record
        </p>
        <button onClick={showDevMessage} className="btn-primary inline-flex items-center gap-2">
          <Plus className="w-4 h-4" />
          Create First Fitting
        </button>
      </div>

      {/* Common Contact Lens Types Info Card */}
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        <div className="card">
          <h3 className="font-medium text-gray-900 mb-3">Common Lens Types</h3>
          <div className="space-y-2 text-sm">
            <div className="flex items-center justify-between p-2 bg-gray-50 rounded">
              <span className="text-gray-700">Daily Disposable</span>
              <span className="text-gray-500">1 day</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-gray-50 rounded">
              <span className="text-gray-700">Bi-weekly</span>
              <span className="text-gray-500">2 weeks</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-gray-50 rounded">
              <span className="text-gray-700">Monthly</span>
              <span className="text-gray-500">1 month</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-gray-50 rounded">
              <span className="text-gray-700">Extended Wear</span>
              <span className="text-gray-500">Up to 30 days</span>
            </div>
          </div>
        </div>

        <div className="card">
          <h3 className="font-medium text-gray-900 mb-3">Typical Measurements</h3>
          <div className="space-y-2 text-sm">
            <div className="p-2 bg-gray-50 rounded">
              <div className="flex justify-between mb-1">
                <span className="text-gray-700">Base Curve (BC)</span>
                <span className="text-gray-500">8.0 - 9.0 mm</span>
              </div>
              <p className="text-xs text-gray-500">Curvature of the lens</p>
            </div>
            <div className="p-2 bg-gray-50 rounded">
              <div className="flex justify-between mb-1">
                <span className="text-gray-700">Diameter (DIA)</span>
                <span className="text-gray-500">13.8 - 14.5 mm</span>
              </div>
              <p className="text-xs text-gray-500">Overall size of the lens</p>
            </div>
            <div className="p-2 bg-gray-50 rounded">
              <div className="flex justify-between mb-1">
                <span className="text-gray-700">Power</span>
                <span className="text-gray-500">-20.00 to +20.00</span>
              </div>
              <p className="text-xs text-gray-500">Refractive correction</p>
            </div>
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="card bg-gradient-to-r from-purple-50 to-blue-50">
        <h3 className="font-medium text-gray-900 mb-3">Quick Actions</h3>
        <div className="grid grid-cols-2 gap-3">
          <button onClick={showDevMessage} className="p-3 bg-white rounded-lg shadow-sm hover:shadow transition-shadow text-left">
            <User className="w-5 h-5 text-purple-600 mb-2" />
            <p className="text-sm font-medium text-gray-900">New Patient</p>
            <p className="text-xs text-gray-500">Add to queue</p>
          </button>
          <button onClick={showDevMessage} className="p-3 bg-white rounded-lg shadow-sm hover:shadow transition-shadow text-left">
            <Calendar className="w-5 h-5 text-purple-600 mb-2" />
            <p className="text-sm font-medium text-gray-900">Follow-up</p>
            <p className="text-xs text-gray-500">Schedule visit</p>
          </button>
          <button onClick={showDevMessage} className="p-3 bg-white rounded-lg shadow-sm hover:shadow transition-shadow text-left">
            <Eye className="w-5 h-5 text-purple-600 mb-2" />
            <p className="text-sm font-medium text-gray-900">Trial Lens</p>
            <p className="text-xs text-gray-500">Fitting session</p>
          </button>
          <button onClick={showDevMessage} className="p-3 bg-white rounded-lg shadow-sm hover:shadow transition-shadow text-left">
            <Search className="w-5 h-5 text-purple-600 mb-2" />
            <p className="text-sm font-medium text-gray-900">Search</p>
            <p className="text-xs text-gray-500">Find records</p>
          </button>
        </div>
      </div>
    </div>
  );
}

export default ContactLensFittingPage;
