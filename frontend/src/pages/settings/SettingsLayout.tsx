// ============================================================================
// IMS 2.0 - Settings module layout
// ============================================================================
// Wave 1 split: the old SettingsPage tab container became real pages, one URL
// per section (/settings/profile, /settings/hsn-rates, …). Owner direction
// 2026-08-30 ("use grouping so it is cleaner"): the rail's five audience
// groups are COLLAPSIBLE — only the group holding the open page stays
// expanded, the rest collapse to a single row with a count, so the rail
// shows ~10 rows instead of 32. A Find-a-setting filter expands every group
// with a match. Canvas mockup: ims-refactor-mockups → "Settings — shell,
// grouped rail".

import { useMemo, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Building2, ChevronDown, ChevronRight, ExternalLink, Search } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import type { UserRole } from '../../types';
import { SETTINGS_SECTIONS, SETTINGS_GROUPS, SETTINGS_GROUP_OF } from './settingsSections';

export function SettingsLayout() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [query, setQuery] = useState('');
  const [toggled, setToggled] = useState<Record<string, boolean>>({});

  // Filter nav by user role — same rule as the old tab container.
  const visibleSections = SETTINGS_SECTIONS.filter(section => {
    if (!user) return false;
    if (section.role.includes('ALL')) return true;
    const userRoles = user.roles || [user.activeRole];
    return section.role.some(role => userRoles.includes(role as UserRole)) || user.activeRole === 'SUPERADMIN';
  });

  const activeId = pathname.split('/')[2] ?? '';
  const activeSection = visibleSections.find(s => s.id === activeId) ?? visibleSections[0];
  const activeGroup = activeSection ? SETTINGS_GROUP_OF[activeSection.id] : undefined;

  const q = query.trim().toLowerCase();
  const matches = useMemo(
    () =>
      q
        ? visibleSections.filter(
            s =>
              s.label.toLowerCase().includes(q) ||
              s.description.toLowerCase().includes(q)
          )
        : visibleSections,
    [q, visibleSections]
  );

  const isGroupOpen = (groupId: string, hasMatches: boolean) => {
    if (q) return hasMatches; // searching opens every group with a match
    if (groupId in toggled) return toggled[groupId];
    return groupId === activeGroup; // only the working group stays open
  };

  return (
    <div className="setup-grid">
      {/* Left nav — collapsible audience groups, one row per settings page */}
      <nav className="s-nav">
        <div className="s-nav-top">
          <span className="eyebrow">Configuration</span>
          <span className="s-nav-count">{visibleSections.length}</span>
        </div>
        <div className="s-nav-find">
          <Search className="s-nav-icon" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Find a setting…"
            aria-label="Find a setting"
          />
        </div>
        {SETTINGS_GROUPS.map((group) => {
          const groupSections = visibleSections.filter((s) => SETTINGS_GROUP_OF[s.id] === group.id);
          if (groupSections.length === 0) return null;
          const groupMatches = matches.filter((s) => SETTINGS_GROUP_OF[s.id] === group.id);
          if (q && groupMatches.length === 0) return null;
          const open = isGroupOpen(group.id, groupMatches.length > 0);
          const shown = q ? groupMatches : groupSections;
          return (
            <div key={group.id}>
              <button
                type="button"
                className={'s-nav-group-btn' + (open ? ' open' : '')}
                onClick={() =>
                  setToggled(prev => ({ ...prev, [group.id]: !open }))
                }
                aria-expanded={open}
              >
                {open ? (
                  <ChevronDown className="s-nav-chev" />
                ) : (
                  <ChevronRight className="s-nav-chev" />
                )}
                <span className="s-nav-group-label">{group.label}</span>
                <span className="s-nav-count">{groupSections.length}</span>
              </button>
              {open && shown.map((section) => {
                const IconCmp = section.icon;
                return (
                  <NavLink
                    key={section.id}
                    to={`/settings/${section.id}`}
                    className={({ isActive }) => 's-nav-item' + (isActive ? ' on' : '')}
                    title={section.description}
                  >
                    <IconCmp className="s-nav-icon" />
                    <span className="s-nav-label">{section.label}</span>
                  </NavLink>
                );
              })}
              {/* Business & Org links out to the canonical Organization screen
                  (entities + stores live there, not here). */}
              {group.id === 'org' && open && !q && (
                <button
                  type="button"
                  onClick={() => navigate('/organization')}
                  className="s-nav-item"
                  title="Manage legal entities and stores (canonical screen)"
                >
                  <Building2 className="s-nav-icon" />
                  <span className="s-nav-label">Organization</span>
                  <ExternalLink className="s-nav-icon" style={{ marginLeft: 'auto', opacity: 0.5 }} />
                </button>
              )}
            </div>
          );
        })}
        {user?.activeRole === 'SUPERADMIN' && (
          <>
            <div className="divider" />
            <span className="eyebrow">Superadmin</span>
            <div style={{ padding: '0 10px', fontSize: 11, color: 'var(--ink-4)' }}>
              Elevated mode — all changes audit-logged.
            </div>
          </>
        )}
      </nav>

      {/* Content */}
      <div className="s-content">
        <div className="s-head">
          <span className="eyebrow" style={{ display: 'block', marginBottom: 6 }}>
            {activeSection?.description ?? ''}
          </span>
          <h1>{activeSection?.label ?? 'Store Setup'}</h1>
          <p className="sub">
            System configuration and master data management. Some settings are <strong>locked at HQ level</strong> — changes require superadmin approval and are recorded in audit.
          </p>
        </div>

        <div className="s-section-body">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
