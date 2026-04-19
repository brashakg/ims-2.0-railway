// Priority pills for Tasks & SOPs (P0–P4).
// Visual rules per design: P0 safety, P1 active escalation, P2 today, P3 week, P4 backlog.

import type { ReactNode } from 'react';

export type Priority = 'P0' | 'P1' | 'P2' | 'P3' | 'P4';

interface PriorityPillProps {
  priority: Priority;
  children?: ReactNode; // override label if needed
}

export function PriorityPill({ priority, children }: PriorityPillProps) {
  return <span className={`pill-${priority}`}>{children ?? priority}</span>;
}

// Generic status chip (ok / warn / err / info / accent / neutral)
export type ChipTone = 'neutral' | 'ok' | 'warn' | 'err' | 'info' | 'accent';

interface ChipProps {
  tone?: ChipTone;
  dot?: boolean;
  children: ReactNode;
}

export function Chip({ tone = 'neutral', dot = false, children }: ChipProps) {
  const toneClass = tone === 'neutral' ? '' : ' ' + tone;
  return (
    <span className={'chip' + toneClass}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}
