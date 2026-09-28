// Shared by the two cataloguing pages (scorecard + QC review).

/** "COLORED_CONTACT_LENS" -> "Colored contact lens" */
export function prettyCategory(cat: string): string {
  const s = String(cat || '').replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}
