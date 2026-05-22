"use client";

import { useEffect, useState } from "react";

interface LocationUser {
  id: string;
  name: string;
  email: string;
  role: string;
}

interface Location {
  id: string;
  name: string;
  code: string;
  address?: string;
  city?: string;
  users: LocationUser[];
}

export default function LocationsPage() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formData, setFormData] = useState({
    name: "",
    code: "",
    address: "",
    city: "",
  });

  // Fetch locations
  useEffect(() => {
    fetchLocations();
  }, []);

  const fetchLocations = async () => {
    try {
      setLoading(true);
      const response = await fetch("/api/locations");
      if (!response.ok) throw new Error("Failed to fetch locations");
      const data = await response.json();
      setLocations(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  const handleAddLocation = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!formData.name || !formData.code) {
      setError("Name and code are required");
      return;
    }

    try {
      setSaving(true);
      const response = await fetch("/api/locations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.message || "Failed to create location");
      }

      setFormData({
        name: "",
        code: "",
        address: "",
        city: "",
      });
      setShowModal(false);
      setError(null);
      await fetchLocations();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create location");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-slate-600">Loading locations...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-8">
      <div className="max-w-6xl mx-auto">
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-3xl font-bold text-slate-900">Location Management</h1>
          <button
            onClick={() => setShowModal(true)}
            className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition"
          >
            Add Location
          </button>
        </div>

        {error && (
          <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
            {error}
          </div>
        )}

        {/* Locations Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {locations.length === 0 ? (
            <div className="col-span-full p-8 text-center text-slate-500 bg-white rounded-lg">
              No locations found
            </div>
          ) : (
            locations.map((location) => (
              <div key={location.id} className="bg-white rounded-lg shadow hover:shadow-lg transition">
                <div className="p-6">
                  <div className="flex justify-between items-start mb-4">
                    <div>
                      <h3 className="text-lg font-semibold text-slate-900">
                        {location.name}
                      </h3>
                      <p className="text-sm text-slate-500 font-mono">{location.code}</p>
                    </div>
                  </div>

                  {location.address && (
                    <div className="mb-3">
                      <p className="text-sm text-slate-600">
                        <span className="font-medium">Address:</span> {location.address}
                      </p>
                    </div>
                  )}

                  {location.city && (
                    <div className="mb-4">
                      <p className="text-sm text-slate-600">
                        <span className="font-medium">City:</span> {location.city}
                      </p>
                    </div>
                  )}

                  <div className="border-t border-slate-200 pt-4">
                    <h4 className="text-sm font-semibold text-slate-900 mb-2">
                      Users ({location.users.length})
                    </h4>
                    {location.users.length === 0 ? (
                      <p className="text-sm text-slate-500">No users assigned</p>
                    ) : (
                      <ul className="space-y-2">
                        {location.users.map((user) => (
                          <li key={user.id} className="text-sm">
                            <p className="font-medium text-slate-900">{user.name}</p>
                            <p className="text-slate-600">{user.email}</p>
                            <p className="text-xs text-slate-500">{user.role.replace(/_/g, " ")}</p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Add Location Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full">
            <div className="p-6 border-b border-slate-200">
              <h2 className="text-xl font-semibold text-slate-900">Add New Location</h2>
            </div>

            <form onSubmit={handleAddLocation} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Location Name
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Main Warehouse"
                  disabled={saving}
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Location Code
                </label>
                <input
                  type="text"
                  value={formData.code}
                  onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="LOC-001"
                  disabled={saving}
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Address
                </label>
                <input
                  type="text"
                  value={formData.address}
                  onChange={(e) => setFormData({ ...formData, address: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="123 Main Street"
                  disabled={saving}
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  City
                </label>
                <input
                  type="text"
                  value={formData.city}
                  onChange={(e) => setFormData({ ...formData, city: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="New York"
                  disabled={saving}
                />
              </div>

              <div className="flex gap-2 pt-4">
                <button
                  type="submit"
                  disabled={saving}
                  className="flex-1 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:bg-slate-300 transition"
                >
                  {saving ? "Creating..." : "Create Location"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowModal(false);
                    setError(null);
                    setFormData({
                      name: "",
                      code: "",
                      address: "",
                      city: "",
                    });
                  }}
                  className="flex-1 px-4 py-2 bg-slate-300 text-slate-700 rounded-lg hover:bg-slate-400 transition"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
