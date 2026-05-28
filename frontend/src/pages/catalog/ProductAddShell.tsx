// ============================================================================
// IMS 2.0 - Product Add shell (mode switch)
// ============================================================================
// Routes /catalog/add. Defaults to the fast one-screen "Quick Add" (Single);
// `?mode=guided` renders the unchanged step-by-step wizard ("Guided Add"). The
// mode toggle lives here so both modes show it and the wizard component stays
// untouched. Phase B's "Bulk" rapid-grid tab slots into the toggle later.

import { useSearchParams, Link } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';
import clsx from 'clsx';
import { QuickAddPage } from './QuickAddPage';
import { GuidedAddProduct } from './AddProductPage';

type Mode = 'single' | 'guided';

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
      <span className="qa-modeswitch-btn is-disabled" aria-disabled title="Bulk rapid-grid — coming soon">
        Bulk
        <ExternalLink className="w-3 h-3 ml-1 opacity-50" />
      </span>
    </div>
  );
}

export function ProductAddShell() {
  const [params] = useSearchParams();
  const mode: Mode = params.get('mode') === 'guided' ? 'guided' : 'single';

  return (
    <div className="qa-shell">
      {/* Floating mode switch (top-right). Both modes render the editorial
          header themselves; this sits above them. */}
      <div className="qa-shell-toolbar">
        <ModeToggle mode={mode} />
      </div>
      {mode === 'guided' ? <GuidedAddProduct /> : <QuickAddPage />}
    </div>
  );
}

export default ProductAddShell;
