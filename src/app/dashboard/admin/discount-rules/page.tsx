"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Trash2, Tag } from "lucide-react";
import { CATEGORIES as CATEGORY_DEFS } from "@/lib/categories";

interface DiscountRule {
  id: string;
  category: string;
  brand: string | null;
  subBrand: string | null;
  discountPercentage: number;
  createdAt: string;
  updatedAt?: string;
}

export default function DiscountRulesPage() {
  const [rules, setRules] = useState<DiscountRule[]>([]);
  const [brandOptions, setBrandOptions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // New rule form
  const [newCategory, setNewCategory] = useState<string>(
    CATEGORY_DEFS[0]?.key || ""
  );
  const [newBrand, setNewBrand] = useState("");
  const [newSubBrand, setNewSubBrand] = useState("");
  const [newDiscount, setNewDiscount] = useState("");

  useEffect(() => {
    fetchRules();
    // pull brand options for the picker
    fetch("/api/products/filters")
      .then((r) => r.json())
      .then((d) => setBrandOptions(d?.brands || []))
      .catch(() => {});
  }, []);

  const fetchRules = async () => {
    try {
      setLoading(true);
      const res = await fetch("/api/discount-rules");
      const data = await res.json();
      if (data.success) setRules(data.data || []);
    } catch {
      setError("Failed to load discount rules");
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (
    category: string,
    discountPercentage: number,
    brand: string | null,
    subBrand: string | null
  ) => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch("/api/discount-rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, discountPercentage, brand, subBrand }),
      });
      const data = await res.json();
      if (data.success) {
        const scope = [category, brand, subBrand].filter(Boolean).join(" › ");
        setSuccess(`Saved: ${scope} — ${discountPercentage}%`);
        fetchRules();
      } else {
        setError(data.error || "Save failed");
      }
    } catch {
      setError("Failed to save rule");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this discount rule?")) return;
    try {
      const res = await fetch(`/api/discount-rules?id=${id}`, {
        method: "DELETE",
      });
      const data = await res.json();
      if (data.success) {
        setRules(rules.filter((r) => r.id !== id));
        setSuccess("Rule deleted");
      }
    } catch {
      setError("Failed to delete rule");
    }
  };

  const handleAddNew = () => {
    if (!newCategory || !newDiscount) return;
    handleSave(
      newCategory,
      parseFloat(newDiscount),
      newBrand.trim() || null,
      newSubBrand.trim() || null
    );
    setNewBrand("");
    setNewSubBrand("");
    setNewDiscount("");
  };

  // Group rules by category for display.
  const rulesByCategory = useMemo(() => {
    const map = new Map<string, DiscountRule[]>();
    for (const r of rules) {
      if (!map.has(r.category)) map.set(r.category, []);
      map.get(r.category)!.push(r);
    }
    return map;
  }, [rules]);

  const categoryLabel = (key: string) =>
    CATEGORY_DEFS.find((c) => c.key === key)?.label || key;

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-8">
      <div className="max-w-4xl mx-auto space-y-6">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold text-slate-900">
            Discount Rules
          </h1>
          <p className="text-slate-600 mt-1 text-sm">
            Apply discount percentages per category, optionally narrowed to a
            specific brand and sub-brand. The most specific rule matching a
            product wins:{" "}
            <b>category + brand + sub-brand</b> &nbsp;›&nbsp;{" "}
            <b>category + brand</b> &nbsp;›&nbsp; <b>category only</b>.
          </p>
        </div>

        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
            {error}
          </div>
        )}
        {success && (
          <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-emerald-700 text-sm">
            {success}
          </div>
        )}

        {/* Add New Rule */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h2 className="text-lg font-semibold text-slate-900 mb-4">
            Add / Update Rule
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            <div className="lg:col-span-1">
              <label className="block text-xs font-medium text-slate-600 mb-1 uppercase tracking-wide">
                Category
              </label>
              <select
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
              >
                {CATEGORY_DEFS.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="lg:col-span-1">
              <label className="block text-xs font-medium text-slate-600 mb-1 uppercase tracking-wide">
                Brand{" "}
                <span className="text-slate-400 normal-case tracking-normal">
                  (optional)
                </span>
              </label>
              <input
                type="text"
                list="discount-brand-options"
                value={newBrand}
                onChange={(e) => setNewBrand(e.target.value)}
                placeholder="e.g. Ray-Ban"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
              />
              <datalist id="discount-brand-options">
                {brandOptions.map((b) => (
                  <option key={b} value={b} />
                ))}
              </datalist>
            </div>
            <div className="lg:col-span-1">
              <label className="block text-xs font-medium text-slate-600 mb-1 uppercase tracking-wide">
                Sub-brand{" "}
                <span className="text-slate-400 normal-case tracking-normal">
                  (optional)
                </span>
              </label>
              <input
                type="text"
                value={newSubBrand}
                onChange={(e) => setNewSubBrand(e.target.value)}
                placeholder="e.g. Aviator Classic"
                disabled={!newBrand.trim()}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm disabled:bg-slate-50 disabled:text-slate-400"
              />
            </div>
            <div className="lg:col-span-1">
              <label className="block text-xs font-medium text-slate-600 mb-1 uppercase tracking-wide">
                Discount
              </label>
              <div className="relative">
                <input
                  type="number"
                  value={newDiscount}
                  onChange={(e) => setNewDiscount(e.target.value)}
                  placeholder="e.g. 30"
                  min="0"
                  max="100"
                  step="0.5"
                  className="w-full pr-8 pl-3 py-2 border border-slate-300 rounded-lg text-sm"
                />
                <span className="absolute right-3 top-2 text-sm text-slate-400">
                  %
                </span>
              </div>
            </div>
            <div className="lg:col-span-1 flex items-end">
              <button
                onClick={handleAddNew}
                disabled={!newCategory || !newDiscount || saving}
                className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm"
              >
                {saving ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Plus className="w-4 h-4" />
                )}
                Save
              </button>
            </div>
          </div>
          <p className="text-xs text-slate-500 mt-3">
            Leave brand empty for a category-wide rule. Adding the same
            category + brand + sub-brand twice updates the existing rule in
            place (doesn&apos;t create a duplicate).
          </p>
        </div>

        {/* Active Rules grouped by category */}
        <div className="space-y-4">
          {CATEGORY_DEFS.map((cat) => {
            const list = rulesByCategory.get(cat.key) || [];
            if (list.length === 0) return null;
            return (
              <div
                key={cat.key}
                className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden"
              >
                <div className="px-6 py-3 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
                  <h3 className="font-semibold text-slate-900">{cat.label}</h3>
                  <span className="text-xs text-slate-500">
                    {list.length} rule{list.length === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="divide-y divide-slate-100">
                  {list.map((rule) => (
                    <RuleRow
                      key={rule.id}
                      rule={rule}
                      onSave={(pct) =>
                        handleSave(rule.category, pct, rule.brand, rule.subBrand)
                      }
                      onDelete={() => handleDelete(rule.id)}
                    />
                  ))}
                </div>
              </div>
            );
          })}

          {rulesByCategory.size === 0 && (
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-8 text-center">
              <Tag className="w-8 h-8 mx-auto mb-2 text-slate-300" />
              <p className="text-sm text-slate-500">
                No discount rules configured yet. Add one above.
              </p>
            </div>
          )}
        </div>

        {/* How it works */}
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800 space-y-2">
          <p>
            <b>How matching works:</b> When a product is created or updated,
            the engine picks the most specific rule that matches the product.
          </p>
          <ul className="list-disc pl-5 space-y-1 text-xs">
            <li>
              <code className="bg-amber-100 px-1 rounded">
                SPECTACLES × Ray-Ban × Aviator Classic
              </code>{" "}
              — wins over brand-only and category-only rules for that exact
              combination.
            </li>
            <li>
              <code className="bg-amber-100 px-1 rounded">
                SPECTACLES × Ray-Ban
              </code>{" "}
              — applies to every Ray-Ban Spectacles product unless a more
              specific sub-brand rule exists.
            </li>
            <li>
              <code className="bg-amber-100 px-1 rounded">SPECTACLES</code>{" "}
              (no brand) — fallback for every Spectacles product.
            </li>
          </ul>
          <p className="text-xs">
            Formula: <code>MRP × (1 − discount%/100)</code>. Example: 30% on
            ₹10,000 MRP → sells at ₹7,000.
          </p>
        </div>
      </div>
    </div>
  );
}

function RuleRow({
  rule,
  onSave,
  onDelete,
}: {
  rule: DiscountRule;
  onSave: (pct: number) => void;
  onDelete: () => void;
}) {
  const [pct, setPct] = useState(rule.discountPercentage);

  const scopeLabel = rule.subBrand
    ? `${rule.brand || "(no brand)"} › ${rule.subBrand}`
    : rule.brand
      ? rule.brand
      : "All (category-wide)";

  const sampleSelling = Math.round(10000 * (1 - pct / 100));

  const specificity = rule.subBrand ? 3 : rule.brand ? 2 : 1;
  const specificityColor =
    specificity === 3
      ? "bg-purple-50 text-purple-700 border-purple-200"
      : specificity === 2
        ? "bg-blue-50 text-blue-700 border-blue-200"
        : "bg-slate-50 text-slate-600 border-slate-200";

  return (
    <div className="flex items-center gap-4 px-6 py-3 hover:bg-slate-50">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="font-medium text-slate-900 truncate">{scopeLabel}</p>
          <span
            className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-medium border ${specificityColor}`}
            title={`Specificity ${specificity} (higher wins)`}
          >
            {specificity === 3 ? "Brand+Sub" : specificity === 2 ? "Brand" : "Category"}
          </span>
        </div>
        <p className="text-xs text-slate-500 mt-0.5">
          MRP ₹10,000 → selling ₹{sampleSelling.toLocaleString("en-IN")}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="number"
          value={pct}
          min={0}
          max={100}
          step={0.5}
          onChange={(e) => setPct(parseFloat(e.target.value) || 0)}
          onBlur={() => {
            if (pct !== rule.discountPercentage) onSave(pct);
          }}
          className="w-20 px-2 py-1.5 border border-slate-300 rounded text-sm text-center"
        />
        <span className="text-sm text-slate-500">%</span>
        <button
          onClick={onDelete}
          className="p-1.5 text-red-500 hover:bg-red-50 rounded"
          title="Delete rule"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
