// ============================================================================
// IMS 2.0 - GSTR-1 Report Generator
// ============================================================================
// Generate GSTR-1 reports for outward supplies (sales)

import { useState, useEffect } from 'react';
import {
  Download,
  FileText,
  Calendar,
  AlertCircle,
  AlertTriangle,
  CheckCircle,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { reportsApi } from '../../services/api';
import { neutralizeFormula } from '../../utils/exportUtils';

interface GSTR1Invoice {
  invoiceNumber: string;
  invoiceDate: string;
  customerName: string;
  customerGSTIN?: string;
  customerState: string;
  placeOfSupply: string;
  invoiceValue: number;
  taxableValue: number;
  cgst: number;
  sgst: number;
  igst: number;
  totalTax: number;
  hsnCode: string;
  gstRate: number;
}

interface GSTR1B2CSummary {
  placeOfSupply: string;
  gstRate: number;
  taxableValue: number;
  cgst: number;
  sgst: number;
  igst: number;
  totalTax: number;
}

interface GSTR1ValidationIssue {
  level: string;
  issue: string;
  invoice?: string;
  // Aggregated issues (e.g. the >16-char serial warn) carry a machine code +
  // how many invoices it applies to.
  issue_code?: string;
  count?: number;
}

interface GSTR1Validation {
  ok: boolean;
  issueCount: number;
  issues: GSTR1ValidationIssue[];
}

// issue_code of the aggregated "minted serial exceeds GSTN's 16-char cap" warn.
const SERIAL_OVER_CAP_CODE = 'INVOICE_SERIAL_OVER_16';

interface GSTR1Data {
  b2b: GSTR1Invoice[];
  b2cl: GSTR1Invoice[];
  b2cs: GSTR1B2CSummary[];
  period: string;
  gstin: string;
  legalName: string;
  totalInvoices: number;
  totalTaxableValue: number;
  totalTax: number;
  validation?: GSTR1Validation;
}

export function GSTR1Report() {
  const toast = useToast();
  const { user } = useAuth();

  const [isLoading, setIsLoading] = useState(false);
  const [reportData, setReportData] = useState<GSTR1Data | null>(null);
  const [selectedMonth, setSelectedMonth] = useState(new Date().toISOString().slice(0, 7)); // YYYY-MM
  const [activeSection, setActiveSection] = useState<'b2b' | 'b2cl' | 'b2cs'>('b2b');

  useEffect(() => {
    if (selectedMonth) {
      loadReportData();
    }
  }, [selectedMonth]);

  const loadReportData = async () => {
    setIsLoading(true);
    try {
      const data = await reportsApi.getGSTR1Report(selectedMonth, user?.activeStoreId);
      setReportData(data as GSTR1Data);
    } catch (error: any) {
      toast.error(error?.message || 'Failed to load GSTR-1 report');
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
    link.download = `GSTR1_${reportData.period}.json`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success('GSTR-1 JSON downloaded successfully');
  };

  const hasSerialLengthIssue = Boolean(
    reportData?.validation?.issues?.some(
      (i) => i.issue_code === SERIAL_OVER_CAP_CODE
    )
  );

  // GST portal offline-tool JSON (the format you upload on gst.gov.in).
  // NOTE: validation warnings are shown HERE (and in the banner above the
  // tables) -- they are never injected into the uploaded JSON body itself.
  const downloadGstnJSON = async () => {
    if (!reportData) return;
    if (hasSerialLengthIssue) {
      const proceed = window.confirm(
        'Warning: invoice numbers in this report exceed the GSTN 16-character ' +
          'cap, so the portal may REJECT this upload. The invoice serial ' +
          'format needs shortening (a tracked fix). Download anyway?'
      );
      if (!proceed) return;
      toast.warning(
        'GSTN JSON downloaded with over-length invoice numbers - the portal may reject it'
      );
    }
    try {
      const gstn = await reportsApi.getGSTR1GstnJson(selectedMonth, user?.activeStoreId);
      const dataStr = JSON.stringify(gstn, null, 2);
      const dataBlob = new Blob([dataStr], { type: 'application/json' });
      const url = URL.createObjectURL(dataBlob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `GSTR1_GSTN_${reportData.period}.json`;
      link.click();
      URL.revokeObjectURL(url);
      toast.success('GSTN portal JSON downloaded');
    } catch (error: any) {
      toast.error(error?.message || 'Failed to generate GSTN JSON');
    }
  };

  const downloadCSV = () => {
    if (!reportData) return;

    let csv = '';
    const BOM = '﻿'; // UTF-8 BOM for Excel compatibility

    if (activeSection === 'b2b' && reportData.b2b.length > 0) {
      csv = 'Invoice Number,Date,Customer Name,GSTIN,State,Place of Supply,Invoice Value,Taxable Value,CGST,SGST,IGST,Total Tax,HSN Code,GST Rate\n';
      reportData.b2b.forEach(inv => {
        csv += `${inv.invoiceNumber},${inv.invoiceDate},${neutralizeFormula(inv.customerName)},${inv.customerGSTIN},${inv.customerState},${inv.placeOfSupply},${inv.invoiceValue},${inv.taxableValue},${inv.cgst},${inv.sgst},${inv.igst},${inv.totalTax},${inv.hsnCode},${inv.gstRate}%\n`;
      });
    } else if (activeSection === 'b2cl' && reportData.b2cl.length > 0) {
      csv = 'Invoice Number,Date,Customer Name,State,Place of Supply,Invoice Value,Taxable Value,CGST,SGST,IGST,Total Tax,HSN Code,GST Rate\n';
      reportData.b2cl.forEach(inv => {
        csv += `${inv.invoiceNumber},${inv.invoiceDate},${neutralizeFormula(inv.customerName)},${inv.customerState},${inv.placeOfSupply},${inv.invoiceValue},${inv.taxableValue},${inv.cgst},${inv.sgst},${inv.igst},${inv.totalTax},${inv.hsnCode},${inv.gstRate}%\n`;
      });
    } else if (activeSection === 'b2cs' && reportData.b2cs.length > 0) {
      csv = 'Place of Supply,GST Rate,Taxable Value,CGST,SGST,IGST,Total Tax\n';
      reportData.b2cs.forEach(sum => {
        csv += `${sum.placeOfSupply},${sum.gstRate}%,${sum.taxableValue},${sum.cgst},${sum.sgst},${sum.igst},${sum.totalTax}\n`;
      });
    }

    const dataBlob = new Blob([BOM + csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(dataBlob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `GSTR1_${activeSection.toUpperCase()}_${reportData.period}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success('CSV downloaded successfully');
  };

  const renderB2BTable = () => {
    if (!reportData || reportData.b2b.length === 0) {
      return (
        <div className="text-center py-12 text-gray-500">
          <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>No B2B invoices found for the selected period</p>
        </div>
      );
    }

    return (
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-gray-100 border-b border-gray-200">
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Invoice No</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Date</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Customer Name</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">GSTIN</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Taxable Value</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">CGST</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">SGST</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">IGST</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Total Tax</th>
            </tr>
          </thead>
          <tbody>
            {reportData.b2b.map((invoice, idx) => (
              <tr key={idx} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-900">{invoice.invoiceNumber}</td>
                <td className="px-4 py-3 text-sm text-gray-600">{new Date(invoice.invoiceDate).toLocaleDateString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-gray-900">{invoice.customerName}</td>
                <td className="px-4 py-3 text-sm font-mono text-gray-600">{invoice.customerGSTIN}</td>
                <td className="px-4 py-3 text-sm text-right text-gray-900">₹{invoice.taxableValue.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right text-gray-600">₹{invoice.cgst.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right text-gray-600">₹{invoice.sgst.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right text-gray-600">₹{invoice.igst.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right font-medium text-gray-900">₹{invoice.totalTax.toLocaleString('en-IN')}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="bg-gray-100 font-medium">
              <td colSpan={4} className="px-4 py-3 text-sm text-gray-900">Total</td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2b.reduce((sum, inv) => sum + inv.taxableValue, 0).toLocaleString('en-IN')}
              </td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2b.reduce((sum, inv) => sum + inv.cgst, 0).toLocaleString('en-IN')}
              </td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2b.reduce((sum, inv) => sum + inv.sgst, 0).toLocaleString('en-IN')}
              </td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2b.reduce((sum, inv) => sum + inv.igst, 0).toLocaleString('en-IN')}
              </td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2b.reduce((sum, inv) => sum + inv.totalTax, 0).toLocaleString('en-IN')}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    );
  };

  const renderB2CLTable = () => {
    if (!reportData || reportData.b2cl.length === 0) {
      return (
        <div className="text-center py-12 text-gray-500">
          <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>No B2C Large invoices found for the selected period</p>
        </div>
      );
    }

    return (
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-gray-100 border-b border-gray-200">
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Invoice No</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Date</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Customer Name</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Place of Supply</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Invoice Value</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Taxable Value</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Total Tax</th>
            </tr>
          </thead>
          <tbody>
            {reportData.b2cl.map((invoice, idx) => (
              <tr key={idx} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-900">{invoice.invoiceNumber}</td>
                <td className="px-4 py-3 text-sm text-gray-600">{new Date(invoice.invoiceDate).toLocaleDateString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-gray-900">{invoice.customerName}</td>
                <td className="px-4 py-3 text-sm text-gray-600">{invoice.placeOfSupply}</td>
                <td className="px-4 py-3 text-sm text-right text-gray-900">₹{invoice.invoiceValue.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right text-gray-900">₹{invoice.taxableValue.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right font-medium text-gray-900">₹{invoice.totalTax.toLocaleString('en-IN')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  const renderB2CSTable = () => {
    if (!reportData || reportData.b2cs.length === 0) {
      return (
        <div className="text-center py-12 text-gray-500">
          <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>No B2C Small invoices found for the selected period</p>
        </div>
      );
    }

    return (
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-gray-100 border-b border-gray-200">
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Place of Supply</th>
              <th className="px-4 py-3 text-center text-sm font-medium text-gray-700">GST Rate</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Taxable Value</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">CGST</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">SGST</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">IGST</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Total Tax</th>
            </tr>
          </thead>
          <tbody>
            {reportData.b2cs.map((summary, idx) => (
              <tr key={idx} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-900">{summary.placeOfSupply}</td>
                <td className="px-4 py-3 text-sm text-center text-gray-600">{summary.gstRate}%</td>
                <td className="px-4 py-3 text-sm text-right text-gray-900">₹{summary.taxableValue.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right text-gray-600">₹{summary.cgst.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right text-gray-600">₹{summary.sgst.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right text-gray-600">₹{summary.igst.toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 text-sm text-right font-medium text-gray-900">₹{summary.totalTax.toLocaleString('en-IN')}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="bg-gray-100 font-medium">
              <td colSpan={2} className="px-4 py-3 text-sm text-gray-900">Total</td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2cs.reduce((sum, s) => sum + s.taxableValue, 0).toLocaleString('en-IN')}
              </td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2cs.reduce((sum, s) => sum + s.cgst, 0).toLocaleString('en-IN')}
              </td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2cs.reduce((sum, s) => sum + s.sgst, 0).toLocaleString('en-IN')}
              </td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2cs.reduce((sum, s) => sum + s.igst, 0).toLocaleString('en-IN')}
              </td>
              <td className="px-4 py-3 text-sm text-right text-gray-900">
                ₹{reportData.b2cs.reduce((sum, s) => sum + s.totalTax, 0).toLocaleString('en-IN')}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">GSTR-1 Report</h1>
          <p className="text-gray-500">Outward supplies (sales) report for GST filing</p>
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
              <button onClick={downloadJSON} className="btn-outline text-sm flex items-center gap-2">
                <Download className="w-4 h-4" />
                Download JSON
              </button>
              <button onClick={downloadGstnJSON} className="btn-outline text-sm flex items-center gap-2">
                <Download className="w-4 h-4" />
                Download GSTN JSON
              </button>
              <button onClick={downloadCSV} className="btn-primary text-sm flex items-center gap-2">
                <Download className="w-4 h-4" />
                Download CSV
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Summary Cards */}
      {reportData && (
        <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
          <div className="card">
            <p className="text-sm text-gray-600">Total Invoices</p>
            <p className="text-2xl font-bold text-gray-900">{reportData.totalInvoices}</p>
          </div>
          <div className="card">
            <p className="text-sm text-gray-600">Taxable Value</p>
            <p className="text-2xl font-bold text-gray-900">₹{reportData.totalTaxableValue.toLocaleString('en-IN')}</p>
          </div>
          <div className="card">
            <p className="text-sm text-gray-600">Total Tax</p>
            <p className="text-2xl font-bold text-gray-900">₹{reportData.totalTax.toLocaleString('en-IN')}</p>
          </div>
          <div className="card bg-green-50 border-green-200">
            <p className="text-sm text-green-700 flex items-center gap-2">
              <CheckCircle className="w-4 h-4" />
              GSTIN
            </p>
            <p className="text-lg font-bold text-green-900 font-mono">
              {reportData.gstin || 'Not configured'}
            </p>
          </div>
        </div>
      )}

      {/* Validation warnings (serial-length cap, missing GSTIN/state, ...) */}
      {reportData?.validation && !reportData.validation.ok && (
        <div className="card bg-amber-50 border-amber-300">
          <div className="flex gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-amber-900 min-w-0">
              <p className="font-medium mb-1">
                {reportData.validation.issueCount} validation issue
                {reportData.validation.issueCount === 1 ? '' : 's'} - review before
                filing
              </p>
              <ul className="list-disc list-inside space-y-1 text-amber-800">
                {reportData.validation.issues.map((issue, idx) => (
                  <li key={idx} className="break-words">
                    <span className="uppercase text-xs font-semibold mr-1">
                      {issue.level}
                    </span>
                    {issue.issue}
                    {(issue.count ?? 1) > 1 && (
                      <span className="ml-1 font-medium">
                        ({issue.count} invoices)
                      </span>
                    )}
                    {issue.invoice && !issue.issue.includes(issue.invoice) && (
                      <span className="ml-1 font-mono text-xs">
                        [{issue.invoice}]
                      </span>
                    )}
                  </li>
                ))}
              </ul>
              {hasSerialLengthIssue && (
                <p className="mt-2 text-xs text-amber-700">
                  The GSTN portal caps invoice numbers at 16 characters; the GSTN
                  JSON download will warn before generating. Fixing the minted
                  serial format is tracked separately.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Section Tabs */}
      <div className="card">
        <div className="flex border-b border-gray-200">
          <button
            onClick={() => setActiveSection('b2b')}
            className={`px-4 py-3 font-medium text-sm transition-colors ${
              activeSection === 'b2b'
                ? 'border-b-2 border-purple-600 text-purple-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            B2B Invoices
            {reportData && <span className="ml-2 text-xs bg-gray-100 px-2 py-1 rounded-full">{reportData.b2b.length}</span>}
          </button>
          <button
            onClick={() => setActiveSection('b2cl')}
            className={`px-4 py-3 font-medium text-sm transition-colors ${
              activeSection === 'b2cl'
                ? 'border-b-2 border-purple-600 text-purple-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            B2C Large
            {reportData && <span className="ml-2 text-xs bg-gray-100 px-2 py-1 rounded-full">{reportData.b2cl.length}</span>}
          </button>
          <button
            onClick={() => setActiveSection('b2cs')}
            className={`px-4 py-3 font-medium text-sm transition-colors ${
              activeSection === 'b2cs'
                ? 'border-b-2 border-purple-600 text-purple-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            B2C Small (Summary)
            {reportData && <span className="ml-2 text-xs bg-gray-100 px-2 py-1 rounded-full">{reportData.b2cs.length}</span>}
          </button>
        </div>

        <div className="p-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
            </div>
          ) : (
            <>
              {activeSection === 'b2b' && renderB2BTable()}
              {activeSection === 'b2cl' && renderB2CLTable()}
              {activeSection === 'b2cs' && renderB2CSTable()}
            </>
          )}
        </div>
      </div>

      {/* Info Banner */}
      <div className="card bg-blue-50 border-blue-200">
        <div className="flex gap-3">
          <AlertCircle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-blue-900">
            <p className="font-medium mb-1">GSTR-1 Filing Information</p>
            <ul className="list-disc list-inside space-y-1 text-blue-800">
              <li><strong>B2B:</strong> All invoices to registered businesses (with GSTIN)</li>
              <li><strong>B2C Large:</strong> Invoices to consumers with value &gt; ₹2.5 lakh</li>
              <li><strong>B2C Small:</strong> Consolidated summary of invoices ≤ ₹2.5 lakh</li>
              <li><strong>Filing Due Date:</strong> 11th of next month (monthly) or 13th of month after quarter (quarterly)</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

export default GSTR1Report;
