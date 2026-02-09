// ============================================================================
// IMS 2.0 - Profit & Loss Statement
// ============================================================================
// Comprehensive financial statement with detailed breakdowns

import { Download, Printer } from 'lucide-react';
import clsx from 'clsx';

interface PLData {
  period: string;
  revenues: {
    grossSales: number;
    discounts: number;
    netSales: number;
  };
  costOfGoodsSold: {
    openingStock: number;
    purchases: number;
    closingStock: number;
    cogs: number;
  };
  grossProfit: number;
  grossProfitMargin: number;
  operatingExpenses: {
    salaries: number;
    rent: number;
    utilities: number;
    marketing: number;
    maintenance: number;
    otherExpenses: number;
    totalOperatingExpenses: number;
  };
  ebitda: number;
  ebitdaMargin: number;
  depreciation: number;
  interestExpense: number;
  ebt: number;
  taxExpense: number;
  netProfit: number;
  netProfitMargin: number;
}

interface ProfitLossStatementProps {
  data: PLData;
  onExport?: () => void;
  onPrint?: () => void;
}

export function ProfitLossStatement({
  data,
  onExport,
  onPrint,
}: ProfitLossStatementProps) {
  const formatCurrency = (amount: number) =>
    new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      minimumFractionDigits: 2,
    }).format(amount);

  const formatPercentage = (value: number) => `${value.toFixed(2)}%`;

  const LineItem = ({
    label,
    amount,
    indent = 0,
    bold = false,
    highlight = false,
  }: {
    label: string;
    amount: number;
    indent?: number;
    bold?: boolean;
    highlight?: boolean;
  }) => (
    <div
      className={clsx(
        'flex justify-between py-2 px-4 border-b border-gray-200',
        indent > 0 && 'ml-4',
        bold && 'font-semibold',
        highlight && 'bg-blue-50'
      )}
    >
      <span className="text-gray-700">{label}</span>
      <span className={clsx('font-medium', amount < 0 && 'text-red-600')}>
        {formatCurrency(amount)}
      </span>
    </div>
  );

  const SectionHeader = ({ title }: { title: string }) => (
    <div className="bg-gray-100 px-4 py-3 font-bold text-gray-900 border-b-2 border-gray-300">
      {title}
    </div>
  );

  const MetricBox = ({
    label,
    value,
    percentage,
    type = 'normal',
  }: {
    label: string;
    value: number;
    percentage?: number;
    type?: 'success' | 'warning' | 'danger' | 'normal';
  }) => {
    const bgColor = {
      success: 'bg-green-50 border-green-200',
      warning: 'bg-yellow-50 border-yellow-200',
      danger: 'bg-red-50 border-red-200',
      normal: 'bg-blue-50 border-blue-200',
    }[type];

    const textColor = {
      success: 'text-green-800',
      warning: 'text-yellow-800',
      danger: 'text-red-800',
      normal: 'text-blue-800',
    }[type];

    return (
      <div className={clsx('border rounded-lg p-4', bgColor)}>
        <p className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
          {label}
        </p>
        <p className={clsx('text-2xl font-bold mt-2', textColor)}>
          {formatCurrency(value)}
        </p>
        {percentage !== undefined && (
          <p className={clsx('text-sm font-medium mt-1', textColor)}>
            {formatPercentage(percentage)} margin
          </p>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Profit & Loss Statement</h2>
          <p className="text-sm text-gray-500 mt-1">{data.period}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onPrint}
            className="px-4 py-2 bg-gray-600 hover:bg-gray-700 text-white rounded-lg flex items-center gap-2 transition-colors"
          >
            <Printer className="w-4 h-4" />
            Print
          </button>
          <button
            onClick={onExport}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg flex items-center gap-2 transition-colors"
          >
            <Download className="w-4 h-4" />
            Export
          </button>
        </div>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-4 gap-4">
        <MetricBox
          label="Net Sales"
          value={data.revenues.netSales}
          type="normal"
        />
        <MetricBox
          label="Gross Profit"
          value={data.grossProfit}
          percentage={data.grossProfitMargin}
          type="success"
        />
        <MetricBox
          label="EBITDA"
          value={data.ebitda}
          percentage={data.ebitdaMargin}
          type="success"
        />
        <MetricBox
          label="Net Profit"
          value={data.netProfit}
          percentage={data.netProfitMargin}
          type={data.netProfit >= 0 ? 'success' : 'danger'}
        />
      </div>

      {/* P&L Statement */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {/* Revenue Section */}
        <SectionHeader title="REVENUE" />
        <LineItem label="Gross Sales" amount={data.revenues.grossSales} bold />
        <LineItem label="Less: Discounts" amount={-data.revenues.discounts} indent={1} />
        <LineItem
          label="Net Sales"
          amount={data.revenues.netSales}
          bold
          highlight
        />

        {/* Cost of Goods Sold */}
        <SectionHeader title="COST OF GOODS SOLD" />
        <LineItem label="Opening Stock" amount={data.costOfGoodsSold.openingStock} indent={1} />
        <LineItem label="Add: Purchases" amount={data.costOfGoodsSold.purchases} indent={1} />
        <LineItem label="Less: Closing Stock" amount={-data.costOfGoodsSold.closingStock} indent={1} />
        <LineItem
          label="Cost of Goods Sold"
          amount={data.costOfGoodsSold.cogs}
          bold
          highlight
        />

        {/* Gross Profit */}
        <div className="bg-green-50 border-b-2 border-green-300 px-4 py-2 flex justify-between">
          <span className="font-bold text-green-800">Gross Profit</span>
          <span className="font-bold text-green-800">{formatCurrency(data.grossProfit)}</span>
        </div>
        <div className="bg-green-50 px-4 py-2 flex justify-between border-b">
          <span className="text-sm text-green-700">Gross Profit Margin</span>
          <span className="font-semibold text-green-800">
            {formatPercentage(data.grossProfitMargin)}
          </span>
        </div>

        {/* Operating Expenses */}
        <SectionHeader title="OPERATING EXPENSES" />
        <LineItem label="Salaries & Wages" amount={data.operatingExpenses.salaries} indent={1} />
        <LineItem label="Rent" amount={data.operatingExpenses.rent} indent={1} />
        <LineItem label="Utilities" amount={data.operatingExpenses.utilities} indent={1} />
        <LineItem label="Marketing" amount={data.operatingExpenses.marketing} indent={1} />
        <LineItem label="Maintenance" amount={data.operatingExpenses.maintenance} indent={1} />
        <LineItem label="Other Expenses" amount={data.operatingExpenses.otherExpenses} indent={1} />
        <LineItem
          label="Total Operating Expenses"
          amount={data.operatingExpenses.totalOperatingExpenses}
          bold
          highlight
        />

        {/* EBITDA */}
        <div className="bg-blue-50 border-b-2 border-blue-300 px-4 py-2 flex justify-between">
          <span className="font-bold text-blue-800">EBITDA</span>
          <span className="font-bold text-blue-800">{formatCurrency(data.ebitda)}</span>
        </div>
        <div className="bg-blue-50 px-4 py-2 flex justify-between border-b">
          <span className="text-sm text-blue-700">EBITDA Margin</span>
          <span className="font-semibold text-blue-800">
            {formatPercentage(data.ebitdaMargin)}
          </span>
        </div>

        {/* Finance Charges */}
        <SectionHeader title="FINANCE & TAX" />
        <LineItem label="Depreciation" amount={-data.depreciation} />
        <LineItem label="Interest Expense" amount={-data.interestExpense} />
        <LineItem
          label="Earnings Before Tax"
          amount={data.ebt}
          bold
          highlight
        />
        <LineItem label="Tax Expense (30%)" amount={-data.taxExpense} />

        {/* Net Profit */}
        <div
          className={clsx(
            'px-4 py-3 font-bold text-lg flex justify-between border-t-2',
            data.netProfit >= 0
              ? 'bg-green-100 border-green-400 text-green-800'
              : 'bg-red-100 border-red-400 text-red-800'
          )}
        >
          <span>NET PROFIT / (LOSS)</span>
          <span>{formatCurrency(data.netProfit)}</span>
        </div>
        <div
          className={clsx(
            'px-4 py-2 font-semibold flex justify-between',
            data.netProfit >= 0 ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'
          )}
        >
          <span>Net Profit Margin</span>
          <span>{formatPercentage(data.netProfitMargin)}</span>
        </div>
      </div>

      {/* Financial Ratios */}
      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-3">
          <h4 className="font-semibold text-gray-900">Profitability Ratios</h4>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-600">Gross Margin</span>
              <span className="font-medium text-gray-900">
                {formatPercentage(data.grossProfitMargin)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">EBITDA Margin</span>
              <span className="font-medium text-gray-900">
                {formatPercentage(data.ebitdaMargin)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">Net Margin</span>
              <span className="font-medium text-gray-900">
                {formatPercentage(data.netProfitMargin)}
              </span>
            </div>
          </div>
        </div>

        <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-3">
          <h4 className="font-semibold text-gray-900">Key Metrics</h4>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-600">Revenue</span>
              <span className="font-medium">{formatCurrency(data.revenues.netSales)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">COGS</span>
              <span className="font-medium">{formatCurrency(data.costOfGoodsSold.cogs)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">Operating Exp.</span>
              <span className="font-medium">
                {formatCurrency(data.operatingExpenses.totalOperatingExpenses)}
              </span>
            </div>
          </div>
        </div>

        <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-3">
          <h4 className="font-semibold text-gray-900">Bottom Line</h4>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-600">EBITDA</span>
              <span className="font-medium">{formatCurrency(data.ebitda)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">EBT</span>
              <span className="font-medium">{formatCurrency(data.ebt)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">Net Profit</span>
              <span className={clsx('font-medium', data.netProfit < 0 && 'text-red-600')}>
                {formatCurrency(data.netProfit)}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ProfitLossStatement;
