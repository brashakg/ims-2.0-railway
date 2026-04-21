"use client";

import { useState, useEffect, useRef } from "react";
import { Trash2, Download, Upload, MapPin, Loader } from "lucide-react";

interface ScannedItem {
  barcode: string;
  quantity: number;
}

interface ComparisonItem {
  variantId: string;
  barcode: string;
  variantTitle: string | null;
  productTitle: string | null;
  brand: string;
  category: string;
  physicalQty: number;
  onlineQty: number;
  difference: number;
  status: "match" | "minor" | "major";
}

interface BreakdownItem {
  name: string;
  totalPhysical: number;
  totalOnline: number;
  excess: number;
  deficit: number;
}

interface TallyResult {
  comparisonData: ComparisonItem[];
  unmatchedBarcodes: string[];
  brandBreakdown: BreakdownItem[];
  categoryBreakdown: BreakdownItem[];
  summary: {
    totalScanned: number;
    uniqueBarcodes: number;
    matchedVariants: number;
    unmatchedCount: number;
    matchingItems: number;
    minorDifferences: number;
    majorDifferences: number;
    totalVariance: number;
  };
}

interface Location {
  id: string;
  name: string;
  code: string;
}

export default function StockTallyPage() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [selectedLocation, setSelectedLocation] = useState("");
  const [barcodeInput, setBarcodeInput] = useState("");
  const [scannedItems, setScannedItems] = useState<ScannedItem[]>([]);
  const [result, setResult] = useState<TallyResult | null>(null);
  const [comparing, setComparing] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [reconcileMsg, setReconcileMsg] = useState<string | null>(null);
  const barcodeInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch("/api/locations?excludeSynthetic=true")
      .then((r) => r.json())
      .then((data) => setLocations(Array.isArray(data) ? data : data.data || []))
      .catch(console.error);
  }, []);

  useEffect(() => {
    if (!comparing) barcodeInputRef.current?.focus();
  }, [comparing]);

  const handleBarcodeScan = (barcode: string) => {
    const bc = barcode.trim();
    if (!bc) return;
    setScannedItems((prev) => {
      const existing = prev.find((i) => i.barcode === bc);
      if (existing) return prev.map((i) => (i.barcode === bc ? { ...i, quantity: i.quantity + 1 } : i));
      return [...prev, { barcode: bc, quantity: 1 }];
    });
    setBarcodeInput("");
  };

  const handleBarcodeKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      handleBarcodeScan(barcodeInput);
    }
  };

  const removeItem = (barcode: string) => setScannedItems((prev) => prev.filter((i) => i.barcode !== barcode));

  const clearAll = () => {
    setScannedItems([]);
    setResult(null);
    setBarcodeInput("");
    barcodeInputRef.current?.focus();
  };

  const compareStock = async () => {
    if (!selectedLocation || scannedItems.length === 0) {
      alert("Please select a location and scan at least one barcode");
      return;
    }
    try {
      setComparing(true);
      const res = await fetch("/api/stock-tally", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ locationId: selectedLocation, barcodes: scannedItems }),
      });
      const json = await res.json();
      if (json.success) setResult(json.data);
      else alert(json.error || "Error comparing stock");
    } catch (err) {
      console.error(err);
      alert("Error comparing stock");
    } finally {
      setComparing(false);
    }
  };

  const handleCSVUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    const lines = text.split("\n").filter((l) => l.trim());
    const newItems: ScannedItem[] = [];
    for (const line of lines) {
      const [barcode, qtyStr] = line.split(",").map((s) => s.trim());
      if (barcode && barcode !== "barcode") newItems.push({ barcode, quantity: parseInt(qtyStr) || 1 });
    }
    setScannedItems((prev) => {
      const map = new Map<string, number>();
      for (const i of prev) map.set(i.barcode, (map.get(i.barcode) || 0) + i.quantity);
      for (const i of newItems) map.set(i.barcode, (map.get(i.barcode) || 0) + i.quantity);
      return Array.from(map.entries()).map(([barcode, quantity]) => ({ barcode, quantity }));
    });
  };

  const exportCSV = () => {
    if (!result) return;
    const loc = locations.find((l) => l.id === selectedLocation);
    let csv = `Stock Tally Report\nLocation: ${loc?.name || selectedLocation}\nDate: ${new Date().toLocaleDateString()}\n\n`;
    csv += "PRODUCT COMPARISON\nBarcode,Product,Variant,Brand,Product Type,Physical,Online,Difference,Status\n";
    for (const i of result.comparisonData) {
      csv += `${i.barcode},${i.productTitle},${i.variantTitle || ""},${i.brand},${i.category},${i.physicalQty},${i.onlineQty},${i.difference},${i.status}\n`;
    }
    if (result.unmatchedBarcodes.length > 0) {
      csv += `\nUNMATCHED BARCODES\n${result.unmatchedBarcodes.join("\n")}\n`;
    }
    csv += "\nBRAND BREAKDOWN\nBrand,Physical,Online,Excess,Deficit\n";
    for (const b of result.brandBreakdown) csv += `${b.name},${b.totalPhysical},${b.totalOnline},${b.excess},${b.deficit}\n`;
    csv += "\nPRODUCT TYPE BREAKDOWN\nProduct Type,Physical,Online,Excess,Deficit\n";
    for (const c of result.categoryBreakdown) csv += `${c.name},${c.totalPhysical},${c.totalOnline},${c.excess},${c.deficit}\n`;

    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `stock-tally-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleReconcile = async () => {
    if (!result || !selectedLocation) return;
    // Only reconcile items with differences
    const adjustments = result.comparisonData
      .filter((i) => i.difference !== 0)
      .map((i) => ({ variantId: i.variantId, newQuantity: i.physicalQty }));

    if (adjustments.length === 0) {
      setReconcileMsg("No differences to reconcile.");
      return;
    }

    if (!confirm(`Update ${adjustments.length} variant(s) to match physical counts?`)) return;

    setReconciling(true);
    setReconcileMsg(null);
    try {
      const res = await fetch("/api/stock-tally/reconcile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ locationId: selectedLocation, adjustments }),
      });
      const data = await res.json();
      if (data.success) {
        setReconcileMsg(`Reconciled: ${data.summary.localUpdated} variant(s) updated.`);
      } else {
        setReconcileMsg(`Error: ${data.error}`);
      }
    } catch {
      setReconcileMsg("Reconcile request failed.");
    } finally {
      setReconciling(false);
    }
  };

  const statusColor = (s: string) =>
    s === "match" ? "bg-green-100 text-green-800" : s === "minor" ? "bg-yellow-100 text-yellow-800" : "bg-red-100 text-red-800";

  const totalScanned = scannedItems.reduce((s, i) => s + i.quantity, 0);

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-slate-900">Barcode Stock Tally</h1>
          <p className="text-slate-500 text-sm mt-1">Scan product barcodes to compare physical stock with online inventory</p>
        </div>

        {/* Controls */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1.5">
                <MapPin className="inline mr-1" size={14} /> Location
              </label>
              <select
                value={selectedLocation}
                onChange={(e) => setSelectedLocation(e.target.value)}
                disabled={comparing}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="">Select location...</option>
                {locations.map((loc) => (
                  <option key={loc.id} value={loc.id}>
                    {loc.name} ({loc.code})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1.5">Scan Barcode</label>
              <div className="flex gap-2">
                <input
                  ref={barcodeInputRef}
                  type="text"
                  value={barcodeInput}
                  onChange={(e) => setBarcodeInput(e.target.value)}
                  onKeyDown={handleBarcodeKeyDown}
                  placeholder="Scan or type barcode..."
                  disabled={!selectedLocation || comparing}
                  className="flex-1 px-3 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:opacity-50"
                />
                <button
                  onClick={() => handleBarcodeScan(barcodeInput)}
                  disabled={!selectedLocation || !barcodeInput || comparing}
                  className="px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
                >
                  Add
                </button>
              </div>
            </div>
            <div className="flex items-end gap-2">
              <div className="text-center">
                <p className="text-3xl font-bold text-blue-600">{totalScanned}</p>
                <p className="text-xs text-slate-500">{scannedItems.length} unique</p>
              </div>
            </div>
          </div>

          {/* CSV upload + actions */}
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 px-3 py-2 border border-dashed border-slate-300 rounded-lg cursor-pointer hover:border-blue-500 text-sm text-slate-600">
              <Upload size={16} /> Upload CSV
              <input type="file" accept=".csv" onChange={handleCSVUpload} disabled={comparing} className="hidden" />
            </label>
            <button
              onClick={compareStock}
              disabled={!selectedLocation || scannedItems.length === 0 || comparing}
              className="px-5 py-2 bg-green-600 text-white font-medium rounded-lg hover:bg-green-700 disabled:opacity-50 flex items-center gap-2 text-sm"
            >
              {comparing && <Loader className="animate-spin" size={16} />}
              {comparing ? "Comparing..." : "Compare Stock"}
            </button>
            <button
              onClick={clearAll}
              disabled={scannedItems.length === 0 || comparing}
              className="px-4 py-2 bg-slate-200 text-slate-700 rounded-lg hover:bg-slate-300 disabled:opacity-50 flex items-center gap-2 text-sm"
            >
              <Trash2 size={16} /> Clear
            </button>
          </div>
        </div>

        {/* Scanned items */}
        {scannedItems.length > 0 && !result && (
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">
            <h2 className="text-sm font-semibold text-slate-700 mb-3">Scanned Barcodes ({scannedItems.length})</h2>
            <div className="max-h-60 overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white">
                  <tr className="border-b">
                    <th className="text-left py-2 px-3 font-medium text-slate-600">Barcode</th>
                    <th className="text-right py-2 px-3 font-medium text-slate-600">Qty</th>
                    <th className="text-center py-2 px-3 w-16"></th>
                  </tr>
                </thead>
                <tbody>
                  {scannedItems.map((item) => (
                    <tr key={item.barcode} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="py-2 px-3 font-mono text-slate-800">{item.barcode}</td>
                      <td className="py-2 px-3 text-right font-semibold">{item.quantity}</td>
                      <td className="py-2 px-3 text-center">
                        <button onClick={() => removeItem(item.barcode)} className="text-red-500 hover:text-red-700 text-xs">
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Results */}
        {result && (
          <div className="space-y-6">
            {/* Summary cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-white rounded-xl shadow-sm border p-4 text-center">
                <p className="text-2xl font-bold text-green-600">{result.summary.matchingItems}</p>
                <p className="text-xs text-slate-500">Matching</p>
              </div>
              <div className="bg-white rounded-xl shadow-sm border p-4 text-center">
                <p className="text-2xl font-bold text-yellow-600">{result.summary.minorDifferences}</p>
                <p className="text-xs text-slate-500">Minor (&le;3)</p>
              </div>
              <div className="bg-white rounded-xl shadow-sm border p-4 text-center">
                <p className="text-2xl font-bold text-red-600">{result.summary.majorDifferences}</p>
                <p className="text-xs text-slate-500">Major (&gt;3)</p>
              </div>
              <div className="bg-white rounded-xl shadow-sm border p-4 text-center">
                <p className="text-2xl font-bold text-slate-800">{result.summary.totalVariance}</p>
                <p className="text-xs text-slate-500">Total Variance</p>
              </div>
            </div>

            {/* Unmatched barcodes warning */}
            {result.unmatchedBarcodes.length > 0 && (
              <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
                <p className="font-semibold text-amber-800 text-sm mb-1">
                  {result.unmatchedBarcodes.length} barcodes not found in system
                </p>
                <p className="text-xs text-amber-700 font-mono">{result.unmatchedBarcodes.join(", ")}</p>
              </div>
            )}

            {reconcileMsg && (
              <div className={`p-3 rounded-lg text-sm mb-4 ${reconcileMsg.startsWith("Error") ? "bg-red-50 text-red-700 border border-red-200" : "bg-green-50 text-green-700 border border-green-200"}`}>
                {reconcileMsg}
              </div>
            )}

            {/* Comparison table */}
            <div className="bg-white rounded-xl shadow-sm border p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold text-slate-700">Product Comparison</h2>
                <div className="flex gap-2">
                  {result.comparisonData.some((i) => i.difference !== 0) && (
                    <button
                      onClick={handleReconcile}
                      disabled={reconciling}
                      className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-lg text-xs hover:bg-green-700 disabled:opacity-50"
                    >
                      {reconciling ? "Reconciling..." : "Reconcile Differences"}
                    </button>
                  )}
                  <button onClick={exportCSV} className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs hover:bg-blue-700">
                    <Download size={14} /> Export CSV
                  </button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-slate-50">
                      <th className="text-left py-2.5 px-3 font-medium text-slate-600">Product</th>
                      <th className="text-left py-2.5 px-3 font-medium text-slate-600">Barcode</th>
                      <th className="text-left py-2.5 px-3 font-medium text-slate-600">Brand</th>
                      <th className="text-left py-2.5 px-3 font-medium text-slate-600">Product Type</th>
                      <th className="text-right py-2.5 px-3 font-medium text-slate-600">Physical</th>
                      <th className="text-right py-2.5 px-3 font-medium text-slate-600">Online</th>
                      <th className="text-right py-2.5 px-3 font-medium text-slate-600">Diff</th>
                      <th className="text-center py-2.5 px-3 font-medium text-slate-600">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.comparisonData.map((item) => (
                      <tr key={item.variantId} className="border-b border-slate-100 hover:bg-slate-50">
                        <td className="py-2.5 px-3">
                          <p className="font-medium text-slate-900 text-xs">{item.productTitle}</p>
                          {item.variantTitle && <p className="text-xs text-slate-500">{item.variantTitle}</p>}
                        </td>
                        <td className="py-2.5 px-3 font-mono text-xs text-slate-700">{item.barcode}</td>
                        <td className="py-2.5 px-3 text-slate-600">{item.brand}</td>
                        <td className="py-2.5 px-3 text-slate-600">{item.category}</td>
                        <td className="py-2.5 px-3 text-right font-semibold">{item.physicalQty}</td>
                        <td className="py-2.5 px-3 text-right font-semibold">{item.onlineQty}</td>
                        <td className={`py-2.5 px-3 text-right font-bold ${item.difference > 0 ? "text-green-600" : item.difference < 0 ? "text-red-600" : "text-slate-600"}`}>
                          {item.difference > 0 ? "+" : ""}
                          {item.difference}
                        </td>
                        <td className="py-2.5 px-3 text-center">
                          <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${statusColor(item.status)}`}>
                            {item.status === "match" ? "Match" : item.status === "minor" ? "Minor" : "Major"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Breakdowns */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Brand */}
              <div className="bg-white rounded-xl shadow-sm border p-5">
                <h2 className="text-sm font-semibold text-slate-700 mb-3">Brand-wise Breakdown</h2>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-slate-50">
                      <th className="text-left py-2 px-3 font-medium text-slate-600">Brand</th>
                      <th className="text-right py-2 px-3 font-medium text-slate-600">Physical</th>
                      <th className="text-right py-2 px-3 font-medium text-slate-600">Online</th>
                      <th className="text-right py-2 px-3 font-medium text-green-700">Excess</th>
                      <th className="text-right py-2 px-3 font-medium text-red-700">Deficit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.brandBreakdown.map((b) => (
                      <tr key={b.name} className="border-b border-slate-100">
                        <td className="py-2 px-3 font-medium">{b.name}</td>
                        <td className="py-2 px-3 text-right">{b.totalPhysical}</td>
                        <td className="py-2 px-3 text-right">{b.totalOnline}</td>
                        <td className="py-2 px-3 text-right text-green-600 font-semibold">{b.excess || "-"}</td>
                        <td className="py-2 px-3 text-right text-red-600 font-semibold">{b.deficit || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Product Type */}
              <div className="bg-white rounded-xl shadow-sm border p-5">
                <h2 className="text-sm font-semibold text-slate-700 mb-3">Product Type Breakdown</h2>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-slate-50">
                      <th className="text-left py-2 px-3 font-medium text-slate-600">Product Type</th>
                      <th className="text-right py-2 px-3 font-medium text-slate-600">Physical</th>
                      <th className="text-right py-2 px-3 font-medium text-slate-600">Online</th>
                      <th className="text-right py-2 px-3 font-medium text-green-700">Excess</th>
                      <th className="text-right py-2 px-3 font-medium text-red-700">Deficit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.categoryBreakdown.map((c) => (
                      <tr key={c.name} className="border-b border-slate-100">
                        <td className="py-2 px-3 font-medium">{c.name}</td>
                        <td className="py-2 px-3 text-right">{c.totalPhysical}</td>
                        <td className="py-2 px-3 text-right">{c.totalOnline}</td>
                        <td className="py-2 px-3 text-right text-green-600 font-semibold">{c.excess || "-"}</td>
                        <td className="py-2 px-3 text-right text-red-600 font-semibold">{c.deficit || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
