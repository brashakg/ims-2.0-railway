// ============================================================================
// E2 - Policy Matrix (schema-driven form)
// The /settings/policies/registry response IS the form definition: every control
// is rendered from its typed PolicySpec (type/enum/min/max/group/write_roles).
// Adding a registry key surfaces a new control automatically -- no FE change.
// Resolution store > entity > global > env > default; the resolved source is shown
// per row. Secret values are masked by the server and only sent when re-entered.
// ============================================================================

import { useState, useEffect, useCallback } from 'react';
import { Save, RotateCcw, RefreshCw, Lock } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { policiesApi } from '../../services/api';
import type { PolicySpecPublic, PolicyEffective } from '../../services/api';

const SOURCE_TINT: Record<string, string> = {
  store: 'bg-blue-50 text-blue-700',
  entity: 'bg-violet-50 text-violet-700',
  global: 'bg-gray-100 text-gray-700',
  env: 'bg-amber-50 text-amber-700',
  default: 'bg-gray-50 text-gray-500',
};

function toDisplay(spec: PolicySpecPublic, value: any): any {
  if (value === null || value === undefined) return spec.type === 'bool' ? false : '';
  switch (spec.type) {
    case 'money_paisa':
      return typeof value === 'number' ? value / 100 : value;
    case 'csv_int':
      return Array.isArray(value) ? value.join(',') : String(value);
    case 'json':
      try { return JSON.stringify(value); } catch { return String(value); }
    case 'bool':
      return !!value;
    default:
      return value;
  }
}

function fromDisplay(spec: PolicySpecPublic, display: any): any {
  switch (spec.type) {
    case 'bool':
      return !!display;
    case 'int':
      return Math.round(Number(display));
    case 'money_paisa':
      return Math.round(Number(display) * 100);
    case 'float':
    case 'percent':
      return Number(display);
    case 'csv_int':
      return String(display).split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => !Number.isNaN(n));
    case 'json':
      return JSON.parse(String(display)); // throws on bad JSON -> caught by caller
    default:
      return display;
  }
}

export function PolicySchemaForm({ storeId }: { storeId?: string }) {
  const { user } = useAuth();
  const toast = useToast();
  const [groups, setGroups] = useState<Record<string, PolicySpecPublic[]>>({});
  const [effective, setEffective] = useState<Record<string, PolicyEffective>>({});
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [scope, setScope] = useState<'global' | 'store'>('global');
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  const roles: string[] = (user?.roles as string[]) || (user?.activeRole ? [user.activeRole] : []);
  const isSuper = roles.includes('SUPERADMIN');
  const scopeParam = scope === 'store' && storeId ? `store:${storeId}` : 'global';

  const canWrite = useCallback((spec: PolicySpecPublic): boolean => {
    if (isSuper) return true;
    if (!spec.write_roles.some((r) => roles.includes(r))) return false;
    // store-scoped role can only write at store scope (mirrors the server gate)
    if (scope !== 'store' && !roles.some((r) => ['ADMIN', 'AREA_MANAGER', 'ACCOUNTANT'].includes(r))) return false;
    return spec.scopes.includes(scope === 'store' ? 'store' : 'global');
  }, [isSuper, roles, scope]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [reg, vals] = await Promise.all([
        policiesApi.getRegistry(),
        policiesApi.getAll(scopeParam),
      ]);
      setGroups(reg.groups);
      setEffective(vals.policies);
      const seeded: Record<string, any> = {};
      for (const s of reg.policies) {
        const eff = vals.policies[s.key];
        seeded[s.key] = s.secret ? '' : toDisplay(s, eff ? eff.value : s.default);
      }
      setEdits(seeded);
    } catch {
      toast.error('Failed to load policy matrix');
    } finally {
      setLoading(false);
    }
  }, [scopeParam, toast]);

  useEffect(() => { load(); }, [load]);

  const save = async (spec: PolicySpecPublic) => {
    setSavingKey(spec.key);
    try {
      const value = fromDisplay(spec, edits[spec.key]);
      const scopeDict = scope === 'store' && storeId ? { store_id: storeId } : null;
      const res = await policiesApi.set(spec.key, value, scopeDict);
      setEffective((prev) => ({ ...prev, [spec.key]: res }));
      toast.success(`${spec.label} saved (${res.source})`);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || (err instanceof SyntaxError ? 'Invalid JSON' : 'Save failed');
      toast.error(msg);
    } finally {
      setSavingKey(null);
    }
  };

  const clear = async (spec: PolicySpecPublic) => {
    setSavingKey(spec.key);
    try {
      const res = await policiesApi.clear(spec.key, scopeParam);
      setEffective((prev) => ({ ...prev, [spec.key]: res }));
      setEdits((prev) => ({ ...prev, [spec.key]: spec.secret ? '' : toDisplay(spec, res.value) }));
      toast.success(`${spec.label} override cleared`);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Clear failed');
    } finally {
      setSavingKey(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <RefreshCw className="w-7 h-7 animate-spin text-gray-400" />
      </div>
    );
  }

  const groupNames = Object.keys(groups);

  return (
    <div className="space-y-4">
      {/* Scope selector */}
      <div className="card flex items-center justify-between p-3">
        <div>
          <p className="font-medium text-gray-900">Scope</p>
          <p className="text-sm text-gray-500">
            Values resolve store &rarr; entity &rarr; global &rarr; env &rarr; default. Edit at the level you intend to override.
          </p>
        </div>
        <select
          value={scope}
          aria-label="Policy scope"
          onChange={(e) => setScope(e.target.value as 'global' | 'store')}
          className="input-field max-w-[220px]"
        >
          <option value="global">Global (all stores)</option>
          <option value="store" disabled={!storeId}>
            {storeId ? `This store (${storeId})` : 'This store (no active store)'}
          </option>
        </select>
      </div>

      {groupNames.map((gname) => (
        <div key={gname} className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-3">{gname}</h2>
          <div className="space-y-3">
            {groups[gname].map((spec) => {
              const eff = effective[spec.key];
              const source = eff?.source || 'default';
              const writable = canWrite(spec);
              const scopeNotAllowed = !spec.scopes.includes(scope === 'store' ? 'store' : 'global');
              return (
                <div key={spec.key} className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] gap-3 items-start py-2 border-b border-gray-100 last:border-0">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-gray-900">{spec.label}</p>
                      {spec.secret && <Lock className="w-3.5 h-3.5 text-gray-400" />}
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${SOURCE_TINT[source] || SOURCE_TINT.default}`}>
                        {source}
                      </span>
                    </div>
                    {spec.help && <p className="text-xs text-gray-500 mt-0.5">{spec.help}</p>}
                    <p className="text-[10px] text-gray-400 font-mono mt-0.5">{spec.key}</p>
                  </div>

                  <div className="flex items-center gap-2">
                    {/* Type-appropriate control */}
                    {spec.type === 'bool' ? (
                      <label className="flex items-center gap-2 flex-1">
                        <input
                          type="checkbox"
                          checked={!!edits[spec.key]}
                          disabled={!writable || scopeNotAllowed}
                          onChange={(e) => setEdits((p) => ({ ...p, [spec.key]: e.target.checked }))}
                          className="rounded border-gray-300"
                        />
                        <span className="text-sm text-gray-600">{edits[spec.key] ? 'Enabled' : 'Disabled'}</span>
                      </label>
                    ) : spec.enum ? (
                      <select
                        value={edits[spec.key] ?? ''}
                        aria-label={spec.label}
                        disabled={!writable || scopeNotAllowed}
                        onChange={(e) => setEdits((p) => ({ ...p, [spec.key]: e.target.value }))}
                        className="input-field flex-1"
                      >
                        {spec.enum.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                      </select>
                    ) : (
                      <input
                        type={['int', 'float', 'percent', 'money_paisa'].includes(spec.type) ? 'number' : (spec.secret ? 'password' : 'text')}
                        value={edits[spec.key] ?? ''}
                        disabled={!writable || scopeNotAllowed}
                        placeholder={spec.secret ? '•••• (re-enter to change)' : (spec.type === 'money_paisa' ? 'rupees' : spec.type === 'csv_int' ? '30,60,90' : '')}
                        min={spec.minimum ?? undefined}
                        max={spec.maximum ?? undefined}
                        onChange={(e) => setEdits((p) => ({ ...p, [spec.key]: e.target.value }))}
                        className="input-field flex-1 font-mono text-sm"
                      />
                    )}
                    <button
                      type="button"
                      onClick={() => save(spec)}
                      disabled={!writable || scopeNotAllowed || savingKey === spec.key}
                      className="btn-primary px-2.5 py-1.5"
                      title={scopeNotAllowed ? 'Not settable at this scope' : !writable ? 'Your role cannot write this key' : 'Save'}
                    >
                      <Save className="w-4 h-4" />
                    </button>
                    {scope === 'store' && (
                      <button
                        type="button"
                        onClick={() => clear(spec)}
                        disabled={!writable || savingKey === spec.key}
                        className="btn-outline px-2.5 py-1.5"
                        title="Clear this store override (fall back to parent scope)"
                      >
                        <RotateCcw className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

export default PolicySchemaForm;
