"use client";

import { useEffect, useMemo, useState } from "react";
import {
  FEATURES,
  defaultFeaturesForRole,
  effectiveFeatures,
  type FeatureKey,
} from "@/lib/features";
import Topbar from "@/components/Topbar";

interface Location {
  id: string;
  name: string;
  code: string;
}

interface UserRow {
  id: string;
  name: string;
  email: string;
  role: string;
  enabledFeatures: string | null;
  locationId: string | null;
  location?: Location;
  createdAt: string;
}

type FormState = {
  name: string;
  email: string;
  password: string;
  role: string;
  locationId: string;
  /** "default" → use role defaults (User.enabledFeatures = null);
   *  "custom"  → admin picked specific features (CSV stored). */
  featureMode: "default" | "custom";
  featureKeys: Set<FeatureKey>;
};

const EMPTY_FORM: FormState = {
  name: "",
  email: "",
  password: "",
  role: "CATALOG_MANAGER",
  locationId: "",
  featureMode: "default",
  featureKeys: new Set(),
};

const FEATURE_GROUPS: Record<string, string> = {
  main: "Main",
  ops: "Operations",
  insights: "Insights",
  admin: "Admin",
};

export default function UsersPage() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingUser, setEditingUser] = useState<UserRow | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      setLoading(true);
      const [usersRes, locationsRes] = await Promise.all([
        fetch("/api/users"),
        fetch("/api/locations?excludeSynthetic=true"),
      ]);
      if (!usersRes.ok || !locationsRes.ok)
        throw new Error("Failed to fetch data");
      const usersData = await usersRes.json();
      const locationsData = await locationsRes.json();
      setUsers(usersData);
      setLocations(locationsData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  const openCreate = () => {
    setEditingUser(null);
    setForm(EMPTY_FORM);
    setError(null);
    setShowModal(true);
  };

  const openEdit = (u: UserRow) => {
    setEditingUser(u);
    setError(null);
    const customMode = u.enabledFeatures !== null;
    setForm({
      name: u.name || "",
      email: u.email,
      password: "",
      role: u.role,
      locationId: u.locationId || "",
      featureMode: customMode ? "custom" : "default",
      featureKeys: new Set(
        effectiveFeatures({
          role: u.role,
          enabledFeatures: u.enabledFeatures,
        })
      ),
    });
    setShowModal(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!editingUser && (!form.name || !form.email || !form.password)) {
      setError("Name, email, and password are required");
      return;
    }

    const payload: any = {
      name: form.name,
      email: form.email,
      role: form.role,
      locationId: form.role === "CATALOG_MANAGER" ? form.locationId || null : null,
      // Send NULL when admin chose "use role defaults"; otherwise
      // explicit list. Empty list is meaningful (= no features at all).
      enabledFeatures:
        form.featureMode === "default"
          ? null
          : Array.from(form.featureKeys),
    };
    if (form.password) payload.password = form.password;

    try {
      setSaving(true);
      const url = editingUser
        ? `/api/users/${editingUser.id}`
        : "/api/users";
      const method = editingUser ? "PATCH" : "POST";
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.message || "Failed to save user");
      }
      setShowModal(false);
      setForm(EMPTY_FORM);
      setEditingUser(null);
      await fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save user");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (u: UserRow) => {
    if (!confirm(`Delete user ${u.name || u.email}? This can't be undone.`))
      return;
    try {
      const res = await fetch(`/api/users/${u.id}`, { method: "DELETE" });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.message || "Failed to delete user");
      }
      await fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const toggleFeature = (k: FeatureKey) => {
    setForm((f) => {
      const next = new Set(f.featureKeys);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return { ...f, featureKeys: next };
    });
  };

  const switchFeatureMode = (mode: "default" | "custom") => {
    setForm((f) => {
      if (mode === "custom" && f.featureMode === "default") {
        // Pre-fill the custom set from the role's defaults so admin sees
        // the current state and tweaks from there instead of starting blank.
        return {
          ...f,
          featureMode: "custom",
          featureKeys: new Set(defaultFeaturesForRole(f.role)),
        };
      }
      return { ...f, featureMode: mode };
    });
  };

  // When role changes in default mode, surface what they'll get.
  const previewFeatures = useMemo(() => {
    if (form.featureMode === "default") {
      return defaultFeaturesForRole(form.role);
    }
    return Array.from(form.featureKeys);
  }, [form.featureMode, form.role, form.featureKeys]);

  const featuresByGroup: Record<string, typeof FEATURES> = {};
  for (const f of FEATURES) {
    featuresByGroup[f.group] = featuresByGroup[f.group] || [];
    featuresByGroup[f.group].push(f);
  }

  if (loading) {
    return (
      <>
        <Topbar
          title="Users"
          breadcrumb={[{ label: "Admin", href: "/dashboard" }, { label: "Users" }]}
          primaryAction={null}
        />
        <div style={{ padding: 24, color: "var(--text-tertiary)" }}>
          Loading users…
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar
        title="Users"
        subtitle={`${users.length} total`}
        breadcrumb={[{ label: "Admin", href: "/dashboard" }, { label: "Users" }]}
        primaryAction={
          <button
            type="button"
            onClick={openCreate}
            className="polaris-btn polaris-btn-primary"
          >
            Add user
          </button>
        }
      />

      <div style={{ padding: 24, maxWidth: 1400, margin: "0 auto" }}>
        {error && (
          <div
            className="polaris-card"
            style={{
              padding: 12,
              marginBottom: 16,
              background: "var(--critical-bg)",
              borderColor: "var(--critical)",
              color: "var(--critical-text)",
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}

        <div className="polaris-card">
          {users.length === 0 ? (
            <div
              style={{
                padding: 32,
                textAlign: "center",
                color: "var(--text-tertiary)",
              }}
            >
              No users yet.
            </div>
          ) : (
            <table className="polaris-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Location</th>
                  <th>Features</th>
                  <th>Created</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const eff = effectiveFeatures({
                    role: u.role,
                    enabledFeatures: u.enabledFeatures,
                  });
                  const isCustom = u.enabledFeatures !== null;
                  return (
                    <tr key={u.id}>
                      <td style={{ fontWeight: 500 }}>{u.name || "—"}</td>
                      <td style={{ color: "var(--text-secondary)" }}>{u.email}</td>
                      <td>
                        <span className="polaris-badge">
                          {u.role.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td style={{ color: "var(--text-secondary)" }}>
                        {u.location?.name || "—"}
                      </td>
                      <td>
                        {u.role === "ADMIN" ? (
                          <span
                            className="polaris-badge polaris-badge-success"
                            title="Admin always has all features"
                          >
                            All ({FEATURES.length})
                          </span>
                        ) : isCustom ? (
                          <span
                            className="polaris-badge polaris-badge-magic"
                            title={eff.join(", ")}
                          >
                            Custom ({eff.length})
                          </span>
                        ) : (
                          <span
                            className="polaris-badge"
                            title={eff.join(", ")}
                          >
                            Role defaults ({eff.length})
                          </span>
                        )}
                      </td>
                      <td style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
                        {new Date(u.createdAt).toLocaleDateString()}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <button
                          type="button"
                          onClick={() => openEdit(u)}
                          className="polaris-btn polaris-btn-sm"
                          style={{ marginRight: 4 }}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(u)}
                          className="polaris-btn polaris-btn-sm"
                          style={{
                            color: "var(--critical-text)",
                            borderColor: "var(--critical)",
                          }}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Add / Edit user modal */}
      {showModal && (
        <div
          onClick={() => !saving && setShowModal(false)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 80,
            background: "rgba(26,26,26,0.4)",
            backdropFilter: "blur(2px)",
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "center",
            paddingTop: "5vh",
            paddingBottom: "5vh",
            overflowY: "auto",
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="fade-in polaris-card"
            style={{
              width: 640,
              maxWidth: "92vw",
              boxShadow: "var(--shadow-lg)",
              maxHeight: "90vh",
              overflowY: "auto",
            }}
          >
            <div className="polaris-card-header">
              <div className="polaris-card-title">
                {editingUser ? "Edit user" : "Add new user"}
              </div>
              <button
                type="button"
                onClick={() => !saving && setShowModal(false)}
                className="polaris-btn polaris-btn-icon"
                aria-label="Close"
              >
                ×
              </button>
            </div>

            <form onSubmit={handleSubmit} style={{ padding: 18 }}>
              <div className="grid grid-cols-2 gap-3 mb-4">
                <div>
                  <label
                    style={{
                      display: "block",
                      fontSize: 12,
                      fontWeight: 500,
                      marginBottom: 4,
                    }}
                  >
                    Name
                  </label>
                  <input
                    type="text"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    disabled={saving}
                    placeholder="Priya Menon"
                    style={{
                      width: "100%",
                      padding: "6px 10px",
                      border: "1px solid var(--border-strong)",
                      borderRadius: 8,
                      fontSize: 13,
                      height: 32,
                    }}
                  />
                </div>
                <div>
                  <label
                    style={{
                      display: "block",
                      fontSize: 12,
                      fontWeight: 500,
                      marginBottom: 4,
                    }}
                  >
                    Email
                  </label>
                  <input
                    type="email"
                    value={form.email}
                    onChange={(e) =>
                      setForm({ ...form, email: e.target.value })
                    }
                    disabled={saving || !!editingUser}
                    placeholder="priya@bettervision.in"
                    style={{
                      width: "100%",
                      padding: "6px 10px",
                      border: "1px solid var(--border-strong)",
                      borderRadius: 8,
                      fontSize: 13,
                      height: 32,
                    }}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 mb-4">
                <div>
                  <label
                    style={{
                      display: "block",
                      fontSize: 12,
                      fontWeight: 500,
                      marginBottom: 4,
                    }}
                  >
                    {editingUser ? "New password (leave blank to keep)" : "Password"}
                  </label>
                  <input
                    type="password"
                    value={form.password}
                    onChange={(e) =>
                      setForm({ ...form, password: e.target.value })
                    }
                    disabled={saving}
                    placeholder="••••••••"
                    style={{
                      width: "100%",
                      padding: "6px 10px",
                      border: "1px solid var(--border-strong)",
                      borderRadius: 8,
                      fontSize: 13,
                      height: 32,
                    }}
                  />
                </div>
                <div>
                  <label
                    style={{
                      display: "block",
                      fontSize: 12,
                      fontWeight: 500,
                      marginBottom: 4,
                    }}
                  >
                    Role
                  </label>
                  <select
                    value={form.role}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        role: e.target.value,
                        // If we were on default mode, stay on default — the
                        // preview below will refresh. If on custom mode,
                        // keep the user's manual selections (they may want
                        // a different role with the same features).
                      })
                    }
                    disabled={saving}
                    style={{
                      width: "100%",
                      padding: "6px 10px",
                      border: "1px solid var(--border-strong)",
                      borderRadius: 8,
                      fontSize: 13,
                      height: 32,
                      background: "white",
                    }}
                  >
                    <option value="ADMIN">Admin</option>
                    <option value="DESIGN_MANAGER">Design Manager</option>
                    <option value="CATALOG_MANAGER">Catalog Manager</option>
                  </select>
                </div>
              </div>

              {form.role === "CATALOG_MANAGER" && (
                <div className="mb-4">
                  <label
                    style={{
                      display: "block",
                      fontSize: 12,
                      fontWeight: 500,
                      marginBottom: 4,
                    }}
                  >
                    Default location
                  </label>
                  <select
                    value={form.locationId}
                    onChange={(e) =>
                      setForm({ ...form, locationId: e.target.value })
                    }
                    disabled={saving}
                    style={{
                      width: "100%",
                      padding: "6px 10px",
                      border: "1px solid var(--border-strong)",
                      borderRadius: 8,
                      fontSize: 13,
                      height: 32,
                      background: "white",
                    }}
                  >
                    <option value="">Select a location…</option>
                    {locations.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name} ({l.code})
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {/* ─── Permissions ─────────────────────────── */}
              <div className="mb-4">
                <label
                  style={{
                    display: "block",
                    fontSize: 12,
                    fontWeight: 600,
                    marginBottom: 6,
                  }}
                >
                  Permissions
                </label>
                {form.role === "ADMIN" ? (
                  <div
                    className="polaris-card"
                    style={{
                      padding: 10,
                      fontSize: 12,
                      color: "var(--text-secondary)",
                      background: "var(--bg-surface-tertiary)",
                    }}
                  >
                    Admin role implicitly has every feature. Per-feature
                    toggles are disabled for this role.
                  </div>
                ) : (
                  <>
                    <div
                      style={{
                        display: "flex",
                        gap: 8,
                        marginBottom: 10,
                        fontSize: 12,
                      }}
                    >
                      <label
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          cursor: "pointer",
                        }}
                      >
                        <input
                          type="radio"
                          checked={form.featureMode === "default"}
                          onChange={() => switchFeatureMode("default")}
                          disabled={saving}
                        />
                        Use role defaults
                      </label>
                      <label
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          cursor: "pointer",
                        }}
                      >
                        <input
                          type="radio"
                          checked={form.featureMode === "custom"}
                          onChange={() => switchFeatureMode("custom")}
                          disabled={saving}
                        />
                        Custom permissions
                      </label>
                    </div>

                    <div
                      className="polaris-card"
                      style={{
                        padding: 10,
                        background:
                          form.featureMode === "default"
                            ? "var(--bg-surface-tertiary)"
                            : "var(--bg-surface)",
                      }}
                    >
                      <div
                        className="grid grid-cols-2 gap-x-4 gap-y-1.5"
                        style={{ fontSize: 12 }}
                      >
                        {Object.entries(featuresByGroup).map(
                          ([groupKey, groupFeats]) => (
                            <div key={groupKey} style={{ minWidth: 0 }}>
                              <div
                                style={{
                                  fontSize: 10,
                                  fontWeight: 600,
                                  color: "var(--text-tertiary)",
                                  textTransform: "uppercase",
                                  letterSpacing: 0.5,
                                  marginBottom: 4,
                                  marginTop: 4,
                                }}
                              >
                                {FEATURE_GROUPS[groupKey] || groupKey}
                              </div>
                              {groupFeats.map((feat) => {
                                const checked = previewFeatures.includes(
                                  feat.key
                                );
                                const customMode = form.featureMode === "custom";
                                return (
                                  <label
                                    key={feat.key}
                                    style={{
                                      display: "flex",
                                      alignItems: "flex-start",
                                      gap: 6,
                                      padding: "3px 0",
                                      cursor: customMode ? "pointer" : "default",
                                      opacity: customMode ? 1 : 0.7,
                                    }}
                                    title={feat.description}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={checked}
                                      onChange={() =>
                                        customMode && toggleFeature(feat.key)
                                      }
                                      disabled={saving || !customMode}
                                      style={{ marginTop: 2 }}
                                    />
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                      <div style={{ fontWeight: 500 }}>
                                        {feat.label}
                                      </div>
                                      <div
                                        style={{
                                          fontSize: 11,
                                          color: "var(--text-tertiary)",
                                        }}
                                      >
                                        {feat.description}
                                      </div>
                                    </div>
                                  </label>
                                );
                              })}
                            </div>
                          )
                        )}
                      </div>
                      <div
                        style={{
                          marginTop: 8,
                          paddingTop: 8,
                          borderTop: "1px solid var(--border-subdued)",
                          fontSize: 11,
                          color: "var(--text-tertiary)",
                        }}
                      >
                        {form.featureMode === "default"
                          ? `Role default for ${form.role.replace(/_/g, " ")}: ${previewFeatures.length} feature(s).`
                          : `Custom: ${previewFeatures.length} feature(s) enabled.`}
                      </div>
                    </div>
                  </>
                )}
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  type="submit"
                  disabled={saving}
                  className="polaris-btn polaris-btn-primary"
                  style={{ flex: 1, justifyContent: "center" }}
                >
                  {saving
                    ? "Saving…"
                    : editingUser
                      ? "Save changes"
                      : "Create user"}
                </button>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => setShowModal(false)}
                  className="polaris-btn"
                  style={{ flex: 1, justifyContent: "center" }}
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
