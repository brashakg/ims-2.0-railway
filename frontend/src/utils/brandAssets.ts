/**
 * IMS 2.0 — Brand asset resolver
 * ================================
 * Returns the correct logo set for the active brand.
 * All assets live in frontend/src/assets/brand/ and are bundled by Vite
 * (fingerprinted for cache-busting).
 *
 * BV  (Better Vision)  — brand color: red  (#B42318)
 * WizOpt               — brand color: blue (#1A56DB-ish)
 */

// ---------- BV assets ----------
import bvMark from '../assets/brand/bv-mark.png';
import bvMark64 from '../assets/brand/bv-mark-64.png';
import bvLockup from '../assets/brand/bv-lockup.png';
import bvLockupHiRes from '../assets/brand/bv-lockup-hires.png';
import bvWhite from '../assets/brand/bv-white.png';
import bvBlack from '../assets/brand/bv-black.png';

// ---------- WizOpt assets ----------
import wizoptMark from '../assets/brand/wizopt-mark.svg';
import wizoptMarkWhite from '../assets/brand/wizopt-mark-white.png';
import wizoptMarkBlue from '../assets/brand/wizopt-mark-blue.png';
import wizoptMarkBlack from '../assets/brand/wizopt-mark-black.png';

export interface BrandAssets {
  /** Square mark / icon (512 px). Use as app icon, rail badge. */
  mark: string;
  /** Small mark (64 px). Rail collapsed view. */
  mark64: string;
  /** White knockout of the mark. For dark/red rail backgrounds. */
  markWhite: string;
  /** Solid black mark. For 1-bit thermal printers. */
  markBlack: string;
  /** Horizontal lockup (mark + wordmark). For login page, A4 prints. */
  lockup: string;
  /** Hi-res lockup (300 DPI). For GST invoice, payslip, PO, GRN headers. */
  lockupHiRes: string;
  /** Brand primary color (hex). Matches CSS --bv / --wizopt tokens. */
  color: string;
  /** Full brand name. */
  name: string;
}

const BV_ASSETS: BrandAssets = {
  mark: bvMark,
  mark64: bvMark64,
  markWhite: bvWhite,
  markBlack: bvBlack,
  lockup: bvLockup,
  lockupHiRes: bvLockupHiRes,
  color: '#B42318',
  name: 'Better Vision',
};

// WizOpt has a mark but no supplied wordmark lockup — Rail.tsx renders the
// mark + "WizOpt" text via CSS (Krona One font). hi-res lockup falls back to
// the color SVG mark for print; this can be upgraded once a lockup PNG is
// supplied.
const WIZOPT_ASSETS: BrandAssets = {
  mark: wizoptMarkBlue,
  mark64: wizoptMarkBlue,
  markWhite: wizoptMarkWhite,
  markBlack: wizoptMarkBlack,
  lockup: wizoptMark,          // SVG mark (scaled by print template)
  lockupHiRes: wizoptMark,     // same until a dedicated lockup is supplied
  color: '#1E40AF',
  name: 'WizOpt',
};

/**
 * Return the logo/asset set for the given brand string.
 * Falls back to BV for any unknown / empty value.
 */
export function getBrandAssets(brand?: string | null): BrandAssets {
  return brand?.toLowerCase() === 'wizopt' ? WIZOPT_ASSETS : BV_ASSETS;
}
