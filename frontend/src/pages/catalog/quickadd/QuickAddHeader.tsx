// Quick Add - the editorial header: which mode the page is in, whose account
// the change is recorded under, and the Templates (Clone a product) popover.
// MOVED verbatim out of QuickAddPage.tsx (Wave 3 file diet).

import { ChevronDown, Copy, LayoutTemplate, Loader2, User as UserIcon } from 'lucide-react';
import clsx from 'clsx';
import type { QuickAddForm } from './useQuickAddForm';

export function QuickAddHeader({ form }: { form: QuickAddForm }) {
  const {
    isReviewMode, editMode, displayName, user,
    templatesOpen, setTemplatesOpen,
    cloneSku, setCloneSku, cloning, handleCloneFromSku,
  } = form;

  // Editorial header (mode toggle is rendered by the route shell)
  return (
    <div className="inv-head">
      <div>
        <div className="eyebrow mb-1.5">
          {isReviewMode
            ? 'Catalog · Review import'
            : editMode?.kind === 'spine'
              ? 'Catalog · Edit product'
              : 'Catalog · Add product'}
        </div>
        <h1>
          {isReviewMode
            ? `Reviewing ${displayName || editMode?.sku || 'imported product'}`
            : editMode?.kind === 'spine'
              ? `Editing ${editMode.sku || 'product'}`
              : 'One screen. One SKU. Fast.'}
        </h1>
        <div className="hint">
          {isReviewMode ? (
            <>
              Everything is editable. <kbd className="qa-kbd">Ctrl</kbd>+<kbd className="qa-kbd">Enter</kbd> saves
              fixes; <kbd className="qa-kbd">Ctrl</kbd>+<kbd className="qa-kbd">Shift</kbd>+<kbd className="qa-kbd">Enter</kbd> approves
              for POS and moves to the next item.
            </>
          ) : editMode?.kind === 'spine' ? (
            <>Saving updates this product in place — it will NOT create a new SKU.</>
          ) : (
            <>
              Fill the essentials and hit <kbd className="qa-kbd">Ctrl</kbd>+<kbd className="qa-kbd">Enter</kbd> to save.
              Category sets HSN + GST automatically.
            </>
          )}
        </div>
      </div>

      {/* Wraps at tablet width: the account chip and the Templates button
          drop to a second line instead of squeezing the title. */}
      <div className="flex flex-wrap items-center justify-end gap-3">
        {/* Accountability cue: whose account this create/edit is recorded
            under (several staff catalogue on shared machines). */}
        {(user?.name || user?.email) && (
          <span
            className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-3 py-1.5 text-xs text-gray-700 whitespace-nowrap"
            title="All changes are recorded under this account"
          >
            <UserIcon className="w-3.5 h-3.5 text-gray-500" />
            {editMode ? 'Editing as' : 'Cataloguing as'}{' '}
            <span className="font-semibold">{user?.name?.trim() || user?.email}</span>
          </span>
        )}

      {/* Clone affordance (hidden while editing/reviewing — a clone load
          would silently overwrite the loaded doc) */}
      {!editMode && (
      <div className="relative">
        <button
          type="button"
          onClick={() => setTemplatesOpen((v) => !v)}
          className="btn-secondary flex items-center gap-2"
          aria-expanded={templatesOpen ? "true" : "false"}
          aria-haspopup="dialog"
        >
          <LayoutTemplate className="w-4 h-4" />
          Templates
          <ChevronDown className={clsx('w-4 h-4 transition-transform', templatesOpen && 'rotate-180')} />
        </button>

        {templatesOpen && (
          <>
            {/* Click-away backdrop */}
            <button
              type="button"
              aria-label="Close templates"
              className="fixed inset-0 z-40 cursor-default"
              onClick={() => setTemplatesOpen(false)}
            />
            <div
              role="dialog"
              aria-label="Templates and clone"
              className="absolute right-0 z-50 mt-2 w-[340px] max-w-[92vw] rounded-xl border border-gray-200 bg-white shadow-xl"
            >
              {/* Clone from an existing SKU (owner 2026-10-19: the only option) */}
              <div className="p-4">
                <p className="text-sm font-semibold text-gray-900 mb-2">Clone a product</p>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={cloneSku}
                    onChange={(e) => setCloneSku(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        void handleCloneFromSku();
                      }
                    }}
                    placeholder="Enter SKU or barcode"
                    className="input-field w-full"
                  />
                  <button
                    type="button"
                    onClick={() => handleCloneFromSku()}
                    disabled={cloning}
                    className="btn-secondary shrink-0 flex items-center gap-1.5"
                  >
                    {cloning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Copy className="w-4 h-4" />}
                    Clone
                  </button>
                </div>
                <p className="text-xs text-gray-400 mt-1.5">Prefills the form; saves as a brand-new SKU.</p>
              </div>

            </div>
          </>
        )}
      </div>
      )}
      </div>
    </div>
  );
}
