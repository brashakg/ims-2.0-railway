/* global React */
// Shared shell chrome: Rail + Topbar. Exposes window.Shell, window.Icon.

const { useState, useEffect, useRef } = React;

/* ── Icons (line) ──────────────────────────────────────────── */
const I = (path) => (props = {}) =>
  React.createElement('svg', {
    viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round',
    ...props
  }, React.createElement('path', { d: path }));

const Icon = {
  home:    I('M3 11l9-8 9 8M5 10v10h14V10'),
  cart:    I('M3 4h2l2.4 12.2a2 2 0 0 0 2 1.8h7.2a2 2 0 0 0 2-1.6L21 8H6M9 22a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM18 22a1 1 0 1 0 0-2 1 1 0 0 0 0 2z'),
  eye:     I('M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'),
  box:     I('M21 8l-9-5-9 5 9 5 9-5zM3 8v9l9 5 9-5V8M12 13v9'),
  check:   I('M9 11l3 3 8-8M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0z'),
  chart:   I('M3 3v18h18M7 15l4-4 3 3 6-6'),
  cpu:     I('M9 3v3M15 3v3M9 18v3M15 18v3M3 9H.01M3 15H.01M21 9h.01M21 15h.01M6 6h12v12H6z M10 10h4v4h-4z'),
  settings:I('M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z'),
  bell:    I('M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0'),
  search:  I('M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3'),
  plus:    I('M12 5v14M5 12h14'),
  chevron: I('M9 6l6 6-6 6'),
  chevronDown: I('M6 9l6 6 6-6'),
  x:       I('M6 6l12 12M18 6l6 12'),
  clipboard:I('M9 3h6a1 1 0 0 1 1 1v2H8V4a1 1 0 0 1 1-1zM8 6H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-2'),
  ticket:  I('M3 10V7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3a2 2 0 0 0 0-4zM12 5v14'),
  file:    I('M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6zM14 3v6h6'),
  zap:     I('M13 2L3 14h9l-1 8 10-12h-9l1-8z'),
  calendar:I('M8 2v4M16 2v4M3 9h18M5 5h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z'),
  user:    I('M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z'),
  printer: I('M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6z'),
  lock:    I('M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4'),
  store:   I('M3 9l2-5h14l2 5M3 9v11a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V9M3 9h18M9 21V13h6v8'),
};

/* ── Left rail ─────────────────────────────────────────────── */
function Rail({ active, brand = 'bv' }) {
  const items = [
    { id: 'hub',       label: 'Hub',            icon: Icon.home,     href: 'hub.html' },
    { id: 'pos',       label: 'POS',            icon: Icon.cart,     href: 'pos.html' },
    { id: 'clinical',  label: 'Clinical',       icon: Icon.eye,      href: 'clinical.html' },
    { id: 'inventory', label: 'Inventory',      icon: Icon.box,      href: 'inventory.html' },
    { id: 'tasks',     label: 'Tasks & SOPs',   icon: Icon.check,    href: 'tasks.html' },
    { id: 'reports',   label: 'Reports',        icon: Icon.chart,    href: 'reports.html' },
    { id: 'print',     label: 'Print',          icon: Icon.printer,  href: 'print.html' },
    { id: 'jarvis',    label: 'Jarvis',         icon: Icon.cpu,      href: 'jarvis.html' },
    { id: 'setup',     label: 'Store Setup',    icon: Icon.settings, href: 'setup.html' },
  ];
  const glyph = brand === 'wizopt' ? 'W' : 'B';
  return (
    <aside className="rail">
      <div className="brand" title="Better Vision">{glyph}</div>
      {items.map((it, i) => {
        const isActive = it.id === active;
        const IconEl = it.icon;
        return (
          <React.Fragment key={it.id}>
            <a className={'rail-item' + (isActive ? ' active' : '')} href={it.href} title={it.label}>
              <IconEl />
              <span className="rail-label">{it.label}</span>
            </a>
            {i === 0 && <div className="rail-sep" />}
            {i === 6 && <div className="rail-sep" />}
          </React.Fragment>
        );
      })}
      <div className="rail-spacer" />
      <div className="rail-avatar" title="Sonia K. • Store Manager">SK</div>
    </aside>
  );
}

/* ── Top bar ──────────────────────────────────────────────── */
function Topbar({ crumbs = [], actions, role = 'Manager', store = 'BV-DELHI-GK1', storeName = 'GK-I Flagship' }) {
  return (
    <header className="topbar no-print">
      <div className="crumbs">
        {crumbs.map((c, i) => (
          <React.Fragment key={i}>
            {i > 0 && <span className="sep"><Icon.chevron width="12" height="12" /></span>}
            <span className={i === crumbs.length - 1 ? 'cur' : ''}>{c}</span>
          </React.Fragment>
        ))}
      </div>
      <div className="spacer" />
      <div className="cmdk" onClick={() => {}}>
        <Icon.search width="14" height="14" />
        <span>Search or jump to…</span>
        <span className="kbd">⌘K</span>
      </div>
      <button className="store-pill">
        <span className="dot" />
        <span>{storeName}</span>
        <span className="code">· {store}</span>
        <Icon.chevronDown width="12" height="12" />
      </button>
      <span className="role-pill"><span className="k">Role</span>{role}</span>
      <button className="btn icon ghost" title="Notifications"><Icon.bell /></button>
      {actions}
    </header>
  );
}

/* ── Shell wrapper ─────────────────────────────────────────── */
function Shell({ active, crumbs, actions, role, store, storeName, children, brand }) {
  return (
    <div className="app" data-brand={brand || 'bv'}>
      <Rail active={active} brand={brand} />
      <div className="main">
        <Topbar crumbs={crumbs} actions={actions} role={role} store={store} storeName={storeName} />
        {children}
      </div>
    </div>
  );
}

/* ── Tweaks helper ─────────────────────────────────────────── */
function useTweaks(defaults) {
  const [vals, setVals] = useState(defaults);
  const [on, setOn] = useState(false);
  useEffect(() => {
    const h = (e) => {
      if (e.data?.type === '__activate_edit_mode') { setOn(true); document.body.classList.add('tweaks-on'); }
      if (e.data?.type === '__deactivate_edit_mode') { setOn(false); document.body.classList.remove('tweaks-on'); }
    };
    window.addEventListener('message', h);
    window.parent?.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', h);
  }, []);
  const set = (patch) => {
    setVals(v => ({ ...v, ...patch }));
    window.parent?.postMessage({ type: '__edit_mode_set_keys', edits: patch }, '*');
  };
  return [vals, set, on];
}

/* Segmented control */
function Seg({ value, onChange, options }) {
  return (
    <div className="seg">
      {options.map(o => (
        <button key={o} className={value === o ? 'on' : ''} onClick={() => onChange(o)}>{o}</button>
      ))}
    </div>
  );
}

Object.assign(window, { Icon, Shell, Rail, Topbar, Seg, useTweaks });
