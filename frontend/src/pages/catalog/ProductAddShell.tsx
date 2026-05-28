// ============================================================================
// IMS 2.0 - Product Add shell (mode switch)
// ============================================================================
// Routes /catalog/add. Defaults to the fast one-screen "Quick Add" (Single);
// `?mode=guided` renders the unchanged step-by-step wizard ("Guided Add");
// `?mode=bulk` renders the Rapid Grid (Phase B) for adding many products fast.
// The mode toggle lives here so every mode shows it and the wizard component
// stays untouched.

import { useSearchParams, Link } from 'react-router-dom';
import clsx from 'clsx';
import { QuickAddPage } from './QuickAddPage';
import { GuidedAddProduct } from './AddProductPage';
import { RapidGridPage } from './RapidGridPage';

type Mode = 'single' | 'guided' | 'bulk';

function ModeToggle({ mode }: { mode: Mode }) {
  return (
    <div className="qa-modeswitch" role="tablist" aria-label="Add product mode">
      <Link
        to="/catalog/add"
        role="tab"
        aria-selected={mode === 'single'}
        className={clsx('qa-modeswitch-btn', mode === 'single' && 'is-active')}
      >
        Single
      </Link>
      <Link
        to="/catalog/add?mode=guided"
        role="tab"
        aria-selected={mode === 'guided'}
        className={clsx('qa-modeswitch-btn', mode === 'guided' && 'is-active')}
      >
        Guided
      </Link>
      <Link
        to="/catalog/add?mode=bulk"
        role="tab"
        aria-selected={mode === 'bulk'}
        className={clsx('qa-modeswitch-btn', mode === 'bulk' && 'is-active')}
      >
        Bulk
      </Link>
    </div>
  );
}

function modeFromParam(value: string | null): Mode {
  if (value === 'guided') return 'guided';
  if (value === 'bulk') return 'bulk';
  return 'single';
}

export function ProductAddShell() {
  const [params] = useSearchParams();
  const mode: Mode = modeFromParam(params.get('mode'));

  return (
    <div className="qa-shell">
      {/* Floating mode switch (top-right). Each mode renders its own editorial
          header; this sits above them. */}
      <div className="qa-shell-toolbar">
        <ModeToggle mode={mode} />
      </div>
      {mode === 'guided' && <GuidedAddProduct />}
      {mode === 'bulk' && <RapidGridPage />}
      {mode === 'single' && <QuickAddPage />}
    </div>
  );
}

export default ProductAddShell;
