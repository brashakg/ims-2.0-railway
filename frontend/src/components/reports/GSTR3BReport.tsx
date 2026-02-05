// ============================================================================
// IMS 2.0 - GSTR-3B Summary Report
// ============================================================================
// Monthly summary return for GST compliance

import { useState, useEffect } from 'react';
import {
  Download,
  FileText,
  Calendar,
  Loader2,
  AlertCircle,
  CheckCircle,
  RefreshCw,
  IndianRupee,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

interface GSTR3BTaxLiability {
  integratedTax: number;
  centralTax: number;
  stateTax: number;
  cess: number;
}

interface GSTR3BITCAvailable {
  integratedTax: number;
  centralTax: number;
  stateTax: number;
  cess: number;
}

interface GSTR3BData {
  period: string;
  gstin: string;
  legalName: string;

  // Table 3.1: Outward taxable supplies
  outwardTaxableSupplies: GSTR3BTaxLiability;
  outwardTaxableValue: number;

  // Table 3.2: Outward taxable supplies (zero rated)
  zeroRatedSupplies: GSTR3BTaxLiability;
  zeroRatedValue: number;

  // Table 4: Eligible ITC
  itcAvailable: GSTR3BITCAvailable;

  // Table 5: Exempt, Nil rated and Non-GST supplies
  exemptSupplies: number;

  // Table 6.1: Payment of tax
  taxPayable: GSTR3BTaxLiability;
  itcUtilized: GSTR3BITCAvailable;
  taxPaidCash: GSTR3BTaxLiability;

  // Interest & Late Fee
  interest: GSTR3BTaxLiability;
  lateFee: number;
}

export function GSTR3BReport() {
  const { user } = useAuth();
  const toast = useToast();

  const [isLoading, setIsLoading] = useState(false);
  const [reportData, setReportData] = useState<GSTR3BData | null>(null);
  const [selectedMonth, setSelectedMonth] = useState(new Date().toISOString().slice(0, 7)); // YYYY-MM

  useEffect(() => {
    if (selectedMonth) {
      loadReportData();
    }
  }, [selectedMonth]);

  const loadReportData = async () => {
    setIsLoading(true);
    try {
      // In production, fetch from backend API
      await new Promise(resolve => setTimeout(resolve, 1000));

      // Mock data for demonstration
      const mockData: GSTR3BData = {
        period: selectedMonth,
        gstin: '27AABCU9603R1ZM',
        legalName: 'Better Vision Optics Pvt Ltd',

        outwardTaxableSupplies: {
          integratedTax: 45000,
          centralTax: 35000,
          stateTax: 35000,
          cess: 0,
        },
        outwardTaxableValue: 650000,

        zeroRatedSupplies: {
          integratedTax: 0,
          centralTax: 0,
          stateTax: 0,
          cess: 0,
        },
        zeroRatedValue: 0,

        itcAvailable: {
          integratedTax: 12000,
          centralTax: 8000,
          stateTax: 8000,
          cess: 0,
        },

        exemptSupplies: 0,

        taxPayable: {
          integratedTax: 45000,
          centralTax: 35000,
          stateTax: 35000,
          cess: 0,
        },

        itcUtilized: {
          integratedTax: 12000,
          centralTax: 8000,
          stateTax: 8000,
          cess: 0,
        },

        taxPaidCash: {
          integratedTax: 33000,
          centralTax: 27000,
          stateTax: 27000,
          cess: 0,
        },

        interest: {
          integratedTax: 0,
          centralTax: 0,
          stateTax: 0,
          cess: 0,
        },

        lateFee: 0,
      };

      setReportData(mockData);
    } catch (error: any) {
      toast.error(error?.message || 'Failed to load GSTR-3B report');
    } finally {
      setIsLoading(false);
    }
  };

  const downloadJSON = () => {
    if (!reportData) return;

    const dataStr = JSON.stringify(reportData, null, 2);
    const dataBlob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(dataBlob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `GSTR3B_${reportData.period}.json`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success('GSTR-3B JSON downloaded successfully');
  };

  const getTotalTax = (liability: GSTR3BTaxLiability): number => {
    return liability.integratedTax + liability.centralTax + liability.stateTax + liability.cess;
  };

  const renderTaxRow = (label: string, value: number, highlight = false) => (
    <tr className={highlight ? 'bg-purple-50 font-medium' : ''}>
      <td className="px-4 py-3 text-sm text-gray-700">{label}</td>
      <td className="px-4 py-3 text-sm text-right text-gray-900">
        ₹{value.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
      </td>
    </tr>
  );

  const renderTaxLiabilityRow = (label: string, liability: GSTR3BTaxLiability, highlight = false) => (
    <>
      <tr className={highlight ? 'bg-purple-50' : ''}>
        <td className="px-4 py-3 text-sm text-gray-900 font-medium" rowSpan={5}>
          {label}
        </td>
        <td className="px-4 py-3 text-sm text-gray-700">Integrated Tax</td>
        <td className="px-4 py-3 text-sm text-right text-gray-900">
          ₹{liability.integratedTax.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
        </td>
      </tr>
      <tr className={highlight ? 'bg-purple-50' : ''}>
        <td className="px-4 py-3 text-sm text-gray-700">Central Tax</td>
        <td className="px-4 py-3 text-sm text-right text-gray-900">
          ₹{liability.centralTax.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
        </td>
      </tr>
      <tr className={highlight ? 'bg-purple-50' : ''}>
        <td className="px-4 py-3 text-sm text-gray-700">State Tax</td>
        <td className="px-4 py-3 text-sm text-right text-gray-900">
          ₹{liability.stateTax.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
        </td>
      </tr>
      <tr className={highlight ? 'bg-purple-50' : ''}>
        <td className="px-4 py-3 text-sm text-gray-700">Cess</td>
        <td className="px-4 py-3 text-sm text-right text-gray-900">
          ₹{liability.cess.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
        </td>
      </tr>
      <tr className={highlight ? 'bg-purple-100 font-medium' : 'bg-gray-50 font-medium'}>
        <td className="px-4 py-3 text-sm text-gray-900">Total</td>
        <td className="px-4 py-3 text-sm text-right text-gray-900">
          ₹{getTotalTax(liability).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
        </td>
      </tr>
    </>
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">GSTR-3B Summary</h1>
          <p className="text-gray-500">Monthly summary return for GST compliance</p>
        </div>
        <button
          onClick={loadReportData}
          disabled={isLoading}
          className="btn-outline flex items-center gap-2"
        >
          {isLoading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <Calendar className="w-5 h-5 text-gray-500" />
            <label className="text-sm font-medium text-gray-700">Tax Period:</label>
            <input
              type="month"
              value={selectedMonth}
              onChange={(e) => setSelectedMonth(e.target.value)}
              className="input-field"
            />
          </div>

          {reportData && (
            <div className="ml-auto flex items-center gap-2">
              <button onClick={downloadJSON} className="btn-primary text-sm flex items-center gap-2">
                <Download className="w-4 h-4" />
                Download JSON
              </button>
            </div>
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : reportData ? (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
            <div className="card">
              <p className="text-sm text-gray-600">Outward Supplies</p>
              <p className="text-2xl font-bold text-gray-900">₹{reportData.outwardTaxableValue.toLocaleString('en-IN')}</p>
            </div>
            <div className="card">
              <p className="text-sm text-gray-600">Tax Liability</p>
              <p className="text-2xl font-bold text-red-600">₹{getTotalTax(reportData.taxPayable).toLocaleString('en-IN')}</p>
            </div>
            <div className="card">
              <p className="text-sm text-gray-600">ITC Available</p>
              <p className="text-2xl font-bold text-green-600">₹{getTotalTax(reportData.itcAvailable).toLocaleString('en-IN')}</p>
            </div>
            <div className="card">
              <p className="text-sm text-gray-600">Cash Paid</p>
              <p className="text-2xl font-bold text-purple-600">₹{getTotalTax(reportData.taxPaidCash).toLocaleString('en-IN')}</p>
            </div>
          </div>

          {/* GSTIN Info */}
          <div className="card bg-green-50 border-green-200">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-green-700 flex items-center gap-2">
                  <CheckCircle className="w-4 h-4" />
                  GSTIN: <span className="font-mono font-medium">{reportData.gstin}</span>
                </p>
                <p className="text-sm text-green-900 mt-1 font-medium">{reportData.legalName}</p>
              </div>
              <div className="text-right">
                <p className="text-xs text-green-700">Tax Period</p>
                <p className="text-lg font-bold text-green-900">
                  {new Date(reportData.period + '-01').toLocaleDateString('en-IN', {
                    month: 'long',
                    year: 'numeric',
                  })}
                </p>
              </div>
            </div>
          </div>

          {/* Table 3.1: Outward Taxable Supplies */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <FileText className="w-5 h-5" />
              3.1 Outward Taxable Supplies (Other than zero rated, nil rated and exempted)
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="bg-gray-100 border-b border-gray-200">
                    <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Description</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Tax Type</th>
                    <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {renderTaxRow('Taxable Value', reportData.outwardTaxableValue, true)}
                  {renderTaxLiabilityRow('Tax Liability', reportData.outwardTaxableSupplies)}
                </tbody>
              </table>
            </div>
          </div>

          {/* Table 4: Eligible ITC */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <IndianRupee className="w-5 h-5" />
              4 Eligible ITC (Input Tax Credit)
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="bg-gray-100 border-b border-gray-200">
                    <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Description</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Tax Type</th>
                    <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {renderTaxLiabilityRow('ITC Available', reportData.itcAvailable, true)}
                </tbody>
              </table>
            </div>
          </div>

          {/* Table 6.1: Payment of Tax */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <IndianRupee className="w-5 h-5" />
              6.1 Payment of Tax
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="bg-gray-100 border-b border-gray-200">
                    <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Description</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Tax Type</th>
                    <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {renderTaxLiabilityRow('Tax Payable', reportData.taxPayable)}
                  {renderTaxLiabilityRow('ITC Utilized', reportData.itcUtilized, true)}
                  {renderTaxLiabilityRow('Tax Paid in Cash', reportData.taxPaidCash, true)}
                  {renderTaxRow('Interest (if any)', getTotalTax(reportData.interest))}
                  {renderTaxRow('Late Fee', reportData.lateFee)}
                </tbody>
              </table>
            </div>
          </div>

          {/* Net Tax Liability */}
          <div className="card bg-purple-50 border-purple-200">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-purple-700 font-medium">Net Tax Liability</p>
                <p className="text-xs text-purple-600 mt-1">Total tax payable after ITC</p>
              </div>
              <div className="text-right">
                <p className="text-3xl font-bold text-purple-900">
                  ₹{(getTotalTax(reportData.taxPayable) - getTotalTax(reportData.itcAvailable)).toLocaleString('en-IN')}
                </p>
              </div>
            </div>
          </div>

          {/* Info Banner */}
          <div className="card bg-blue-50 border-blue-200">
            <div className="flex gap-3">
              <AlertCircle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-blue-900">
                <p className="font-medium mb-1">GSTR-3B Filing Information</p>
                <ul className="list-disc list-inside space-y-1 text-blue-800">
                  <li>GSTR-3B is a simplified summary return filed monthly</li>
                  <li>Filing due date: 20th of next month</li>
                  <li>Late fee: ₹50/day (₹20/day for nil return) - CGST + SGST</li>
                  <li>Must be filed even if there is no business activity (Nil Return)</li>
                </ul>
              </div>
            </div>
          </div>
        </>
      ) : (
        <div className="card text-center py-12 text-gray-500">
          <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>No data available. Please select a tax period.</p>
        </div>
      )}
    </div>
  );
}

export default GSTR3BReport;
