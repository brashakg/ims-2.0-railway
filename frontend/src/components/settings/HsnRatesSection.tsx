// ============================================================================
// IMS 2.0 - Settings: HSN & GST Rates (SUPERADMIN/ADMIN)
// ============================================================================
// Editable HSN -> GST-rate master. POS billing resolves the GST rate for every
// sale line from this table (backend api/services/gst_rates.py), overriding the
// static canonical table, so when the govt revises GST the owner updates it here
// — no code change / redeploy. Seeded with GST 2.0 rates (eff 22 Sep 2025).

import { useEffect, useState } from 'react';
import { Plus, Trash2, Save, Pencil, X, RefreshCw, AlertCircle, Info } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
// Direct import (barrel re-export gotcha for newly-added services).
import { hsnApi, type HsnRate } from '../../services/api/hsn';
import { loadHsnRates } from '../../constants/gstRuntime';

const CATEGORY_HINTS = [
  'CONTACT_LENS', 'LENS', 'FRAME', 'SPECTACLE', 'SUNGLASSES',
  'WATCH', 'SMARTWATCH', 'ACCESSORIES', 'SERVICE', 'HEARING_AID',
];

interface DraftRow {
  description: string;
  category_hint: string;
  gst_rate: string;
}

export function HsnRatesSection() {
  const toast = useToast();
  const [rows, setRows] = useState<HsnRate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftRow>({ description: '', category_hint: '', gst_rate: '' });

  const [showAdd, setShowAdd] = useState(false);
  const [newRow, setNewRow] = useState({ hsn_code: '', description: '', category_hint: '', gst_rate: '' });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await hsnApi.list();
      setRows(res.hsn_rates || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load HSN rates');
      setRows([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const startEdit = (row: HsnRate) => {
    setEditingId(row.hsn_id);
    setDraft({
      description: row.description || '',
      category_hint: row.category_hint || '',
      gst_rate: String(row.gst_rate ?? ''),
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setDraft({ description: '', category_hint: '', gst_rate: '' });
  };

  const saveEdit = async (row: HsnRate) => {
    const rate = parseFloat(draft.gst_rate);
    if (Number.isNaN(rate) || rate < 0 || rate > 40) {
      toast.error('GST rate must be between 0 and 40');
      return;
    }
    setSaving(true);
    try {
      await hsnApi.update(row.hsn_id, {
        gst_rate: rate,
        description: draft.description || undefined,
        category_hint: draft.category_hint || undefined,
      });
      toast.success(`Updated ${row.hsn_code} -> ${rate}%`);
      cancelEdit();
      await load();
      await loadHsnRates(); // refresh the POS preview cache this session
    } catch {
      toast.error('Failed to update HSN rate');
    } finally {
      setSaving(false);
    }
  };

  const addRow = async () => {
    const rate = parseFloat(newRow.gst_rate);
    if (!newRow.hsn_code.trim()) { toast.error('HSN code is required'); return; }
    if (Number.isNaN(rate) || rate < 0 || rate > 40) { toast.error('GST rate must be between 0 and 40'); return; }
    setSaving(true);
    try {
      await hsnApi.create({
        hsn_code: newRow.hsn_code.trim(),
        gst_rate: rate,
        description: newRow.description || undefined,
        category_hint: newRow.category_hint || undefined,
      });
      toast.success(`Added HSN ${newRow.hsn_code}`);
      setNewRow({ hsn_code: '', description: '', category_hint: '', gst_rate: '' });
      setShowAdd(false);
      await load();
      await loadHsnRates();
    } catch (err: any) {
      const msg = err?.response?.status === 409 ? 'That HSN code already exists' : 'Failed to add HSN rate';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const removeRow = async (row: HsnRate) => {
    if (!window.confirm(`Delete HSN ${row.hsn_code}? POS will then fall back to the canonical rate for its category.`)) return;
    try {
      await hsnApi.remove(row.hsn_id);
      toast.success(`Deleted ${row.hsn_code}`);
      await load();
      await loadHsnRates();
    } catch {
      toast.error('Failed to delete HSN rate');
    }
  };

  return (
    <div className="space-y-4">
      <div className="card" style={{ padding: 12, display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        <Info className="w-5 h-5" style={{ color: 'var(--bv)', flexShrink: 0 }} />
        <p className="text-sm text-gray-600">
          POS billing resolves every sale line&apos;s GST from this table (by HSN code, then product
          category), overriding the built-in defaults. Edit a rate here when the govt revises GST — no
          code change needed. Seeded with GST 2.0 rates (effective 22 Sep 2025): corrective optical 5%,
          sunglasses / watches / accessories 18%. Changes are audit-logged.
        </p>
      </div>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
          <span className="text-sm text-red-700">{error}</span>
          <button onClick={load} className="ml-auto text-sm text-red-600 hover:underline">Retry</button>
        </div>
      )}

      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">HSN &amp; GST Rates ({rows.length})</h2>
          <div className="flex items-center gap-2">
            <button onClick={load} className="btn-outline flex items-center gap-1" title="Reload">
              <RefreshCw className="w-4 h-4" />
              <span className="hidden sm:inline text-sm">Refresh</span>
            </button>
            <button onClick={() => setShowAdd((v) => !v)} className="btn-primary flex items-center gap-1">
              <Plus className="w-4 h-4" /> Add HSN
            </button>
          </div>
        </div>

        {showAdd && (
          <div className="mb-4 p-3 bg-gray-50 rounded-lg border border-gray-200 grid grid-cols-1 sm:grid-cols-5 gap-2 items-end">
            <div>
              <label className="block text-xs text-gray-500 mb-1">HSN Code</label>
              <input className="input-field font-mono" value={newRow.hsn_code}
                onChange={(e) => setNewRow({ ...newRow, hsn_code: e.target.value })} placeholder="900130" />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-xs text-gray-500 mb-1">Description</label>
              <input className="input-field" value={newRow.description}
                onChange={(e) => setNewRow({ ...newRow, description: e.target.value })} placeholder="Contact lenses" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Category</label>
              <select className="input-field" value={newRow.category_hint}
                onChange={(e) => setNewRow({ ...newRow, category_hint: e.target.value })}>
                <option value="">—</option>
                {CATEGORY_HINTS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">GST %</label>
              <div className="flex gap-1">
                <input className="input-field" type="number" min={0} max={40} step={0.5} value={newRow.gst_rate}
                  onChange={(e) => setNewRow({ ...newRow, gst_rate: e.target.value })} placeholder="5" />
                <button onClick={addRow} disabled={saving} className="btn-primary px-2" title="Save">
                  <Save className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        )}

        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">HSN Code</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Description</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Category</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">GST %</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {loading ? (
                <tr><td colSpan={5} className="px-4 py-12 text-center text-gray-500">
                  <RefreshCw className="w-6 h-6 mx-auto animate-spin" />
                </td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={5} className="px-4 py-12 text-center text-gray-500">No HSN rates configured.</td></tr>
              ) : (
                rows.map((row) => {
                  const isEditing = editingId === row.hsn_id;
                  return (
                    <tr key={row.hsn_id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-mono text-sm text-gray-900">{row.hsn_code}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {isEditing ? (
                          <input className="input-field" value={draft.description}
                            onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                        ) : (row.description || '—')}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {isEditing ? (
                          <select className="input-field" value={draft.category_hint}
                            onChange={(e) => setDraft({ ...draft, category_hint: e.target.value })}>
                            <option value="">—</option>
                            {CATEGORY_HINTS.map((c) => <option key={c} value={c}>{c}</option>)}
                          </select>
                        ) : (row.category_hint || '—')}
                      </td>
                      <td className="px-4 py-3 text-right text-sm font-semibold text-gray-900">
                        {isEditing ? (
                          <input className="input-field text-right w-24 ml-auto" type="number" min={0} max={40} step={0.5}
                            value={draft.gst_rate} onChange={(e) => setDraft({ ...draft, gst_rate: e.target.value })} />
                        ) : `${row.gst_rate}%`}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        {isEditing ? (
                          <div className="flex items-center justify-end gap-1">
                            <button onClick={() => saveEdit(row)} disabled={saving}
                              className="p-1.5 rounded hover:bg-green-100 text-green-600" title="Save">
                              <Save className="w-4 h-4" />
                            </button>
                            <button onClick={cancelEdit} className="p-1.5 rounded hover:bg-gray-200 text-gray-500" title="Cancel">
                              <X className="w-4 h-4" />
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center justify-end gap-1">
                            <button onClick={() => startEdit(row)}
                              className="p-1.5 rounded hover:bg-blue-100 text-blue-600" title="Edit">
                              <Pencil className="w-4 h-4" />
                            </button>
                            <button onClick={() => removeRow(row)}
                              className="p-1.5 rounded hover:bg-red-100 text-red-600" title="Delete">
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default HsnRatesSection;
