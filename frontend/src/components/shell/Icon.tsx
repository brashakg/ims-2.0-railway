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
  // Calendar with a check — used for the top-level Attendance nav item.
  calendarCheck: makeIcon('M8 2v4M16 2v4M3 9h18M5 5h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM9 15l2 2 4-4'),
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
  // CRM-14: WhatsApp inbox
  chat: makeIcon('M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'),
  // F39: NBA daily call list (a handset)
  phone: makeIcon('M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z'),
  // --- Distinct money/admin glyphs so each nav destination is recognizable ---
  // (breaks the 9x `banknote` + 3x `settings` reuse in the rail). Finance keeps
  // `banknote`; the rest below give Cash-Register / Cash-Flow / ITC / Blind-EOD /
  // Incentive / Pricing / Salary / Payroll / Expenses / Organization / Staff
  // Onboarding their own marks.
  // Cash register / till drawer — stacked drawer with a coin slot.
  cashRegister: makeIcon('M4 21h16M4 21v-7h16v7M6 14v-3a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v3M9 6h6M10 17h4'),
  // Cash flow — trending-up arrow.
  trendingUp: makeIcon('M3 17l6-6 4 4 7-7M14 8h7v7'),
  // Percent — GST credit / ITC.
  percent: makeIcon('M19 5L5 19M7.5 5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM16.5 14a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5z'),
  // Coins — pricing & offers.
  coins: makeIcon('M8 14a6 6 0 1 0 0-12 6 6 0 0 0 0 12zM16 22a6 6 0 1 0 0-12 6 6 0 0 0 0 12zM8.5 6h.01M16.5 14h.01'),
  // Wallet — expenses.
  wallet: makeIcon('M3 6a2 2 0 0 1 2-2h13v4M3 6v12a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1H5a2 2 0 0 1-2-2zM17 13h.01'),
  // Pay packet / salary — a banknote with a coin (distinct from plain banknote).
  payslip: makeIcon('M3 7h14v8H3zM6 11a2 2 0 1 0 0 .01M20 9v8a2 2 0 0 1-2 2H6'),
  // Calculator — payroll run.
  calculator: makeIcon('M5 3h14a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM8 7h8M8 12h.01M12 12h.01M16 12h.01M8 16h.01M12 16h.01M16 16h.01'),
  // Building — organization (legal entities + stores).
  building: makeIcon('M3 21h18M5 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16M19 21V11a2 2 0 0 0-2-2h-2M9 7h2M9 11h2M9 15h2'),
  // User-plus — staff onboarding (sits next to Users).
  userPlus: makeIcon('M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM19 8v6M22 11h-6'),
};

export type IconName = keyof typeof Icon;
