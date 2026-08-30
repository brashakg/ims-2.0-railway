// ============================================================================
// IMS 2.0 - Settings IA: sections, audience groups, per-section roles
// ============================================================================
// Moved verbatim from SettingsPage.tsx when the tab container was split into
// per-URL pages (Wave 1). The role arrays drive BOTH the nav visibility in
// SettingsLayout and the per-route gates in settingsRoutes — one source.
//
// COUNCIL RULING §3 — taxonomy by AUDIENCE, ~5 buckets, NO "More" orphan.
// Group membership is a TYPED TOTAL MAP (`Record<SettingsTab, GroupId>`):
// every tab MUST name its group, so adding a SettingsTab without assigning
// it a group is a COMPILE ERROR rather than silently falling into a "More"
// bucket (which is how Lens Pricing + Loyalty were previously dropped).

import {
  Users, Tag, Percent, Database, BookOpenCheck,
  Link, Boxes, CircleDot, Layers,
  User, Building2, Receipt, Bell, History, Printer,
  Shield, Bot, Award, Sliders, RotateCcw, ToggleLeft,
} from 'lucide-react';
import type { SettingsTab } from './settingsTypes';

export const SETTINGS_SECTIONS = [
  { id: 'profile' as SettingsTab, label: 'My Profile', icon: User, description: 'Account settings and preferences', role: ['ALL'] },
  { id: 'business' as SettingsTab, label: 'Business Profile', icon: Building2, description: 'Company info and branding', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'users' as SettingsTab, label: 'User Management', icon: Users, description: 'Manage users and roles', role: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
  { id: 'categories' as SettingsTab, label: 'Category Master', icon: Tag, description: 'Product categories and attributes', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  // Backend /admin/brands is gated SUPERADMIN/ADMIN — CATALOG_MANAGER was
  // shown this tab but 403'd on every call, so the tab matches the gate now.
  { id: 'brands' as SettingsTab, label: 'Brand Master', icon: Boxes, description: 'Brands, sub-brands and tier — drives the Catalog brand list', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'lens-master' as SettingsTab, label: 'Lens Master', icon: CircleDot, description: 'Lens brands, indices, coatings', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'lens-enums' as SettingsTab, label: 'Lens Catalog Enums', icon: Layers, description: 'Editable brand/coating/index/material/type lists for the typed catalog', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'catalog-dictionary' as SettingsTab, label: 'Catalog Dictionary', icon: BookOpenCheck, description: 'Allowed values per Add-Product field — only saved values can be chosen in Catalog', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'lens-pricing' as SettingsTab, label: 'Lens Pricing', icon: Receipt, description: 'Range-based tier pricing brackets', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'discounts' as SettingsTab, label: 'Discount Rules', icon: Percent, description: 'Role-based discount limits', role: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'] },
  { id: 'loyalty' as SettingsTab, label: 'Loyalty Programme', icon: Award, description: 'Earn rate, tiers, expiry, redemption rules', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'tax-invoice' as SettingsTab, label: 'Tax & Invoice', icon: Receipt, description: 'GST, invoice numbering', role: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
  { id: 'hsn-rates' as SettingsTab, label: 'HSN & GST Rates', icon: Percent, description: 'Edit GST rate per HSN code (govt revisions)', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'tds-rates' as SettingsTab, label: 'TDS Rates', icon: Percent, description: 'TDS rates on vendor / rent / contractor payments', role: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
  { id: 'policies' as SettingsTab, label: 'Policy Matrix', icon: Sliders, description: 'Scoped operational policies — discount caps, cash variance, refund tiers, promo, reminders (global → entity → store)', role: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT', 'STORE_MANAGER'] },
  { id: 'refund-policy' as SettingsTab, label: 'Refund Policy', icon: RotateCcw, description: 'Refund approval thresholds (auto / admin / superadmin), matrix on/off, original-tender hard-lock, and your approval PIN', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'notifications' as SettingsTab, label: 'Notifications', icon: Bell, description: 'SMS, WhatsApp templates', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'reminders' as SettingsTab, label: 'Reminders', icon: Bell, description: 'Configurable reminder rules — segment, channel, schedule, on/off (config only; no live send)', role: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
  { id: 'integrations' as SettingsTab, label: 'Integrations', icon: Link, description: 'Payment, Tally, Shopify', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'printers' as SettingsTab, label: 'Printers', icon: Printer, description: 'Receipt and label printers', role: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
  { id: 'approvals' as SettingsTab, label: 'Approval Workflows', icon: Shield, description: 'Configure approval rules; set your approval PIN', role: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
  { id: 'agents' as SettingsTab, label: 'AI Agents', icon: Bot, description: 'JARVIS agent control panel', role: ['SUPERADMIN'] },
  { id: 'feature-toggles' as SettingsTab, label: 'Feature Toggles', icon: ToggleLeft, description: 'Enable/disable system features per store', role: ['SUPERADMIN'] },
  { id: 'audit-logs' as SettingsTab, label: 'Audit Logs', icon: History, description: 'Activity history and logs', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'system' as SettingsTab, label: 'System', icon: Database, description: 'Backup, sync, maintenance', role: ['SUPERADMIN', 'ADMIN'] },
];

export type GroupId = 'account' | 'org' | 'catalog' | 'compliance' | 'system';

export const SETTINGS_GROUP_OF: Record<SettingsTab, GroupId> = {
  // My Account
  profile: 'account',
  // Business & Org (the Organization link lives in this group's header)
  business: 'org',
  users: 'org',
  // Catalog & Pricing
  categories: 'catalog',
  brands: 'catalog',
  'lens-master': 'catalog',
  'lens-enums': 'catalog',
  'catalog-dictionary': 'catalog',
  'lens-pricing': 'catalog',
  discounts: 'catalog',
  loyalty: 'catalog',
  // Compliance & Finance
  'tax-invoice': 'compliance',
  'hsn-rates': 'compliance',
  'tds-rates': 'compliance',
  policies: 'compliance',
  'refund-policy': 'compliance',
  // System & Admin
  notifications: 'system',
  reminders: 'system',
  integrations: 'system',
  printers: 'system',
  approvals: 'system',
  agents: 'system',
  'feature-toggles': 'system',
  'audit-logs': 'system',
  system: 'system',
};

export const SETTINGS_GROUPS: Array<{ id: GroupId; label: string }> = [
  { id: 'account',    label: 'My Account' },
  { id: 'org',        label: 'Business & Org' },
  { id: 'catalog',    label: 'Catalog & Pricing' },
  { id: 'compliance', label: 'Compliance & Finance' },
  { id: 'system',     label: 'System & Admin' },
];
