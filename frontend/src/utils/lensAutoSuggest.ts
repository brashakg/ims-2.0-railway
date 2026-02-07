// ============================================================================
// IMS 2.0 - Lens Auto-Suggestion Engine
// ============================================================================
// Intelligent lens recommendations based on prescription data
// Tailored for Indian optical retail with INR pricing

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface LensSuggestion {
  id: string;
  lensType: string;
  material: string;
  coatings: string[];
  priceRange: { min: number; max: number };
  reason: string;
  priority: 'PRIMARY' | 'UPGRADE' | 'OPTIONAL';
}

export interface PrescriptionInput {
  rightSphere: number | null;
  rightCylinder: number | null;
  rightAxis: number | null;
  rightAdd: number | null;
  leftSphere: number | null;
  leftCylinder: number | null;
  leftAxis: number | null;
  leftAdd: number | null;
  patientAge?: number;
  lifestyle?: 'STUDENT' | 'OFFICE' | 'OUTDOOR' | 'DRIVER' | 'GENERAL';
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/** Return the larger absolute value across both eyes for a given field. */
function maxAbsField(
  left: number | null,
  right: number | null,
): number {
  const l = left !== null ? Math.abs(left) : 0;
  const r = right !== null ? Math.abs(right) : 0;
  return Math.max(l, r);
}

/** Determine whether ADD power is present (i.e. near-vision correction needed). */
function hasAddPower(rx: PrescriptionInput): boolean {
  return (rx.rightAdd !== null && rx.rightAdd > 0) ||
         (rx.leftAdd !== null && rx.leftAdd > 0);
}

let _idCounter = 0;
function nextId(): string {
  _idCounter += 1;
  return `lens-sug-${_idCounter}`;
}

// ---------------------------------------------------------------------------
// Price tables (INR)
// ---------------------------------------------------------------------------

const PRICE = {
  // Material base prices (per pair)
  CR39_SINGLE:          { min: 800,  max: 1500  },
  POLY_SINGLE:          { min: 1200, max: 2500  },
  TRIVEX_SINGLE:        { min: 1800, max: 3500  },
  HI_INDEX_167_SINGLE:  { min: 2500, max: 5000  },
  HI_INDEX_174_SINGLE:  { min: 4000, max: 8000  },

  CR39_BIFOCAL:         { min: 1500, max: 3000  },
  POLY_BIFOCAL:         { min: 2000, max: 4000  },

  CR39_PROGRESSIVE:     { min: 3000, max: 6000  },
  POLY_PROGRESSIVE:     { min: 4000, max: 8000  },
  HI_INDEX_167_PROG:    { min: 6000, max: 12000 },
  HI_INDEX_174_PROG:    { min: 8000, max: 15000 },

  // Coating add-on prices (approx per pair)
  COATING_AR:           { min: 300,  max: 800   },
  COATING_BLUE_CUT:     { min: 500,  max: 1500  },
  COATING_PHOTOCHROMIC: { min: 1000, max: 3000  },
  COATING_HARD_COAT:    { min: 200,  max: 500   },
} as const;

function addPriceRanges(
  ...ranges: { min: number; max: number }[]
): { min: number; max: number } {
  let min = 0;
  let max = 0;
  for (const r of ranges) {
    min += r.min;
    max += r.max;
  }
  return { min, max };
}

// ---------------------------------------------------------------------------
// Suggestion engine
// ---------------------------------------------------------------------------

export function suggestLenses(rx: PrescriptionInput): LensSuggestion[] {
  // Reset id counter per call so IDs are stable for a given invocation.
  _idCounter = 0;

  const suggestions: LensSuggestion[] = [];

  const maxSph = maxAbsField(rx.leftSphere, rx.rightSphere);
  const maxCyl = maxAbsField(rx.leftCylinder, rx.rightCylinder);
  const addPresent = hasAddPower(rx);
  const age = rx.patientAge ?? null;
  const lifestyle = rx.lifestyle ?? 'GENERAL';

  // Determine whether this is a child/teen
  const isChild = age !== null && age < 18;
  // Determine screen-heavy usage (office workers, students 20+)
  const isScreenUser =
    lifestyle === 'OFFICE' ||
    lifestyle === 'STUDENT' ||
    (lifestyle === 'GENERAL' && age !== null && age >= 20 && age <= 45);
  const isOutdoor = lifestyle === 'OUTDOOR' || lifestyle === 'DRIVER';

  // --------------------------------------------------
  // 1. Determine lens type (Single Vision / Bifocal / Progressive)
  // --------------------------------------------------

  if (addPresent) {
    // Near-vision correction needed
    const preferProgressive = age === null || age >= 40;

    if (preferProgressive) {
      // --- PRIMARY: Progressive ---
      const material = pickMaterial(maxSph, isChild);
      const basePrice = progressiveBasePrice(material);
      const coatings = pickCoatings(isScreenUser, isOutdoor, false);
      const coatingPrice = coatingsPriceRange(coatings);

      suggestions.push({
        id: nextId(),
        lensType: 'Progressive',
        material,
        coatings,
        priceRange: addPriceRanges(basePrice, coatingPrice),
        reason:
          'ADD power detected with age suited for progressive lenses. Provides seamless near, intermediate and distance vision without visible line.',
        priority: 'PRIMARY',
      });

      // --- UPGRADE: Premium progressive with better material ---
      if (maxSph > 4 && material !== 'Hi-Index 1.74') {
        const upgMaterial = maxSph > 6 ? 'Hi-Index 1.74' : 'Hi-Index 1.67';
        const upgBase = progressiveBasePrice(upgMaterial);
        const upgCoatings = pickCoatings(true, isOutdoor, false);
        const upgCoatingPrice = coatingsPriceRange(upgCoatings);
        suggestions.push({
          id: nextId(),
          lensType: 'Progressive',
          material: upgMaterial,
          coatings: upgCoatings,
          priceRange: addPriceRanges(upgBase, upgCoatingPrice),
          reason:
            `High sphere power (${maxSph.toFixed(2)}D) benefits from ${upgMaterial} material for thinner, lighter lenses in a progressive design.`,
          priority: 'UPGRADE',
        });
      }

      // --- OPTIONAL: Bifocal as budget alternative ---
      const bifMaterial = isChild ? 'Polycarbonate' : 'CR-39';
      const bifBase = bifMaterial === 'Polycarbonate'
        ? PRICE.POLY_BIFOCAL
        : PRICE.CR39_BIFOCAL;
      const bifCoatings = pickCoatings(false, false, false);
      const bifCoatingPrice = coatingsPriceRange(bifCoatings);

      suggestions.push({
        id: nextId(),
        lensType: 'Bifocal (Kryptok / D-Segment)',
        material: bifMaterial,
        coatings: bifCoatings,
        priceRange: addPriceRanges(bifBase, bifCoatingPrice),
        reason:
          'Budget-friendly alternative to progressives. Has a visible line but provides clear near and distance zones.',
        priority: 'OPTIONAL',
      });
    } else {
      // Younger patient with ADD — bifocal primary, progressive upgrade
      const bifMaterial = isChild ? 'Polycarbonate' : 'CR-39';
      const bifBase = bifMaterial === 'Polycarbonate'
        ? PRICE.POLY_BIFOCAL
        : PRICE.CR39_BIFOCAL;
      const bifCoatings = pickCoatings(isScreenUser, isOutdoor, false);
      const bifCoatingPrice = coatingsPriceRange(bifCoatings);

      suggestions.push({
        id: nextId(),
        lensType: 'Bifocal (Kryptok / D-Segment)',
        material: bifMaterial,
        coatings: bifCoatings,
        priceRange: addPriceRanges(bifBase, bifCoatingPrice),
        reason:
          'ADD power detected. Bifocal provides distinct near and distance zones suitable for younger patients.',
        priority: 'PRIMARY',
      });

      // Progressive upgrade
      const progMaterial = pickMaterial(maxSph, isChild);
      const progBase = progressiveBasePrice(progMaterial);
      const progCoatings = pickCoatings(isScreenUser, isOutdoor, false);
      const progCoatingPrice = coatingsPriceRange(progCoatings);

      suggestions.push({
        id: nextId(),
        lensType: 'Progressive',
        material: progMaterial,
        coatings: progCoatings,
        priceRange: addPriceRanges(progBase, progCoatingPrice),
        reason:
          'Premium upgrade offering seamless vision at all distances without a visible line.',
        priority: 'UPGRADE',
      });
    }
  } else {
    // --- SINGLE VISION ---
    const material = pickMaterial(maxSph, isChild);
    const basePrice = singleVisionBasePrice(material);
    const coatings = pickCoatings(isScreenUser, isOutdoor, false);
    const coatingPrice = coatingsPriceRange(coatings);

    suggestions.push({
      id: nextId(),
      lensType: 'Single Vision',
      material,
      coatings,
      priceRange: addPriceRanges(basePrice, coatingPrice),
      reason: singleVisionReason(material, maxSph, isChild),
      priority: 'PRIMARY',
    });

    // If power is moderate, offer Hi-Index upgrade
    if (maxSph > 4 && material !== 'Hi-Index 1.74') {
      const upgMaterial = maxSph > 6 ? 'Hi-Index 1.74' : 'Hi-Index 1.67';
      const upgBase = singleVisionBasePrice(upgMaterial);
      const upgCoatings = pickCoatings(isScreenUser, isOutdoor, false);
      const upgCoatingPrice = coatingsPriceRange(upgCoatings);

      suggestions.push({
        id: nextId(),
        lensType: 'Single Vision',
        material: upgMaterial,
        coatings: upgCoatings,
        priceRange: addPriceRanges(upgBase, upgCoatingPrice),
        reason:
          `Sphere power of ${maxSph.toFixed(2)}D will result in thick edges/centre. ${upgMaterial} reduces thickness by up to 40% for better aesthetics and comfort.`,
        priority: 'UPGRADE',
      });
    }

    // If child and not already polycarbonate, suggest it
    if (isChild && material !== 'Polycarbonate') {
      const polyBase = PRICE.POLY_SINGLE;
      const polyCoatings = pickCoatings(isScreenUser, false, true);
      const polyCoatingPrice = coatingsPriceRange(polyCoatings);
      suggestions.push({
        id: nextId(),
        lensType: 'Single Vision',
        material: 'Polycarbonate',
        coatings: polyCoatings,
        priceRange: addPriceRanges(polyBase, polyCoatingPrice),
        reason:
          'Impact-resistant polycarbonate recommended for patients under 18 for safety during sports and daily activities.',
        priority: 'PRIMARY',
      });
    }
  }

  // --------------------------------------------------
  // 2. Aspheric lens recommendation for high cylinder
  // --------------------------------------------------
  if (maxCyl > 2) {
    const material = pickMaterial(maxSph, isChild);
    const basePrice = addPresent
      ? progressiveBasePrice(material)
      : singleVisionBasePrice(material);
    // Aspheric adds roughly 30-50% premium
    const asphericPrice = {
      min: Math.round(basePrice.min * 1.3),
      max: Math.round(basePrice.max * 1.5),
    };
    const coatings = pickCoatings(isScreenUser, isOutdoor, false);
    const coatingPrice = coatingsPriceRange(coatings);

    suggestions.push({
      id: nextId(),
      lensType: addPresent ? 'Progressive (Aspheric)' : 'Single Vision (Aspheric)',
      material,
      coatings,
      priceRange: addPriceRanges(asphericPrice, coatingPrice),
      reason:
        `Cylinder power of ${maxCyl.toFixed(2)}D is significant. Aspheric design reduces distortion and provides wider clear vision area.`,
      priority: 'UPGRADE',
    });
  }

  // --------------------------------------------------
  // 3. Photochromic / Transitions as upgrade
  // --------------------------------------------------
  if (isOutdoor) {
    const material = pickMaterial(maxSph, isChild);
    const basePrice = addPresent
      ? progressiveBasePrice(material)
      : singleVisionBasePrice(material);
    const coatings = ['Anti-Reflective', 'Photochromic', 'Hard Coat'];

    suggestions.push({
      id: nextId(),
      lensType: addPresent ? 'Progressive (Photochromic)' : 'Single Vision (Photochromic)',
      material,
      coatings,
      priceRange: addPriceRanges(basePrice, PRICE.COATING_AR, PRICE.COATING_PHOTOCHROMIC, PRICE.COATING_HARD_COAT),
      reason:
        'Outdoor / driving lifestyle detected. Photochromic lenses darken in sunlight, eliminating the need for separate sunglasses.',
      priority: lifestyle === 'DRIVER' ? 'PRIMARY' : 'UPGRADE',
    });
  } else {
    // Offer photochromic as optional for everyone else
    const material = pickMaterial(maxSph, isChild);
    const basePrice = addPresent
      ? progressiveBasePrice(material)
      : singleVisionBasePrice(material);

    suggestions.push({
      id: nextId(),
      lensType: addPresent ? 'Progressive (Photochromic)' : 'Single Vision (Photochromic)',
      material,
      coatings: ['Anti-Reflective', 'Photochromic', 'Hard Coat'],
      priceRange: addPriceRanges(basePrice, PRICE.COATING_AR, PRICE.COATING_PHOTOCHROMIC, PRICE.COATING_HARD_COAT),
      reason:
        'Photochromic lenses adapt to sunlight automatically. Convenient for patients who move between indoor and outdoor settings.',
      priority: 'OPTIONAL',
    });
  }

  // --------------------------------------------------
  // 4. Blue Cut coating option for screen users
  // --------------------------------------------------
  if (isScreenUser) {
    // We may already have blue cut in primary, but offer a dedicated blue-cut
    // option only if primary doesn't already have it. Check the first suggestion.
    const primaryHasBlueCut = suggestions.length > 0 && suggestions[0].coatings.includes('Blue Cut');
    if (!primaryHasBlueCut) {
      const material = pickMaterial(maxSph, isChild);
      const basePrice = addPresent
        ? progressiveBasePrice(material)
        : singleVisionBasePrice(material);

      suggestions.push({
        id: nextId(),
        lensType: addPresent ? 'Progressive' : 'Single Vision',
        material,
        coatings: ['Anti-Reflective', 'Blue Cut', 'Hard Coat'],
        priceRange: addPriceRanges(basePrice, PRICE.COATING_AR, PRICE.COATING_BLUE_CUT, PRICE.COATING_HARD_COAT),
        reason:
          'Blue Cut coating filters harmful blue light from screens. Recommended for students, office workers, and digital device users.',
        priority: 'UPGRADE',
      });
    }
  }

  // De-duplicate: remove suggestions with identical lensType + material + priority
  const seen = new Set<string>();
  const deduped = suggestions.filter((s) => {
    const key = `${s.lensType}|${s.material}|${s.priority}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // Sort: PRIMARY first, then UPGRADE, then OPTIONAL
  const priorityOrder: Record<string, number> = {
    PRIMARY: 0,
    UPGRADE: 1,
    OPTIONAL: 2,
  };
  deduped.sort((a, b) => priorityOrder[a.priority] - priorityOrder[b.priority]);

  return deduped;
}

// ---------------------------------------------------------------------------
// Material selection
// ---------------------------------------------------------------------------

function pickMaterial(maxSph: number, isChild: boolean): string {
  if (isChild) return 'Polycarbonate';
  if (maxSph > 6) return 'Hi-Index 1.74';
  if (maxSph > 4) return 'Hi-Index 1.67';
  if (maxSph > 2) return 'Polycarbonate';
  return 'CR-39';
}

// ---------------------------------------------------------------------------
// Price lookups
// ---------------------------------------------------------------------------

function singleVisionBasePrice(material: string): { min: number; max: number } {
  switch (material) {
    case 'Hi-Index 1.74': return PRICE.HI_INDEX_174_SINGLE;
    case 'Hi-Index 1.67': return PRICE.HI_INDEX_167_SINGLE;
    case 'Trivex':        return PRICE.TRIVEX_SINGLE;
    case 'Polycarbonate': return PRICE.POLY_SINGLE;
    default:              return PRICE.CR39_SINGLE;
  }
}

function progressiveBasePrice(material: string): { min: number; max: number } {
  switch (material) {
    case 'Hi-Index 1.74': return PRICE.HI_INDEX_174_PROG;
    case 'Hi-Index 1.67': return PRICE.HI_INDEX_167_PROG;
    case 'Polycarbonate': return PRICE.POLY_PROGRESSIVE;
    default:              return PRICE.CR39_PROGRESSIVE;
  }
}

// ---------------------------------------------------------------------------
// Coating selection
// ---------------------------------------------------------------------------

function pickCoatings(
  isScreenUser: boolean,
  isOutdoor: boolean,
  forceHardCoat: boolean,
): string[] {
  const coatings: string[] = ['Anti-Reflective']; // Always recommended

  if (isScreenUser) {
    coatings.push('Blue Cut');
  }
  if (isOutdoor) {
    coatings.push('Photochromic');
  }
  if (forceHardCoat || isScreenUser || isOutdoor) {
    coatings.push('Hard Coat');
  }
  return coatings;
}

function coatingsPriceRange(coatings: string[]): { min: number; max: number } {
  let min = 0;
  let max = 0;
  for (const c of coatings) {
    switch (c) {
      case 'Anti-Reflective':
        min += PRICE.COATING_AR.min;
        max += PRICE.COATING_AR.max;
        break;
      case 'Blue Cut':
        min += PRICE.COATING_BLUE_CUT.min;
        max += PRICE.COATING_BLUE_CUT.max;
        break;
      case 'Photochromic':
        min += PRICE.COATING_PHOTOCHROMIC.min;
        max += PRICE.COATING_PHOTOCHROMIC.max;
        break;
      case 'Hard Coat':
        min += PRICE.COATING_HARD_COAT.min;
        max += PRICE.COATING_HARD_COAT.max;
        break;
    }
  }
  return { min, max };
}

// ---------------------------------------------------------------------------
// Reason text helpers
// ---------------------------------------------------------------------------

function singleVisionReason(material: string, maxSph: number, isChild: boolean): string {
  if (isChild) {
    return 'Polycarbonate single vision lens recommended for patients under 18. Impact-resistant and lightweight for active lifestyles.';
  }
  if (material === 'Hi-Index 1.74') {
    return `Sphere power of ${maxSph.toFixed(2)}D is very high. Hi-Index 1.74 provides the thinnest possible lens for comfort and aesthetics.`;
  }
  if (material === 'Hi-Index 1.67') {
    return `Sphere power of ${maxSph.toFixed(2)}D benefits from Hi-Index 1.67 for noticeably thinner lenses compared to standard CR-39.`;
  }
  if (material === 'Polycarbonate') {
    return 'Moderate prescription suits polycarbonate lenses. Lighter and more impact-resistant than standard CR-39.';
  }
  return 'Standard CR-39 single vision lens. Excellent optical clarity at an affordable price point for everyday use.';
}
