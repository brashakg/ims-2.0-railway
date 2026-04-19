// Canonical big operational number — Inter 600 tabular, tight tracking.
// Never use Instrument Serif for numbers — this is the design rule.

import type { CSSProperties, ReactNode } from 'react';

interface FigureProps {
  children: ReactNode;
  size?: number;            // px; defaults to 38 (matches .figure usage in prototypes)
  color?: string;
  style?: CSSProperties;
  as?: 'span' | 'div';
}

export function Figure({ children, size = 38, color, style, as = 'span' }: FigureProps) {
  const Cmp = as;
  return (
    <Cmp
      className="figure"
      style={{
        fontSize: size,
        lineHeight: 1,
        color,
        ...style,
      }}
    >
      {children}
    </Cmp>
  );
}
