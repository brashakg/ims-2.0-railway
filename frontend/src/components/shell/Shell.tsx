// Shell wrapper — Rail + Topbar + page body.
// Used by AppLayout; also usable directly if a page needs custom crumbs/actions.

import type { ReactNode } from 'react';
import { Rail } from './Rail';
import { Topbar, type Crumb } from './Topbar';

interface ShellProps {
  crumbs?: Crumb[];
  actions?: ReactNode;
  brand?: 'bv' | 'wizopt';
  children: ReactNode;
}

export function Shell({ crumbs, actions, brand, children }: ShellProps) {
  return (
    <div className="app-shell" data-brand={brand ?? 'bv'}>
      <Rail brand={brand} />
      <div className="app-main">
        <Topbar crumbs={crumbs} actions={actions} />
        <div className="page-body">{children}</div>
      </div>
    </div>
  );
}
