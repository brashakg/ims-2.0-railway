// ============================================================================
// IMS 2.0 - Data Export Button Component
// ============================================================================
// Button component for exporting table data in various formats

import { useState } from 'react';
import { Download, ChevronDown } from 'lucide-react';
import clsx from 'clsx';
import { exportToCSV } from '../../utils/exportUtils';

export type ExportFormat = 'csv' | 'excel' | 'json';

interface DataExportButtonProps {
  data: Record<string, any>[];
  filename: string;
  selectedIds?: string[];
  idKey?: string;
  columns?: { key: string; label: string }[];
  disabled?: boolean;
}

export function DataExportButton({
  data,
  filename,
  selectedIds,
  idKey = 'id',
  columns,
  disabled = false,
}: DataExportButtonProps) {
  const [showMenu, setShowMenu] = useState(false);

  const handleExport = (format: ExportFormat) => {
    const exportData = selectedIds && selectedIds.length > 0
      ? data.filter(item => selectedIds.includes(String(item[idKey])))
      : data;

    if (exportData.length === 0) {
      alert('No data available to export');
      setShowMenu(false);
      return;
    }

    const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
    const filenameWithTime = `${filename}_${timestamp}`;

    switch (format) {
      case 'csv':
        exportToCSV(exportData, filenameWithTime, columns);
        break;
      case 'excel':
        // Excel export using TSV format that Excel can read natively
        const tsvContent = convertToTSV(exportData, columns);
        const blob = new Blob([tsvContent], { type: 'application/vnd.ms-excel;charset=utf-8;' });
        downloadBlob(blob, `${filenameWithTime}.xls`);
        break;
      case 'json':
        const jsonContent = JSON.stringify(exportData, null, 2);
        const jsonBlob = new Blob([jsonContent], { type: 'application/json;charset=utf-8;' });
        downloadBlob(jsonBlob, `${filenameWithTime}.json`);
        break;
    }

    setShowMenu(false);
  };

  const getButtonText = () => {
    if (selectedIds && selectedIds.length > 0) {
      return `Export (${selectedIds.length} selected)`;
    }
    return `Export (${data.length} rows)`;
  };

  return (
    <div className="relative">
      <button
        onClick={() => setShowMenu(!showMenu)}
        disabled={disabled || data.length === 0}
        className={clsx(
          'inline-flex items-center gap-2 px-3 py-2 rounded-lg font-medium transition-colors',
          disabled || data.length === 0
            ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
            : 'bg-blue-50 text-blue-700 hover:bg-blue-100 active:bg-blue-200'
        )}
        aria-label="Export data"
        aria-haspopup="menu"
        aria-expanded={showMenu}
      >
        <Download className="w-4 h-4" />
        <span className="text-sm">{getButtonText()}</span>
        <ChevronDown className={clsx('w-4 h-4 transition-transform', showMenu && 'rotate-180')} />
      </button>

      {showMenu && (
        <div
          className="absolute right-0 mt-2 w-48 bg-white border border-gray-200 rounded-lg shadow-lg z-50"
          role="menu"
          aria-orientation="vertical"
        >
          <button
            onClick={() => handleExport('csv')}
            className="w-full text-left px-4 py-3 text-sm text-gray-700 hover:bg-gray-50 first:rounded-t-lg transition-colors"
            role="menuitem"
          >
            <div className="font-medium">Export as CSV</div>
            <div className="text-xs text-gray-500">Best for spreadsheets</div>
          </button>
          <button
            onClick={() => handleExport('excel')}
            className="w-full text-left px-4 py-3 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
            role="menuitem"
          >
            <div className="font-medium">Export as Excel</div>
            <div className="text-xs text-gray-500">Compatible with Office</div>
          </button>
          <button
            onClick={() => handleExport('json')}
            className="w-full text-left px-4 py-3 text-sm text-gray-700 hover:bg-gray-50 last:rounded-b-lg transition-colors"
            role="menuitem"
          >
            <div className="font-medium">Export as JSON</div>
            <div className="text-xs text-gray-500">For data integration</div>
          </button>
        </div>
      )}
    </div>
  );
}

function convertToTSV(
  data: Record<string, any>[],
  columns?: { key: string; label: string }[]
): string {
  if (data.length === 0) return '';

  const cols = columns || Object.keys(data[0]).map(key => ({ key, label: key }));
  const header = cols.map(c => c.label).join('\t');
  const rows = data.map(row =>
    cols
      .map(c => {
        const val = row[c.key];
        if (val === null || val === undefined) return '';
        return String(val).replace(/\t/g, '  ').replace(/\n/g, ' ');
      })
      .join('\t')
  );

  return [header, ...rows].join('\n');
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
