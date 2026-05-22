// Per-category section visibility map for the Edit Product redesign.
// Each entry tells the new shell which sections (after Identity) to render
// and what their nav labels should be. Pricing → Inventory → Images →
// Publish are universal and always last; categorySpecs only handles the
// middle band between Identity and the universal tail.
//
// Ported from CLAUDE_CODE_HANDOFF.md §5.

export type SectionId =
  | "identity"
  | "frame"
  | "lens"
  | "power"        // reading glasses
  | "safety"       // safety glasses certification
  | "contact"      // contact lens spec
  | "packSize"     // contact lens pack size
  | "watch"        // watch specs (movement, case, water resist)
  | "smart"        // smart features (battery, connectivity)
  | "strap"        // watch / smartwatch strap
  | "details"
  | "pricing"
  | "inventory"
  | "media"
  | "publish";

export interface SectionSpec {
  id: SectionId;
  label: string;
  /** Optional hint surfaced in the SectionNav as a small subdued line. */
  hint?: string;
  /** When true, show a red dot in the nav until at least one required field
   *  in this section has a value. The actual required fields live in
   *  validation.ts → getIssues(). */
  required?: boolean;
}

export interface CategorySpec {
  /** UI label for chips and nav. */
  label: string;
  /** Sections specific to this category, between Identity and the universal
   *  Pricing/Inventory/Media/Publish tail. */
  sections: SectionSpec[];
}

const FRAME: SectionSpec = { id: "frame", label: "Frame" };
const LENS: SectionSpec = { id: "lens", label: "Lens" };
const LENS_UV: SectionSpec = { id: "lens", label: "Lens", hint: "UV protection required", required: true };
const LENS_BLUE: SectionSpec = { id: "lens", label: "Lens", hint: "Blue-block emphasis" };
const POWER: SectionSpec = { id: "power", label: "Power", hint: "Required", required: true };
const SAFETY: SectionSpec = { id: "safety", label: "Safety certification", hint: "Required", required: true };
const CONTACT: SectionSpec = { id: "contact", label: "Lens spec", hint: "BC, DIA, power, replacement", required: true };
const PACK: SectionSpec = { id: "packSize", label: "Pack size" };
const WATCH: SectionSpec = { id: "watch", label: "Watch specs", hint: "Movement, case, water resist", required: true };
const SMART: SectionSpec = { id: "smart", label: "Smart features", hint: "Battery, connectivity" };
const STRAP: SectionSpec = { id: "strap", label: "Strap" };
const DETAILS: SectionSpec = { id: "details", label: "Details" };

export const CAT_SPECS: Record<string, CategorySpec> = {
  SPECTACLES:        { label: "Spectacles",        sections: [FRAME, LENS, DETAILS] },
  CLIP_ON_FRAMES:    { label: "Clip-On Frames",    sections: [FRAME, DETAILS] },
  SUNGLASSES:        { label: "Sunglasses",        sections: [FRAME, LENS_UV, DETAILS] },
  READING_GLASSES:   { label: "Reading Glasses",   sections: [FRAME, LENS, POWER, DETAILS] },
  COMPUTER_GLASSES:  { label: "Computer Glasses",  sections: [FRAME, LENS_BLUE, DETAILS] },
  SAFETY_GLASSES:    { label: "Safety Glasses",    sections: [FRAME, SAFETY, DETAILS] },
  CONTACT_LENSES:    { label: "Contact Lenses",    sections: [CONTACT, PACK, DETAILS] },
  SMARTGLASSES:      { label: "Smartglasses",      sections: [FRAME, LENS, SMART, DETAILS] },
  WATCHES:           { label: "Watches",           sections: [WATCH, STRAP, DETAILS] },
  SMARTWATCHES:      { label: "Smartwatches",      sections: [WATCH, SMART, STRAP, DETAILS] },
  ACCESSORIES:       { label: "Accessories",       sections: [DETAILS] },
};

/** Universal sections that are always last, always in this order. */
export const UNIVERSAL_TAIL: SectionSpec[] = [
  { id: "pricing",   label: "Pricing" },
  { id: "inventory", label: "Inventory" },
  { id: "media",     label: "Images" },
  { id: "publish",   label: "Publish" },
];

/** Always-first section. */
export const IDENTITY: SectionSpec = { id: "identity", label: "Identity", required: true };

/** Build the full ordered nav for a given category. */
export function navForCategory(category: string | null | undefined): SectionSpec[] {
  const spec = (category && CAT_SPECS[category]) || CAT_SPECS.SPECTACLES;
  return [IDENTITY, ...spec.sections, ...UNIVERSAL_TAIL];
}

/** All known category keys, in display order. */
export const CATEGORY_ORDER: Array<keyof typeof CAT_SPECS> = [
  "SPECTACLES",
  "SUNGLASSES",
  "CLIP_ON_FRAMES",
  "READING_GLASSES",
  "COMPUTER_GLASSES",
  "SAFETY_GLASSES",
  "CONTACT_LENSES",
  "SMARTGLASSES",
  "WATCHES",
  "SMARTWATCHES",
  "ACCESSORIES",
];
