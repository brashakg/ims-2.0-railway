// ============================================================================
// IMS 2.0 - Tick-box multi-select: a compact dropdown, one tick box per option
// ============================================================================
// Owner 2026-11-01 ("make ALL filters here multiselect"). The value is ONE
// comma-separated string ('Black,Gold'), so it drops straight into the page
// address and the request. A ticked value the options no longer list (a shared
// link) stays shown and can be unticked. The panel stays in the page while
// closed, `hidden`.

import { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import clsx from 'clsx';

export type MultiOption = { value: string; label?: string; count?: number };

/** Long lists get a search box. */
const SEARCH_FROM = 8;
/** The panel's width (w-64), to tell whether it fits to the right. */
const PANEL_PX = 256;

const split = (s: string) => (s ? s.split(',') : []);

export function MultiSelect({
  title,
  anyLabel,
  value,
  options,
  onChange,
  width = 'w-40',
}: {
  title: string;
  /** Shown when nothing is ticked ('Any colour', 'Anyone'). */
  anyLabel: string;
  value: string;
  options: MultiOption[] | undefined;
  onChange: (next: string) => void;
  width?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  // A box near the right edge (phone widths) opens its panel leftwards.
  const [alignRight, setAlignRight] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  // Closing from inside the panel hands focus back to the button, or the
  // focused tick box goes hidden and focus falls to the top of the page.
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    // The side it opens to was measured for this width; a resize closes it.
    const shut = () => setOpen(false);
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    window.addEventListener('resize', shut);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', esc);
      window.removeEventListener('resize', shut);
    };
  }, [open]);

  const picked = split(value);
  const known = options || [];
  const list: MultiOption[] = [
    ...known,
    ...picked.filter((v) => !known.some((o) => o.value === v)).map((v) => ({ value: v })),
  ];
  const name = (o: MultiOption) => o.label || o.value;
  const nameOf = (v: string) => name(list.find((o) => o.value === v) || { value: v });
  const needle = q.trim().toLowerCase();
  const shown = needle ? list.filter((o) => name(o).toLowerCase().includes(needle)) : list;
  const summary =
    picked.length === 0 ? anyLabel : picked.length === 1 ? nameOf(picked[0]) : `${picked.length} selected`;
  const toggle = (v: string) =>
    onChange((picked.includes(v) ? picked.filter((x) => x !== v) : [...picked, v]).join(','));

  return (
    <div
      ref={box}
      onBlur={(e) => {
        // Tabbing out of the box closes it, so two panels never stack.
        if (!box.current?.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
      className="relative flex flex-col gap-1 text-[11px] font-medium uppercase tracking-[.06em] text-gray-500"
    >
      <span>{title}</span>
      <button
        type="button"
        ref={trigger}
        aria-label={title}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => {
          const r = trigger.current?.getBoundingClientRect();
          setAlignRight(!!r && r.left + PANEL_PX > window.innerWidth);
          setOpen((o) => !o);
        }}
        className={clsx(
          'input-field flex items-center justify-between gap-1 text-left normal-case tracking-normal',
          picked.length > 0 && 'border-gray-500 text-gray-900',
          width
        )}
      >
        <span className="truncate">{summary}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-gray-400" />
      </button>
      <div
        role="group"
        aria-label={`${title} options`}
        hidden={!open}
        className={clsx(
          'absolute top-full z-30 mt-1 w-64 max-w-[calc(100vw-2rem)] rounded-lg border border-gray-200 bg-white p-2 text-sm font-normal normal-case tracking-normal text-gray-800 shadow-lg',
          alignRight ? 'right-0' : 'left-0'
        )}
      >
        {list.length > SEARCH_FROM && (
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search"
            aria-label={`Search ${title}`}
            className="input-field mb-1 w-full"
          />
        )}
        <div className="max-h-64 overflow-y-auto">
          {shown.map((o) => (
            <label
              key={o.value}
              className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 hover:bg-gray-50"
            >
              <input
                type="checkbox"
                checked={picked.includes(o.value)}
                onChange={() => toggle(o.value)}
                className="h-4 w-4 shrink-0 accent-gray-800"
              />
              <span className="min-w-0 flex-1 truncate">
                {`${name(o)}${o.count === undefined ? '' : ` (${o.count.toLocaleString('en-IN')})`}`}
              </span>
            </label>
          ))}
          {shown.length === 0 && <p className="px-1.5 py-1 text-gray-500">No match</p>}
        </div>
        <div className="mt-1 flex items-center justify-between border-t border-gray-100 pt-2">
          <button
            type="button"
            onClick={() => onChange('')}
            disabled={picked.length === 0}
            className="text-sm font-medium text-blue-700 underline disabled:text-gray-400 disabled:no-underline"
          >
            Clear
          </button>
          <button
            type="button"
            onClick={close}
            className="rounded-md bg-gray-900 px-3 py-1 text-sm font-medium text-white"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
