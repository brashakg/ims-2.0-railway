// ============================================================================
// IMS 2.0 - Suppliers List Panel
// ============================================================================

import { useState } from 'react';
import {
  Edit,
  User,
  Phone,
  Mail,
  MapPin,
  Truck,
  Link2,
  Copy,
  Check,
  Loader2,
  X,
} from 'lucide-react';
import { vendorsApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import type { Supplier } from './purchaseTypes';

interface SupplierPanelProps {
  suppliers: Supplier[];
}

export function SupplierPanel({ suppliers }: SupplierPanelProps) {
  // The "Generate vendor portal link" action used to live on the (now
  // retired) VendorManagement page. Re-homed here onto the real Suppliers
  // view so the feature isn't lost (PR #454 deleted the only UI for it).
  const [portalForVendor, setPortalForVendor] = useState<{ id: string; name: string } | null>(null);

  return (
    <div className="grid grid-cols-1 desktop:grid-cols-2 gap-4">
      {suppliers.map((supplier) => (
        <div key={supplier.id} className="card hover:shadow-lg transition-shadow">
          <div className="flex items-start justify-between mb-4">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                <h3 className="text-lg font-semibold text-gray-900">{supplier.name}</h3>
                <span className="px-2 py-1 bg-gray-100 text-gray-700 text-xs rounded">
                  {supplier.code}
                </span>
              </div>
              <div className="flex items-center gap-1 mb-2">
                {[...Array(5)].map((_, i) => (
                  <svg
                    key={i}
                    className={`w-4 h-4 ${i < Math.floor(supplier.rating) ? 'text-yellow-600' : 'text-gray-700'}`}
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                  </svg>
                ))}
                <span className="text-sm text-gray-600 ml-2">{supplier.rating}/5</span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {/* Vendor portal token - admin gives the lab a no-login URL */}
              <button
                type="button"
                onClick={() => setPortalForVendor({ id: supplier.id, name: supplier.name })}
                title="Generate a no-login portal link for this vendor"
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 rounded-lg transition-colors"
              >
                <Link2 className="w-3.5 h-3.5" />
                Portal link
              </button>
              <button className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
                <Edit className="w-5 h-5 text-gray-600" />
              </button>
            </div>
          </div>

          <div className="space-y-2 mb-4">
            <div className="flex items-center gap-2 text-sm">
              <User className="w-4 h-4 text-gray-500" />
              <span className="text-gray-700">{supplier.contactPerson}</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <Phone className="w-4 h-4 text-gray-500" />
              <span className="text-gray-700">{supplier.phone}</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <Mail className="w-4 h-4 text-gray-500" />
              <span className="text-gray-700">{supplier.email}</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <MapPin className="w-4 h-4 text-gray-500" />
              <span className="text-gray-700">{supplier.city}, {supplier.state}</span>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3 p-3 bg-gray-50 rounded-lg mb-3">
            <div>
              <p className="text-xs text-gray-600">On-Time Delivery</p>
              <p className="text-sm font-semibold text-gray-900">{supplier.performance.onTimeDelivery}%</p>
            </div>
            <div>
              <p className="text-xs text-gray-600">Quality Score</p>
              <p className="text-sm font-semibold text-gray-900">{supplier.performance.qualityScore}%</p>
            </div>
            <div>
              <p className="text-xs text-gray-600">Price Score</p>
              <p className="text-sm font-semibold text-gray-900">{supplier.performance.priceCompetitiveness}%</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="text-xs text-gray-600">Total Purchases</p>
              <p className="font-semibold text-gray-900">{'₹'}{(supplier.totalPurchases / 100000).toFixed(1)}L</p>
            </div>
            <div>
              <p className="text-xs text-gray-600">Outstanding</p>
              <p className={`font-semibold ${supplier.currentOutstanding > supplier.creditLimit * 0.8 ? 'text-red-600' : 'text-gray-900'}`}>
                {'₹'}{(supplier.currentOutstanding / 100000).toFixed(1)}L
              </p>
            </div>
          </div>
        </div>
      ))}

      {suppliers.length === 0 && (
        <div className="col-span-2 text-center py-12">
          <Truck className="w-12 h-12 text-gray-500 mx-auto mb-3" />
          <p className="text-gray-500">No suppliers found</p>
        </div>
      )}

      {portalForVendor && (
        <PortalTokenModal
          vendorId={portalForVendor.id}
          vendorName={portalForVendor.name}
          onClose={() => setPortalForVendor(null)}
        />
      )}
    </div>
  );
}

// ============================================================================
// Portal Token Modal - generates a no-login URL the lens lab opens directly
// (recovered from the retired VendorManagement page; logic unchanged)
// ============================================================================

function PortalTokenModal({
  vendorId, vendorName, onClose,
}: { vendorId: string; vendorName: string; onClose: () => void }) {
  const toast = useToast();
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<{ token_id: string; portal_url: string; expires_at: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const generate = async () => {
    setGenerating(true);
    try {
      const r = await vendorsApi.generatePortalToken(vendorId);
      setResult({ token_id: r.token_id, portal_url: r.portal_url, expires_at: r.expires_at });
      toast.success(`Portal link generated for ${vendorName}`);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to generate token';
      toast.error(msg);
    } finally {
      setGenerating(false);
    }
  };

  const copy = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.portal_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Could not copy to clipboard');
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-2xl w-full max-w-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-gray-900 flex items-center gap-2">
              <Link2 className="w-4 h-4 text-indigo-600" />
              Vendor portal link
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">{vendorName}</p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {!result ? (
            <>
              <p className="text-sm text-gray-600">
                Generate a no-login URL that <strong>{vendorName}</strong> can open
                directly to view the jobs you've assigned them. They can post status
                updates without an IMS account. Customer PII (phone / address) is
                hidden - only initials are shown.
              </p>
              <ul className="text-xs text-gray-500 space-y-1 ml-4 list-disc">
                <li>Default validity: 1 year</li>
                <li>Token can be revoked anytime</li>
                <li>Every status update is audit-logged</li>
              </ul>
              <button
                type="button"
                onClick={generate}
                disabled={generating}
                className="w-full px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {generating ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Link2 className="w-4 h-4" />
                )}
                Generate portal link
              </button>
            </>
          ) : (
            <>
              <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-800">
                <p className="font-medium">Link generated. Share it with {vendorName} via WhatsApp / email.</p>
                <p className="text-xs text-green-700 mt-1">
                  Valid until {new Date(result.expires_at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}
                </p>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">
                  Portal URL
                </label>
                <div className="flex gap-2">
                  <input
                    readOnly
                    value={result.portal_url}
                    className="flex-1 px-3 py-2 text-sm font-mono border border-gray-300 rounded-lg bg-gray-50 text-gray-700"
                    onFocus={(e) => e.target.select()}
                  />
                  <button
                    type="button"
                    onClick={copy}
                    className="px-3 py-2 bg-gray-900 hover:bg-gray-700 text-white rounded-lg text-sm font-medium flex items-center gap-1.5"
                  >
                    {copied ? (
                      <>
                        <Check className="w-3.5 h-3.5" />
                        Copied
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5" />
                        Copy
                      </>
                    )}
                  </button>
                </div>
              </div>

              <p className="text-xs text-gray-500 font-mono">
                Token: {result.token_id}
              </p>
            </>
          )}
        </div>

        <div className="px-5 py-3 border-t border-gray-200 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
