"use client";

import { useState, useRef } from "react";
import * as XLSX from "xlsx";
import {
  HardDriveDownload,
  Upload,
  Download,
  CheckCircle,
  AlertTriangle,
  Loader2,
  RefreshCw,
} from "lucide-react";

interface StockItem {
  sku: string;
  barcode?: string;
  quantity: number;
}

interface RestoreResult {
  success: boolean;
  summary: {
    totalProducts?: number;
    totalItems?: number;
    matched: number;
    updated: number;
    notFound: number;
    errors: number;
  };
  notFoundSkus: string[];
  errors: string[];
}

type ActiveTab = "backup" | "restore" | "stock";

export default function BackupRestorePage() {
  const [activeTab, setActiveTab] = useState<ActiveTab>("backup");

  // Backup state
  const [backingUp, setBackingUp] = useState(false);
  const [backupDone, setBackupDone] = useState(false);

  // Restore state
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [restoreResult, setRestoreResult] = useState<RestoreResult | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const restoreRef = useRef<HTMLInputElement>(null);

  // Stock import state
  const [stockItems, setStockItems] = useState<StockItem[]>([]);
  const [stockFileName, setStockFileName] = useState<string>("");
  const [parsing, setParsing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [stockResult, setStockResult] = useState<RestoreResult | null>(null);
  const [stockError, setStockError] = useState<string | null>(null);
  const stockRef = useRef<HTMLInputElement>(null);

  // ── BACKUP ─────────────────────────────────────────────────
  const handleBackup = async () => {
    setBackingUp(true);
    setBackupDone(false);
    try {
      const res = await fetch("/api/backup");
      if (!res.ok) throw new Error("Backup failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const today = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `bv-backup-${today}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setBackupDone(true);
    } catch {
      alert("Backup failed. Please try again.");
    } finally {
      setBackingUp(false);
    }
  };

  // ── RESTORE ────────────────────────────────────────────────
  const handleRestoreFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setRestoreFile(file);
      setRestoreResult(null);
      setRestoreError(null);
    }
  };

  const handleRestore = async () => {
    if (!restoreFile) return;
    setRestoring(true);
    setRestoreError(null);
    setRestoreResult(null);
    try {
      const text = await restoreFile.text();
      const data = JSON.parse(text);
      if (!data.products || !Array.isArray(data.products)) {
        throw new Error("Invalid backup file. Expected a BV backup JSON with a products array.");
      }
      const res = await fetch("/api/backup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ products: data.products, restoreInventory: true }),
      });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error || "Restore failed");
      setRestoreResult(result);
    } catch (err: any) {
      setRestoreError(err.message || "Restore failed");
    } finally {
      setRestoring(false);
    }
  };

  // ── STOCK IMPORT FROM EXCEL ────────────────────────────────
  const handleStockFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setStockError(null);
    setStockResult(null);
    setParsing(true);
    setStockFileName(file.name);
    try {
      const data = await file.arrayBuffer();
      const workbook = XLSX.read(data);
      const allItems: StockItem[] = [];
      for (const sheetName of workbook.SheetNames) {
        const sheet = workbook.Sheets[sheetName];
        const rows: any[][] = XLSX.utils.sheet_to_json(sheet, { header: 1 });
        if (rows.length < 2) continue;
        const header = rows[0];
        let skuIdx = -1;
        let qtyIdx = -1;
        let barcodeIdx = -1;
        for (let i = 0; i < header.length; i++) {
          const h = String(header[i] || "").trim().toUpperCase();
          if (h === "SKU" && skuIdx === -1) skuIdx = i;
          if ((h === "QTY" || h === "QUANTITY") && qtyIdx === -1) qtyIdx = i;
          if ((h === "BARCODE" || h === "BAR CODE" || h === "BAR_CODE") && barcodeIdx === -1) barcodeIdx = i;
        }
        // Need at least one identifier column (SKU or Barcode) + quantity
        if (skuIdx === -1 && barcodeIdx === -1) continue;
        if (qtyIdx === -1) continue;
        for (let r = 1; r < rows.length; r++) {
          const row = rows[r];
          const sku = skuIdx !== -1 ? String(row[skuIdx] || "").trim() : "";
          const barcode = barcodeIdx !== -1 ? String(row[barcodeIdx] || "").trim() : "";
          if (!sku && !barcode) continue;
          const qty = Number(row[qtyIdx]) || 0;
          allItems.push({ sku: sku || barcode, barcode: barcode || undefined, quantity: qty });
        }
      }
      setStockItems(allItems);
    } catch (err: any) {
      setStockError(`Failed to parse file: ${err.message}`);
    } finally {
      setParsing(false);
    }
  };

  const handleStockImport = async () => {
    if (stockItems.length === 0) return;
    setImporting(true);
    setStockError(null);
    setStockResult(null);
    try {
      const res = await fetch("/api/stock/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: stockItems }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Import failed");
      setStockResult(data);
    } catch (err: any) {
      setStockError(err.message || "Import failed");
    } finally {
      setImporting(false);
    }
  };

  const tabs: { id: ActiveTab; label: string; icon: React.ReactNode }[] = [
    { id: "backup", label: "Backup Data", icon: <Download className="w-4 h-4" /> },
    { id: "restore", label: "Restore Data", icon: <RefreshCw className="w-4 h-4" /> },
    { id: "stock", label: "Stock Import (Excel)", icon: <Upload className="w-4 h-4" /> },
  ];

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <HardDriveDownload className="w-7 h-7 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Backup & Restore</h1>
            <p className="text-slate-500 text-sm">
              Export your app data for safekeeping, restore from a previous backup, or import stock quantities from Excel.
            </p>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 bg-slate-200 rounded-lg p-1 mb-6">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                activeTab === tab.id
                  ? "bg-white text-blue-700 shadow"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>

        {/* ── BACKUP TAB ── */}
        {activeTab === "backup" && (
          <div className="bg-white rounded-xl shadow p-6">
            <h2 className="text-lg font-semibold text-slate-900 mb-2">Export Full Backup</h2>
            <p className="text-slate-500 text-sm mb-6">
              Downloads all product data (titles, brands, attributes, inventory, variants, images) as a JSON file.
              Save this file securely — it can be used to restore your data later.
            </p>
            <div className="bg-blue-50 rounded-lg p-4 mb-6 text-sm text-blue-800">
              <strong>What's included:</strong> All products, SKUs, brands, attributes, frame specs, pricing, tags, images, variant details, and inventory quantities.
            </div>
            <button
              onClick={handleBackup}
              disabled={backingUp}
              className="flex items-center gap-2 px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition font-medium"
            >
              {backingUp ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Download className="w-4 h-4" />
              )}
              {backingUp ? "Preparing backup..." : "Download Backup (JSON)"}
            </button>
            {backupDone && (
              <div className="mt-4 flex items-center gap-2 text-green-700 text-sm">
                <CheckCircle className="w-4 h-4" />
                Backup downloaded successfully!
              </div>
            )}
          </div>
        )}

        {/* ── RESTORE TAB ── */}
        {activeTab === "restore" && (
          <div className="bg-white rounded-xl shadow p-6">
            <h2 className="text-lg font-semibold text-slate-900 mb-2">Restore from Backup</h2>
            <p className="text-slate-500 text-sm mb-4">
              Upload a previously exported BV backup JSON file. This will restore inventory quantities for matched products.
            </p>
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6 text-sm text-amber-800">
              <strong>Note:</strong> Restore only updates inventory quantities. It does not overwrite product names, descriptions, or other attributes.
            </div>

            <input
              ref={restoreRef}
              type="file"
              accept=".json"
              onChange={handleRestoreFileChange}
              className="hidden"
            />
            <div className="flex items-center gap-4 mb-6">
              <button
                onClick={() => restoreRef.current?.click()}
                disabled={restoring}
                className="px-5 py-2.5 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 transition font-medium"
              >
                Choose Backup File (.json)
              </button>
              {restoreFile && (
                <span className="text-slate-600 text-sm">{restoreFile.name}</span>
              )}
            </div>

            {restoreFile && !restoreResult && (
              <button
                onClick={handleRestore}
                disabled={restoring}
                className="flex items-center gap-2 px-6 py-3 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition font-medium"
              >
                {restoring ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4" />
                )}
                {restoring ? "Restoring..." : "Restore Now"}
              </button>
            )}

            {restoreError && (
              <div className="mt-4 bg-red-50 border border-red-200 rounded-lg p-3 text-red-700 text-sm flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                {restoreError}
              </div>
            )}

            {restoreResult && (
              <div className="mt-4 bg-green-50 border border-green-200 rounded-lg p-4">
                <div className="flex items-center gap-2 text-green-800 font-semibold mb-3">
                  <CheckCircle className="w-5 h-5" />
                  Restore Completed
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                  <div className="text-center">
                    <div className="text-lg font-bold text-slate-900">{restoreResult.summary.totalProducts || restoreResult.summary.totalItems}</div>
                    <div className="text-slate-500">Total</div>
                  </div>
                  <div className="text-center">
                    <div className="text-lg font-bold text-green-700">{restoreResult.summary.matched}</div>
                    <div className="text-slate-500">Matched</div>
                  </div>
                  <div className="text-center">
                    <div className="text-lg font-bold text-blue-700">{restoreResult.summary.updated}</div>
                    <div className="text-slate-500">Updated</div>
                  </div>
                  <div className="text-center">
                    <div className="text-lg font-bold text-amber-700">{restoreResult.summary.notFound}</div>
                    <div className="text-slate-500">Not Found</div>
                  </div>
                </div>
                {restoreResult.notFoundSkus.length > 0 && (
                  <div className="mt-3">
                    <p className="text-xs text-slate-600 mb-1">SKUs not found (first 20):</p>
                    <div className="bg-amber-50 rounded p-2 text-xs font-mono text-amber-800 max-h-28 overflow-y-auto">
                      {restoreResult.notFoundSkus.map((sku, i) => <div key={i}>{sku}</div>)}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── STOCK IMPORT TAB ── */}
        {activeTab === "stock" && (
          <div className="bg-white rounded-xl shadow p-6">
            <h2 className="text-lg font-semibold text-slate-900 mb-2">Import Stock Quantities (Excel)</h2>
            <p className="text-slate-500 text-sm mb-6">
              Upload an Excel file with SKU (or Barcode) and QTY columns to bulk update inventory quantities.
            </p>

            <input
              ref={stockRef}
              type="file"
              accept=".xlsx,.xls,.csv"
              onChange={handleStockFileUpload}
              className="hidden"
            />
            <div className="flex items-center gap-4 mb-6">
              <button
                onClick={() => stockRef.current?.click()}
                disabled={parsing || importing}
                className="px-5 py-2.5 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 disabled:opacity-50 transition font-medium"
              >
                {parsing ? "Parsing..." : "Choose Excel File"}
              </button>
              {stockFileName && (
                <span className="text-slate-600 text-sm">{stockFileName}</span>
              )}
            </div>

            {stockItems.length > 0 && !stockResult && (
              <>
                <div className="grid grid-cols-3 gap-4 mb-4">
                  <div className="bg-blue-50 rounded-lg p-3 text-center">
                    <div className="text-xl font-bold text-blue-700">{stockItems.length}</div>
                    <div className="text-xs text-blue-600">Total Products</div>
                  </div>
                  <div className="bg-green-50 rounded-lg p-3 text-center">
                    <div className="text-xl font-bold text-green-700">{stockItems.filter((i) => i.quantity > 0).length}</div>
                    <div className="text-xs text-green-600">With Stock &gt; 0</div>
                  </div>
                  <div className="bg-amber-50 rounded-lg p-3 text-center">
                    <div className="text-xl font-bold text-amber-700">{stockItems.filter((i) => i.quantity === 0).length}</div>
                    <div className="text-xs text-amber-600">Zero Stock</div>
                  </div>
                </div>

                <div className="overflow-auto max-h-60 border border-slate-200 rounded-lg mb-4">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-100 sticky top-0">
                      <tr>
                        <th className="text-left px-3 py-2 text-slate-700">#</th>
                        <th className="text-left px-3 py-2 text-slate-700">SKU / Barcode</th>
                        <th className="text-right px-3 py-2 text-slate-700">Qty</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {stockItems.slice(0, 50).map((item, idx) => (
                        <tr key={idx} className="hover:bg-slate-50">
                          <td className="px-3 py-1.5 text-slate-400">{idx + 1}</td>
                          <td className="px-3 py-1.5 font-mono text-slate-900">{item.sku}</td>
                          <td className={`px-3 py-1.5 text-right font-medium ${item.quantity > 0 ? "text-green-600" : "text-slate-400"}`}>
                            {item.quantity}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {stockItems.length > 50 && (
                    <div className="text-center py-2 text-xs text-slate-500 bg-slate-50">
                      Showing 50 of {stockItems.length} items
                    </div>
                  )}
                </div>

                <button
                  onClick={handleStockImport}
                  disabled={importing}
                  className="flex items-center gap-2 px-6 py-3 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition font-medium"
                >
                  {importing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                  {importing ? "Importing..." : `Import ${stockItems.length} Items`}
                </button>
              </>
            )}

            {stockError && (
              <div className="mt-4 bg-red-50 border border-red-200 rounded-lg p-3 text-red-700 text-sm flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                {stockError}
              </div>
            )}

            {stockResult && (
              <div className="mt-4 bg-green-50 border border-green-200 rounded-lg p-4">
                <div className="flex items-center gap-2 text-green-800 font-semibold mb-3">
                  <CheckCircle className="w-5 h-5" />
                  Import Completed
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                  <div className="text-center">
                    <div className="text-lg font-bold text-slate-900">{stockResult.summary.totalItems || stockResult.summary.totalProducts}</div>
                    <div className="text-slate-500">Total</div>
                  </div>
                  <div className="text-center">
                    <div className="text-lg font-bold text-green-700">{stockResult.summary.matched}</div>
                    <div className="text-slate-500">Matched</div>
                  </div>
                  <div className="text-center">
                    <div className="text-lg font-bold text-blue-700">{stockResult.summary.updated}</div>
                    <div className="text-slate-500">Updated</div>
                  </div>
                  <div className="text-center">
                    <div className="text-lg font-bold text-amber-700">{stockResult.summary.notFound}</div>
                    <div className="text-slate-500">Not Found</div>
                  </div>
                </div>
                {stockResult.notFoundSkus.length > 0 && (
                  <div className="mt-3">
                    <p className="text-xs text-slate-600 mb-1">SKUs not found (first 20):</p>
                    <div className="bg-amber-50 rounded p-2 text-xs font-mono text-amber-800 max-h-28 overflow-y-auto">
                      {stockResult.notFoundSkus.map((sku, i) => <div key={i}>{sku}</div>)}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
