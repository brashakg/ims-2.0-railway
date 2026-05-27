// ============================================================================
// IMS 2.0 - Print Template Content Editor (v2-3)
// ============================================================================
// Side-drawer editor for the per-entity print-template content overrides
// (signatory name, declaration text, drug licence, NCAHP UID, footer terms,
// retention years, etc.). The drawer stays open NEXT to the live preview
// so the owner sees the edits land in real time.
//
// Role-gated to SUPERADMIN/ADMIN at the call site (PrintPage hides the Edit
// affordance for lower roles).
//
// Notes:
//   - Drawer UI uses BV brand tokens; the printed template itself does NOT
//     (statutory aesthetic only on real prints).
//   - Empty-string fields are scrubbed on save -- the backend ignores them
//     too. To revert to defaults the owner clicks Revert (which deletes the
//     override row entirely).
//   - Live preview uses the SAME LegalHeader/StaffHeader builders the
//     production renderer uses, so what the owner sees IS what they get.

import { useEffect, useMemo, useState } from 'react';
import { Save, RotateCcw, X, AlertTriangle, Eye } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
// Direct import (barrel re-export gotcha for newly-added services).
import {
  printOverridesApi,
  type PrintOverrideFields,
  type PrintTemplateKey,
} from '../../services/api/printOverrides';
import { entitiesApi, type Entity } from '../../services/api/entities';
import {
  buildLegalHeader,
  LegalHeaderView,
  LegalFooterBlock,
  buildStaffHeader,
  StaffHeaderView,
  declarations,
  type EntityLike,
  type StoreLike,
} from './legalPrimitives';

interface PrintTemplateContentEditorProps {
  templateKey: PrintTemplateKey;
  templateLabel: string;
  /** Pre-selected entity for the drawer; SUPERADMIN can pick another. */
  defaultEntityId?: string;
  /** Sample store data for the preview (the owner's own store usually). */
  sampleStore?: StoreLike | null;
  /** Whether this template is a customer-facing (LegalHeader) vs internal
   *  (StaffHeader) doc -- changes which preview is rendered. */
  usesStaffHeader?: boolean;
  onClose: () => void;
}

const FIELD_GROUPS: Array<{
  title: string;
  fields: Array<{
    key: keyof PrintOverrideFields;
    label: string;
    help?: string;
    multiline?: boolean;
    type?: 'text' | 'number' | 'boolean';
    warn?: string;
    rxOnly?: boolean;
  }>;
}> = [
  {
    title: 'Branding',
    fields: [
      {
        key: 'header_subtitle',
        label: 'Header subtitle / tagline',
        help: 'Shown under the entity name in the header.',
      },
      {
        key: 'logo_url',
        label: 'Logo URL',
        help: 'Optional. Must be a public image URL.',
      },
    ],
  },
  {
    title: 'Signatory',
    fields: [
      {
        key: 'signatory_name',
        label: 'Signatory name',
        help: 'Authorised signatory printed above the seal line.',
      },
      {
        key: 'signatory_designation',
        label: 'Signatory designation',
        help: 'E.g. "Director", "Authorised Signatory", "Proprietor".',
      },
    ],
  },
  {
    title: 'Statutory IDs',
    fields: [
      {
        key: 'drug_licence_no',
        label: 'Drug Licence No.',
        help: 'Required at stores dispensing contact lenses (D&C Rules).',
      },
      {
        key: 'ncahp_uid',
        label: 'NCAHP UID',
        help:
          'Mandatory since 2024 for all practising optometrists on the Rx Card.',
        rxOnly: true,
      },
      {
        key: 'dmc_reg',
        label: 'State Council Reg. No.',
        help:
          'E.g. DMC/R-4412/2014. Goes on the Rx Card alongside the NCAHP UID.',
        rxOnly: true,
      },
    ],
  },
  {
    title: 'Content',
    fields: [
      {
        key: 'declaration_text',
        label: 'Declaration',
        help:
          'Overrides the default CGST-compliant declaration. Leave blank to use the default.',
        multiline: true,
        warn:
          'WARNING: declaration text is a statutory field. Changing it may affect compliance — review with your accountant.',
      },
      {
        key: 'footer_terms',
        label: 'Footer terms / notes',
        help:
          'Free-text payment terms, warranty, jurisdiction note. Optional.',
        multiline: true,
      },
    ],
  },
  {
    title: 'Document Defaults',
    fields: [
      {
        key: 'retention_years',
        label: 'Retention years',
        help: 'Defaults to 7 per CGST Rule 56.',
        type: 'number',
      },
      {
        key: 'reverse_charge_default',
        label: 'Default reverse charge to Yes',
        help: 'Leave off unless your accountant says otherwise.',
        type: 'boolean',
      },
    ],
  },
];

export function PrintTemplateContentEditor({
  templateKey,
  templateLabel,
  defaultEntityId,
  sampleStore,
  usesStaffHeader = false,
  onClose,
}: PrintTemplateContentEditorProps) {
  const toast = useToast();
  const [entities, setEntities] = useState<Entity[]>([]);
  const [entitiesLoading, setEntitiesLoading] = useState(false);
  const [entityId, setEntityId] = useState<string>(defaultEntityId || '');
  const [fields, setFields] = useState<PrintOverrideFields>({});
  const [originalFields, setOriginalFields] = useState<PrintOverrideFields>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [exists, setExists] = useState(false);

  // Load entities once.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setEntitiesLoading(true);
      try {
        const res = await entitiesApi.list();
        if (!cancelled) {
          const list = Array.isArray((res as any)?.entities)
            ? ((res as any).entities as Entity[])
            : ((res as any) as Entity[] | undefined) || [];
          setEntities(Array.isArray(list) ? list : []);
          if (!entityId && Array.isArray(list) && list.length > 0) {
            setEntityId((list[0] as any).entity_id || (list[0] as any).id || '');
          }
        }
      } catch {
        if (!cancelled) setEntities([]);
      } finally {
        if (!cancelled) setEntitiesLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load override row whenever the entity selection changes.
  useEffect(() => {
    if (!entityId) {
      setFields({});
      setOriginalFields({});
      setExists(false);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await printOverridesApi.get(entityId, templateKey);
        if (!cancelled) {
          setFields(res.fields || {});
          setOriginalFields(res.fields || {});
          setExists(!!res.exists);
        }
      } catch {
        if (!cancelled) {
          setFields({});
          setOriginalFields({});
          setExists(false);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [entityId, templateKey]);

  const dirty = useMemo(() => {
    return JSON.stringify(fields) !== JSON.stringify(originalFields);
  }, [fields, originalFields]);

  const selectedEntity = useMemo(
    () => entities.find((e) => (e as any).entity_id === entityId) as
      | EntityLike
      | undefined,
    [entities, entityId]
  );

  const handleSave = async () => {
    if (!entityId) {
      toast.error('Pick an entity first');
      return;
    }
    setSaving(true);
    try {
      const cleaned: PrintOverrideFields = {};
      for (const [k, v] of Object.entries(fields)) {
        if (v === null || v === undefined) continue;
        if (typeof v === 'string' && !v.trim()) continue;
        (cleaned as any)[k] = v;
      }
      const res = await printOverridesApi.upsert(entityId, templateKey, cleaned);
      setFields(res.fields || {});
      setOriginalFields(res.fields || {});
      setExists(true);
      toast.success('Saved');
    } catch {
      toast.error('Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const handleRevert = async () => {
    if (!entityId) return;
    if (!exists) {
      // Nothing on the server yet -- just clear the local edits.
      setFields({});
      setOriginalFields({});
      toast.info('Reset to defaults');
      return;
    }
    if (!confirm('Revert to defaults? The override row will be deleted.')) return;
    setSaving(true);
    try {
      await printOverridesApi.remove(entityId, templateKey);
      setFields({});
      setOriginalFields({});
      setExists(false);
      toast.success('Reverted to defaults');
    } catch {
      toast.error('Failed to revert');
    } finally {
      setSaving(false);
    }
  };

  const updateField = (key: keyof PrintOverrideFields, value: any) => {
    setFields((prev) => {
      const next = { ...prev } as any;
      if (value === '' || value === undefined || value === null) {
        delete next[key];
      } else {
        next[key] = value;
      }
      return next;
    });
  };

  // ---- Preview --------------------------------------------------------
  // Re-render with the in-progress field values on every keystroke. Cheap:
  // it's a pure builder + a single header SVG-free render. (Heavy templates
  // need not be re-rendered fully -- the header + footer are the slices the
  // owner edits.)
  const previewSample = useMemo(() => {
    const store: StoreLike = sampleStore || {
      name: 'Sample Store',
      address: '—',
      city: '—',
      state: 'Delhi',
      state_code: '07',
      pincode: '—',
    };
    if (usesStaffHeader) {
      return buildStaffHeader(selectedEntity, store, templateKey, {
        docNumber: 'SAMPLE-0001',
        docDate: new Date(),
        overrides: fields,
      });
    }
    return buildLegalHeader(selectedEntity, store, templateKey, {
      docNumber: 'SAMPLE-0001',
      docDate: new Date(),
      overrides: fields,
      copyMarkerMode: templateKey === 'thermal_receipt' ? 'none' : 'rule_48',
    });
  }, [selectedEntity, sampleStore, templateKey, usesStaffHeader, fields]);

  const previewDeclaration =
    fields.declaration_text || declarations(templateKey);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={{ background: 'rgba(0,0,0,.5)' }} onClick={onClose}>
      <div
        className="bg-white shadow-2xl flex flex-col"
        style={{ width: 'min(880px, 90vw)', height: '100vh' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Drawer header (BV brand tokens — editor UI only) */}
        <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between bg-bv-50">
          <div>
            <div className="text-xs uppercase tracking-wider text-bv font-semibold">Customise content</div>
            <div className="text-lg font-bold text-gray-900">{templateLabel}</div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded hover:bg-bv-100 text-gray-700"
            aria-label="Close editor"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="grid flex-1 min-h-0" style={{ gridTemplateColumns: '1fr 1fr' }}>
          {/* LEFT: editor */}
          <div className="overflow-y-auto p-5 border-r border-gray-200" style={{ background: '#fff' }}>
            {/* Entity selector */}
            <div className="mb-4">
              <label className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1">Entity</label>
              <select
                value={entityId}
                onChange={(e) => setEntityId(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm bg-white focus:outline-none focus:ring-2 focus:ring-bv"
                disabled={entitiesLoading || saving}
              >
                {entitiesLoading && <option value="">Loading entities...</option>}
                {!entitiesLoading && entities.length === 0 && (
                  <option value="">No entities found</option>
                )}
                {entities.map((e: any) => (
                  <option key={e.entity_id} value={e.entity_id}>
                    {e.name}
                    {e.legal_name && e.legal_name !== e.name ? ` (${e.legal_name})` : ''}
                  </option>
                ))}
              </select>
              <div className="text-[11px] text-gray-500 mt-1">
                Overrides apply to every print of {templateLabel} billed under this entity.
              </div>
            </div>

            {loading && (
              <div className="text-sm text-gray-500 py-6 text-center">Loading current content...</div>
            )}

            {!loading && FIELD_GROUPS.map((group) => {
              const visibleFields = group.fields.filter(
                (f) => !f.rxOnly || templateKey === 'rx_card'
              );
              if (visibleFields.length === 0) return null;
              return (
                <div key={group.title} className="mb-5">
                  <div className="text-xs uppercase tracking-wider text-gray-500 font-semibold mb-2">
                    {group.title}
                  </div>
                  <div className="space-y-3">
                    {visibleFields.map((f) => {
                      const value = (fields as any)[f.key];
                      if (f.type === 'boolean') {
                        return (
                          <div key={f.key as string}>
                            <label className="flex items-start gap-2 cursor-pointer">
                              <input
                                type="checkbox"
                                checked={!!value}
                                onChange={(e) => updateField(f.key, e.target.checked || undefined)}
                                className="mt-0.5 accent-bv"
                              />
                              <div>
                                <div className="text-sm font-medium text-gray-900">{f.label}</div>
                                {f.help && <div className="text-[11px] text-gray-500 mt-0.5">{f.help}</div>}
                              </div>
                            </label>
                          </div>
                        );
                      }
                      if (f.type === 'number') {
                        return (
                          <div key={f.key as string}>
                            <label className="block text-sm font-medium text-gray-900 mb-1">{f.label}</label>
                            <input
                              type="number"
                              value={typeof value === 'number' ? value : ''}
                              onChange={(e) =>
                                updateField(
                                  f.key,
                                  e.target.value === '' ? undefined : Number(e.target.value)
                                )
                              }
                              className="w-32 px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-bv"
                              min={1}
                              max={20}
                            />
                            {f.help && <div className="text-[11px] text-gray-500 mt-1">{f.help}</div>}
                          </div>
                        );
                      }
                      if (f.multiline) {
                        return (
                          <div key={f.key as string}>
                            <label className="block text-sm font-medium text-gray-900 mb-1">{f.label}</label>
                            <textarea
                              value={typeof value === 'string' ? value : ''}
                              onChange={(e) => updateField(f.key, e.target.value)}
                              rows={3}
                              className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-bv"
                            />
                            {f.help && <div className="text-[11px] text-gray-500 mt-1">{f.help}</div>}
                            {f.warn && (
                              <div className="mt-1 flex items-start gap-1 text-[11px] text-orange-700 bg-orange-50 border border-orange-200 px-2 py-1 rounded">
                                <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" />
                                <span>{f.warn}</span>
                              </div>
                            )}
                          </div>
                        );
                      }
                      return (
                        <div key={f.key as string}>
                          <label className="block text-sm font-medium text-gray-900 mb-1">{f.label}</label>
                          <input
                            type="text"
                            value={typeof value === 'string' ? value : ''}
                            onChange={(e) => updateField(f.key, e.target.value)}
                            className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-bv"
                          />
                          {f.help && <div className="text-[11px] text-gray-500 mt-1">{f.help}</div>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>

          {/* RIGHT: live preview (statutory aesthetic) */}
          <div className="overflow-y-auto p-5" style={{ background: '#f6f5f0' }}>
            <div className="text-xs uppercase tracking-wider text-gray-500 font-semibold mb-2 flex items-center gap-1">
              <Eye className="w-3 h-3" /> Preview
            </div>
            <div style={{ background: '#fff', border: '1px solid #1a1a19', boxShadow: '0 4px 20px rgba(0,0,0,.06)' }}>
              {usesStaffHeader ? (
                <StaffHeaderView
                  header={previewSample as any}
                  docTypeLabel={templateLabel.toUpperCase()}
                />
              ) : (
                <>
                  <LegalHeaderView
                    header={previewSample as any}
                    docTypeLabel={templateLabel.toUpperCase()}
                  />
                  {/* Tiny sample body so the footer block lands below something */}
                  <div style={{ padding: 18, fontSize: 11, color: '#4a4a45' }}>
                    [Sample body — your real line items, totals, HSN summary appear here at print time.]
                  </div>
                  <LegalFooterBlock
                    header={previewSample as any}
                    amountInWordsText="Indian Rupees Twenty-Eight Thousand One Hundred Ten Only"
                    declarationText={previewDeclaration}
                  />
                </>
              )}
            </div>
          </div>
        </div>

        {/* Drawer footer (actions) */}
        <div className="px-5 py-3 border-t border-gray-200 flex items-center justify-between bg-bv-50">
          <div className="text-xs text-gray-600">
            {exists ? (
              <span>Custom content saved for this entity. <span className="text-bv font-semibold">Revert</span> deletes the override and returns to defaults.</span>
            ) : (
              <span>No custom content yet — defaults are in effect.</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleRevert}
              disabled={saving || (!exists && Object.keys(fields).length === 0)}
              className="px-3 py-1.5 border border-bv text-bv rounded text-sm font-medium hover:bg-bv-100 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1"
            >
              <RotateCcw className="w-3.5 h-3.5" /> Revert
            </button>
            <button
              onClick={handleSave}
              disabled={saving || !dirty}
              className="px-3 py-1.5 bg-bv text-white rounded text-sm font-medium hover:bg-bv/90 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1"
            >
              <Save className="w-3.5 h-3.5" /> {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default PrintTemplateContentEditor;
