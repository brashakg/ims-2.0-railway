// ============================================================================
// IMS 2.0 - Settings module layout
// ============================================================================
// Wave 1 split: the old SettingsPage tab container became real pages, one URL
// per section (/settings/profile, /settings/hsn-rates, …). This layout keeps
// the grouped left nav (role-filtered, same visibility rule as before) and
// the section header; each section page owns its own data.

import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Building2, ExternalLink } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import type { UserRole } from '../../types';
import { SETTINGS_SECTIONS, SETTINGS_GROUPS, SETTINGS_GROUP_OF } from './settingsSections';

export function SettingsLayout() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  // Filter nav by user role — same rule as the old tab container.
  const visibleSections = SETTINGS_SECTIONS.filter(section => {
    if (!user) return false;
    if (section.role.includes('ALL')) return true;
    const userRoles = user.roles || [user.activeRole];
    return section.role.some(role => userRoles.includes(role as UserRole)) || user.activeRole === 'SUPERADMIN';
  });

  const activeId = pathname.split('/')[2] ?? '';
  const activeSection = visibleSections.find(s => s.id === activeId) ?? visibleSections[0];

  return (
    <div className="setup-grid">
      {/* Left nav — 240px, sections bucketed into 5 functional groups */}
      <nav className="s-nav">
        <span className="eyebrow">Configuration · {visibleSections.length} sections</span>
        {SETTINGS_GROUPS.map((group) => {
          const groupSections = visibleSections.filter((s) => SETTINGS_GROUP_OF[s.id] === group.id);
          if (groupSections.length === 0) return null;
          return (
            <div key={group.id}>
              <span className="s-nav-group">{group.label}</span>
              {groupSections.map((section) => {
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
              {group.id === 'org' && (
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
