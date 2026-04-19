// Shared icon map for the shell.
// 24×24 viewBox, 1.6 stroke, round caps — matches design_handoff_ims_2_0/shell/shell.jsx
// Add new icons here as needed; don't sprinkle lucide imports across pages.

import type { SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement>;

const makeIcon = (path: string) =>
  function I(props: IconProps) {
    return (
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        width={20}
        height={20}
        {...props}
      >
        <path d={path} />
      </svg>
    );
  };

export const Icon = {
  home: makeIcon('M3 11l9-8 9 8M5 10v10h14V10'),
  cart: makeIcon('M3 4h2l2.4 12.2a2 2 0 0 0 2 1.8h7.2a2 2 0 0 0 2-1.6L21 8H6M9 22a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM18 22a1 1 0 1 0 0-2 1 1 0 0 0 0 2z'),
  eye: makeIcon('M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'),
  box: makeIcon('M21 8l-9-5-9 5 9 5 9-5zM3 8v9l9 5 9-5V8M12 13v9'),
  check: makeIcon('M9 11l3 3 8-8M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0z'),
  chart: makeIcon('M3 3v18h18M7 15l4-4 3 3 6-6'),
  cpu: makeIcon('M9 3v3M15 3v3M9 18v3M15 18v3M3 9H.01M3 15H.01M21 9h.01M21 15h.01M6 6h12v12H6z M10 10h4v4h-4z'),
  settings: makeIcon('M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z'),
  bell: makeIcon('M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0'),
  search: makeIcon('M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3'),
  plus: makeIcon('M12 5v14M5 12h14'),
  chevron: makeIcon('M9 6l6 6-6 6'),
  chevronDown: makeIcon('M6 9l6 6 6-6'),
  x: makeIcon('M6 6l12 12M18 6l6 12'),
  clipboard: makeIcon('M9 3h6a1 1 0 0 1 1 1v2H8V4a1 1 0 0 1 1-1zM8 6H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-2'),
  ticket: makeIcon('M3 10V7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3a2 2 0 0 0 0-4zM12 5v14'),
  file: makeIcon('M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6zM14 3v6h6'),
  zap: makeIcon('M13 2L3 14h9l-1 8 10-12h-9l1-8z'),
  calendar: makeIcon('M8 2v4M16 2v4M3 9h18M5 5h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z'),
  user: makeIcon('M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z'),
  printer: makeIcon('M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6z'),
  lock: makeIcon('M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4'),
  store: makeIcon('M3 9l2-5h14l2 5M3 9v11a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V9M3 9h18M9 21V13h6v8'),
  // Added for expanded nav beyond the design's 9
  users: makeIcon('M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75'),
  truck: makeIcon('M1 3h15v13H1zM16 8h4l3 3v5h-7zM5.5 21a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18.5 21a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z'),
  wrench: makeIcon('M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z'),
  tag: makeIcon('M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82zM7 7h.01'),
  megaphone: makeIcon('M3 11l18-5v13L3 14v-3zM11.6 16.8a3 3 0 1 1-5.8-1.6'),
  banknote: makeIcon('M2 6h20v12H2zM12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM6 9h.01M18 15h.01'),
  receipt: makeIcon('M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1zM16 8H8M16 12H8M13 16H8'),
  refresh: makeIcon('M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5'),
  // Marvel/DC glyphs for agent avatars — not used in rail but useful for Jarvis page
  shield: makeIcon('M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z'),
};

export type IconName = keyof typeof Icon;
