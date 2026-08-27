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
  Receipt,
  Link2,
  Copy,
  Check,
  Loader2,
  X,
} from 'lucide-react';
import { vendorsApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import { useStorePrintInfo } from '../../hooks/useStorePrintInfo';
import { useGstStateCodes } from '../../hooks/useGstStateCodes';
import { gstinStateCode } from '../../constants/gst';
import type { Supplier } from './purchaseTypes';

// How a purchase from this vendor is taxed. Local and NOT exported on purpose:
// the canonical exported helper lands in the sibling PR claude/po-gst-and-ux as
// `isInterStateSupply` in constants/gst.ts -- fold this into it when that merges
// (two exports of that name in one module is a compile error on main).
//
// Both states are read from GSTINs and NOTHING else -- the same source
// purchase_invoice_engine.determine_place_of_supply reads when it stamps the
// bill. (That engine also accepts an explicit place-of-supply override, which
// a card has no way to know about; with none passed it is the two GSTINs.)
// An address is not a registration: stores.py sets a store's state_code
// from its ADDRESS while its gstin is the entity's registration for that state,
// falling back to the entity's PRIMARY GSTIN elsewhere (WizOpt's online store
// bills under BV Opticals Pvt Ltd). Reading the address first made this card
// print the OPPOSITE verdict to the bill for exactly those stores.
//
// The one place it departs from the engine, on purpose: the engine must return
// a boolean for a bill, so an unknown pair falls back to intra-state. A card is
// a statement to a human, and "Same state - CGST + SGST" over a pair nobody has
// established is a wrong-tax claim, not a conservative default -- so unknown
// reads as unknown, never as the engine's fallback.
type TaxSplit = 'igst' | 'cgst_sgst' | 'unknown';

function taxSplit(vendorStateCode: string, buyerStateCode: string): TaxSplit {
  if (!vendorStateCode || !buyerStateCode) return 'unknown';
  return vendorStateCode === buyerStateCode ? 'cgst_sgst' : 'igst';
}

const TAX_SPLIT_LABEL: Record<TaxSplit, string> = {
  igst: 'Other state - IGST',
  cgst_sgst: 'Same state - CGST + SGST',
  unknown: 'Tax split unknown',
};

const TAX_SPLIT_CLASS: Record<TaxSplit, string> = {
  igst: 'bg-amber-50 text-amber-700',
  cgst_sgst: 'bg-emerald-50 text-emerald-700',
  unknown: 'bg-gray-100 text-gray-600',
};

interface SupplierPanelProps {
  suppliers: Supplier[];
  /** Opens the supplier editor. Optional so the panel renders standalone. */
  onEdit?: (supplier: Supplier) => void;
}

export function SupplierPanel({ suppliers, onEdit }: SupplierPanelProps) {
  // The "Generate vendor portal link" action used to live on the (now
  // retired) VendorManagement page. Re-homed here onto the real Suppliers
  // view so the feature isn't lost (PR #454 deleted the only UI for it).
  const [portalForVendor, setPortalForVendor] = useState<{ id: string; name: string } | null>(null);
  // The buying store's own GST REGISTRATION decides how a purchase from each
  // vendor is taxed. Same state -> CGST + SGST; another state -> IGST.
  const storeInfo = useStorePrintInfo();
  const stateNames = useGstStateCodes();
  // A two-digit prefix the server's state list does not contain is not a
  // state. The engine's parser (org_validation.validate_gstin, behind
  // determine_place_of_supply) rejects such a GSTIN and reads NO state off it,
  // so a card that keeps the raw digits prints a tax verdict ("Other state -
  // IGST") over a pair the engine never established. Unknown code -> '' ->
  // taxSplit says 'unknown'. Same stateListLoaded idiom as
  // SupplierFormModal.handleSave: until the list arrives there is nothing to
  // check against, so the raw read stands for that first paint.
  const stateListLoaded = Object.keys(stateNames).length > 0;
  const knownStateCode = (code: string): string =>
    stateListLoaded && !stateNames[code] ? '' : code;
  const buyerStateCode = knownStateCode(gstinStateCode(storeInfo?.gstin));

  return (
    <div className="grid grid-cols-1 desktop:grid-cols-2 gap-4">
      {suppliers.map((supplier) => {
        const vendorStateCode = knownStateCode(gstinStateCode(supplier.gstNumber));
        // Display only -- an unregistered vendor still has an address to show.
        const vendorState =
          supplier.state || stateNames[supplier.stateCode || vendorStateCode] || '';
        const split = taxSplit(vendorStateCode, buyerStateCode);
        return (
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
              <button
                type="button"
                onClick={() => onEdit?.(supplier)}
                aria-label={`Edit ${supplier.name}`}
                title={`Edit ${supplier.name}`}
                className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              >
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
              <span className="text-gray-700">
                {[supplier.city, vendorState].filter(Boolean).join(', ') || 'No address'}
              </span>
            </div>
            {/* GSTIN + what it means for tax. The state comes from the GSTIN
                itself, so a wrong number is visible here instead of showing up
                later as a wrongly-taxed purchase. */}
            <div className="flex items-center gap-2 text-sm flex-wrap">
              <Receipt className="w-4 h-4 text-gray-500" />
              {supplier.gstNumber ? (
                <span className="font-mono text-gray-700">{supplier.gstNumber}</span>
              ) : (
                <span className="text-gray-500">Unregistered (no GSTIN)</span>
              )}
              {/* Only a REGISTERED vendor has a GST split to state. An
                  unregistered one charges no GST at all, and the line above
                  already says so. */}
              {supplier.gstNumber && (
                <span className={`px-2 py-0.5 text-xs rounded ${TAX_SPLIT_CLASS[split]}`}>
                  {TAX_SPLIT_LABEL[split]}
                </span>
              )}
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
        );
      })}

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
