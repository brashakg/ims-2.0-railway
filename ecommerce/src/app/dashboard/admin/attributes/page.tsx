"use client";

import { useEffect, useState } from "react";

interface AttributeOption {
  id: string;
  value: string;
  attributeTypeId: string;
}

interface AttributeType {
  id: string;
  name: string;
  options: AttributeOption[];
}

// Format camelCase/concatenated attribute names to Title Case
function formatAttributeName(name: string): string {
  // Insert space before uppercase letters (camelCase)
  const spaced = name.replace(/([a-z])([A-Z])/g, '$1 $2');
  // Insert space before known word boundaries in concatenated names
  const expanded = spaced
    .replace(/countryof/i, 'Country of ')
    .replace(/framecolor/i, 'Frame Color')
    .replace(/framematerial/i, 'Frame Material')
    .replace(/frametype/i, 'Frame Type')
    .replace(/framesize/i, 'Frame Size')
    .replace(/lenscolour/i, 'Lens Colour')
    .replace(/lensmaterial/i, 'Lens Material')
    .replace(/lensUSP/i, 'Lens USP')
    .replace(/^polarization$/i, 'Polarization')
    .replace(/^gender$/i, 'Gender')
    .replace(/^brand$/i, 'Brand');
  // Title case each word
  return expanded
    .split(/\s+/)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
    .replace(/ Of /g, ' of ')
    .replace(/Usp/g, 'USP');
}

export default function AttributesPage() {
  const [attributeTypes, setAttributeTypes] = useState<AttributeType[]>([]);
  const [selectedType, setSelectedType] = useState<AttributeType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newOptionValue, setNewOptionValue] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [saving, setSaving] = useState(false);

  // Fetch attribute types
  useEffect(() => {
    fetchAttributes();
  }, []);

  const fetchAttributes = async () => {
    try {
      setLoading(true);
      const response = await fetch("/api/attributes");
      if (!response.ok) throw new Error("Failed to fetch attributes");
      const data = await response.json();
      setAttributeTypes(data);
      if (data.length > 0) {
        setSelectedType(data[0]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  const handleAddOption = async () => {
    if (!selectedType || !newOptionValue.trim()) return;

    try {
      setSaving(true);
      const response = await fetch("/api/attributes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: selectedType.name,
          options: [...selectedType.options.map((o) => o.value), newOptionValue],
        }),
      });

      if (!response.ok) throw new Error("Failed to add option");
      
      setNewOptionValue("");
      await fetchAttributes();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add option");
    } finally {
      setSaving(false);
    }
  };

  const handleEditOption = async (optionId: string) => {
    if (!editingValue.trim()) return;

    try {
      setSaving(true);
      const response = await fetch(`/api/attributes/${optionId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: editingValue }),
      });

      if (!response.ok) throw new Error("Failed to update option");

      setEditingId(null);
      setEditingValue("");
      await fetchAttributes();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update option");
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteOption = async (optionId: string) => {
    if (!confirm("Are you sure you want to delete this option?")) return;

    try {
      setSaving(true);
      const response = await fetch(`/api/attributes/${optionId}`, {
        method: "DELETE",
      });

      if (!response.ok) throw new Error("Failed to delete option");

      await fetchAttributes();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete option");
    } finally {
      setSaving(false);
    }
  };

  const filteredOptions = selectedType?.options.filter((opt) =>
    opt.value.toLowerCase().includes(searchQuery.toLowerCase())
  ) || [];

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-slate-600">Loading attributes...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-8">
      <div className="max-w-6xl mx-auto">
        <h1 className="text-3xl font-bold text-slate-900 mb-8">Attribute Management</h1>

        {error && (
          <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          {/* Sidebar - Attribute Types */}
          <div className="md:col-span-1">
            <div className="bg-white rounded-lg shadow">
              <div className="p-4 border-b border-slate-200">
                <h2 className="text-lg font-semibold text-slate-900">
                  Attribute Types
                </h2>
              </div>
              <div className="divide-y divide-slate-200">
                {attributeTypes.map((type) => (
                  <button
                    key={type.id}
                    onClick={() => setSelectedType(type)}
                    className={`w-full text-left px-4 py-3 hover:bg-slate-50 transition ${
                      selectedType?.id === type.id
                        ? "bg-blue-50 border-l-4 border-blue-500"
                        : ""
                    }`}
                  >
                    <div className="font-medium text-slate-900">{formatAttributeName(type.name)}</div>
                    <div className="text-sm text-slate-500">{type.options.length} options</div>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Right Panel - Attribute Options */}
          <div className="md:col-span-2">
            {selectedType ? (
              <div className="bg-white rounded-lg shadow">
                <div className="p-6 border-b border-slate-200">
                  <h2 className="text-xl font-semibold text-slate-900 mb-4">
                    {formatAttributeName(selectedType.name)} Options
                  </h2>

                  {/* Add New Option */}
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={newOptionValue}
                      onChange={(e) => setNewOptionValue(e.target.value)}
                      onKeyPress={(e) => {
                        if (e.key === "Enter") handleAddOption();
                      }}
                      placeholder="Enter new option value"
                      className="flex-1 px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                      disabled={saving}
                    />
                    <button
                      onClick={handleAddOption}
                      disabled={saving || !newOptionValue.trim()}
                      className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:bg-slate-300 transition"
                    >
                      Add
                    </button>
                  </div>
                </div>

                {/* Search */}
                <div className="p-6 border-b border-slate-200">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search options..."
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                {/* Options List */}
                <div className="p-6">
                  {filteredOptions.length === 0 ? (
                    <div className="text-center text-slate-500 py-8">
                      No options found
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {filteredOptions.map((option) => (
                        <div
                          key={option.id}
                          className="flex items-center justify-between p-3 bg-slate-50 rounded-lg hover:bg-slate-100 transition"
                        >
                          {editingId === option.id ? (
                            <input
                              type="text"
                              value={editingValue}
                              onChange={(e) => setEditingValue(e.target.value)}
                              onKeyPress={(e) => {
                                if (e.key === "Enter") handleEditOption(option.id);
                              }}
                              className="flex-1 px-2 py-1 border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                              autoFocus
                            />
                          ) : (
                            <span className="text-slate-900">{option.value}</span>
                          )}
                          <div className="flex gap-2">
                            {editingId === option.id ? (
                              <>
                                <button
                                  onClick={() => handleEditOption(option.id)}
                                  disabled={saving}
                                  className="px-3 py-1 bg-green-500 text-white text-sm rounded hover:bg-green-600 disabled:bg-slate-300 transition"
                                >
                                  Save
                                </button>
                                <button
                                  onClick={() => {
                                    setEditingId(null);
                                    setEditingValue("");
                                  }}
                                  className="px-3 py-1 bg-slate-400 text-white text-sm rounded hover:bg-slate-500 transition"
                                >
                                  Cancel
                                </button>
                              </>
                            ) : (
                              <>
                                <button
                                  onClick={() => {
                                    setEditingId(option.id);
                                    setEditingValue(option.value);
                                  }}
                                  className="px-3 py-1 bg-slate-500 text-white text-sm rounded hover:bg-slate-600 transition"
                                >
                                  Edit
                                </button>
                                <button
                                  onClick={() => handleDeleteOption(option.id)}
                                  disabled={saving}
                                  className="px-3 py-1 bg-red-500 text-white text-sm rounded hover:bg-red-600 disabled:bg-slate-300 transition"
                                >
                                  Delete
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="bg-white rounded-lg shadow p-8 text-center text-slate-500">
                Select an attribute type to manage its options
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
