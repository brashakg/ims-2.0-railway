// ============================================================================
// IMS 2.0 - Shared per-user permission delta editor (council ruling sec.2)
// ============================================================================
// Reusable editor for the FOUR locked per-user permission dimensions:
//   1. Discount-cap override   -> writes `discountCap`
//   2. Hide/show modules       -> writes `moduleAccess` (deny-only)
//   3. Returns/refund approval -> a capability toggle within `permissions`
//   4. Per-screen access        -> the same `moduleAccess` map (module = screen)
//
// Owner sees PLAIN-ENGLISH sentences from /users/permissions/options (role
// deltas), never raw capability keys. A capability above the actor's level is
// shown DISABLED with a reason (never silently hidden), driven by `grantable`.
// PRESETS-ONLY: a toggle expands to a grant/deny capability key written on the
// user; we never persist a "preset" reference. The raw all-capabilities matrix
// is deliberately out of scope.
//
// This file extracts the editor that previously lived inline in SettingsAuth so
// the SAME component is reused by the Settings User-Management panel AND the
// onboarding wizard (backlog #13/#14). It is intentionally decoupled from the
// `UserData` shape -- it takes/returns the three plain value+setter pairs.

import { useEffect, useState } from 'react';
import clsx from 'clsx';
import { adminUserApi } from '../../services/api';
import { MODULE_ACCESS_OPTIONS } from '../../context/ModuleContext';
import { ROLE_HIERARCHY } from '../../pages/settings/settingsTypes';

// ---------------------------------------------------------------------------
// Types (mirror the GET /users/permissions/options contract)
// ---------------------------------------------------------------------------
export interface DeltaRow {
  key: string;
  label: string;
  type: 'toggle' | 'number';
  default: boolean | number;
  hard_floor_note: string | null;
}

export interface PermissionOptions {
  schema_version: number;
  discount_cap_field: string;
  role_deltas: Record<string, { defaults: string[]; commonOverrides: DeltaRow[] }>;
  grantable: string[];
}

export type CapabilityOverride = {
  grant?: Record<string, boolean>;
  deny?: Record<string, boolean>;
};

export interface PermissionDeltaEditorProps {
  /** The user's role set (drives which delta preset rows appear). */
  roles: string[];
  /** Two-sided capability override { grant, deny }. */
  permissions: CapabilityOverride;
  onPermissionsChange: (next: CapabilityOverride) => void;
  /** Per-user discount-cap override (a plain number). */
  discountCap: number | undefined;
  onDiscountCapChange: (next: number) => void;
  /** Deny-only module map { moduleKey: bool }. false hides a module/screen. */
  moduleAccess: Record<string, boolean>;
  onModuleAccessChange: (next: Record<string, boolean>) => void;
  /** Pre-loaded options (so a parent can fetch once + share). When omitted the
   *  component fetches /users/permissions/options itself. */
  options?: PermissionOptions | null;
  /** When false, the module-access block is hidden (e.g. a caller that renders
   *  its own module list). Defaults to true so all 4 dimensions live together. */
  showModuleAccess?: boolean;
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------
export function PermissionDeltaEditor({
  roles,
  permissions,
  onPermissionsChange,
  discountCap,
  onDiscountCapChange,
  moduleAccess,
  onModuleAccessChange,
  options: optionsProp,
  showModuleAccess = true,
}: PermissionDeltaEditorProps) {
  const [fetched, setFetched] = useState<PermissionOptions | null>(null);
  const [loadError, setLoadError] = useState(false);

  // Fetch options only when the parent didn't supply them.
  useEffect(() => {
    if (optionsProp !== undefined) return;
    let alive = true;
    adminUserApi
      .getPermissionOptions()
      .then((o) => { if (alive) setFetched(o as PermissionOptions); })
      .catch(() => { if (alive) setLoadError(true); });
    return () => { alive = false; };
  }, [optionsProp]);

  const options = optionsProp !== undefined ? optionsProp : fetched;

  // The delta rows for the user's HIGHEST role (the one that drives the preset).
  const highestRole = [...roles].sort(
    (a, b) => (ROLE_HIERARCHY[b] || 0) - (ROLE_HIERARCHY[a] || 0),
  )[0];
  const discountField = options?.discount_cap_field || '__discount_cap__';
  const rows: DeltaRow[] =
    (highestRole && options?.role_deltas[highestRole]?.commonOverrides) || [];

  const grants = permissions.grant || {};
  const denies = permissions.deny || {};

  // The on/off state of a capability toggle, given its role-baseline default:
  // explicit deny -> off; explicit grant -> on; otherwise the role default.
  const toggleState = (row: DeltaRow): boolean => {
    if (denies[row.key]) return false;
    if (grants[row.key]) return true;
    return Boolean(row.default);
  };

  const setToggle = (row: DeltaRow, on: boolean) => {
    const nextGrant = { ...grants };
    const nextDeny = { ...denies };
    delete nextGrant[row.key];
    delete nextDeny[row.key];
    // Only record an OVERRIDE when it DIFFERS from the role default (so the
    // stored map stays minimal + the preset stays the source of truth).
    if (on && !row.default) nextGrant[row.key] = true;
    if (!on && row.default) nextDeny[row.key] = true;
    onPermissionsChange({ grant: nextGrant, deny: nextDeny });
  };

  const resetToStandard = () => {
    onPermissionsChange({ grant: {}, deny: {} });
  };

  const isGrantable = (key: string) => !options || options.grantable.includes(key);

  return (
    <div className="space-y-5">
      {/* ---- Capability toggles (dimension 3: returns/refund approval etc.) -- */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="block text-sm font-medium text-gray-600">
            Permissions{' '}
            {highestRole ? `(beyond standard ${highestRole.replace(/_/g, ' ')})` : ''}
          </label>
          <button
            type="button"
            onClick={resetToStandard}
            className="text-xs text-bv-red-600 hover:underline"
          >
            Reset to standard role
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-2">
          Turn extra abilities on or off for this person. Limits like discount caps,
          GST and prescription ranges are always enforced and can&apos;t be lifted here.
        </p>

        {loadError && (
          <p className="text-xs text-amber-600">
            Could not load permission options; this person will use their standard role.
          </p>
        )}

        {!loadError && rows.length === 0 && (
          <p className="text-xs text-gray-500">
            {highestRole
              ? 'No extra permission toggles for this role; they use the standard role.'
              : 'Choose a role first to customise permissions.'}
          </p>
        )}

        <div className="space-y-2">
          {rows.map((row) => {
            if (row.type === 'number' || row.key === discountField) {
              return (
                <div
                  key={row.key}
                  className="flex items-center gap-2 p-2 bg-gray-50 border border-gray-200 rounded"
                >
                  <span className="text-sm text-gray-700 flex-1">{row.label}</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={discountCap ?? (row.default as number) ?? 0}
                    onChange={(e) =>
                      onDiscountCapChange(parseInt(e.target.value || '0', 10))
                    }
                    className="w-20 px-2 py-1 border border-gray-300 rounded text-sm"
                  />
                  <span className="text-sm text-gray-500">%</span>
                </div>
              );
            }
            const grantable = isGrantable(row.key);
            const checked = toggleState(row);
            const disabled = !grantable; // above the actor's level -> grayed
            return (
              <label
                key={row.key}
                className={clsx(
                  'flex items-start gap-2 p-2 rounded border',
                  disabled
                    ? 'bg-gray-100 border-gray-200 opacity-70 cursor-not-allowed'
                    : checked
                      ? 'bg-green-50 border-green-200 cursor-pointer'
                      : 'bg-gray-50 border-gray-200 cursor-pointer',
                )}
                title={
                  disabled
                    ? 'This permission is above your level and cannot be granted.'
                    : undefined
                }
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={disabled}
                  onChange={(e) => setToggle(row, e.target.checked)}
                  className="mt-0.5 rounded border-gray-300"
                />
                <span className="text-sm text-gray-700">
                  {row.label}
                  {disabled && (
                    <span className="block text-xs text-gray-400">
                      Above your level — cannot grant
                    </span>
                  )}
                  {row.hard_floor_note && (
                    <span className="block text-xs text-gray-400">{row.hard_floor_note}</span>
                  )}
                </span>
              </label>
            );
          })}
        </div>
      </div>

      {/* ---- Module / screen access (dimensions 2 + 4) --------------------- */}
      {showModuleAccess && (
        <div>
          <label className="block text-sm font-medium text-gray-600 mb-2">
            Module &amp; screen access
          </label>
          <p className="text-xs text-gray-500 mb-2">
            Uncheck to hide a module (and its screens) from this person. This only
            restricts within their role — it can never grant access their role lacks.
          </p>
          <div className="grid grid-cols-2 tablet:grid-cols-3 gap-2">
            {MODULE_ACCESS_OPTIONS.map(({ key: moduleKey, label }) => {
              const isEnabled = moduleAccess[moduleKey] !== false;
              return (
                <label
                  key={moduleKey}
                  className={clsx(
                    'flex items-center gap-2 p-2 rounded cursor-pointer',
                    isEnabled
                      ? 'bg-green-50 border border-green-200'
                      : 'bg-gray-50 border border-gray-200',
                  )}
                >
                  <input
                    type="checkbox"
                    checked={isEnabled}
                    onChange={(e) =>
                      onModuleAccessChange({ ...moduleAccess, [moduleKey]: e.target.checked })
                    }
                    className="rounded border-gray-300"
                  />
                  <span className="text-sm text-gray-600">{label}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default PermissionDeltaEditor;
