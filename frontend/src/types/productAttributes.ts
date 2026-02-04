// ============================================================================
// IMS 2.0 - Product Category Attributes Type Definitions
// ============================================================================
// Based on the category attribute matrix from business requirements
// Each category has specific attributes that must be captured

export type ProductCategoryCode =
  | 'SG'    // Sunglass
  | 'CL'    // Contact Lens
  | 'FR'    // Frame
  | 'ACC'   // Accessories
  | 'LS'    // Lens (Optical)
  | 'RG'    // Reading Glasses
  | 'WT'    // Wrist Watches
  | 'CK'    // Clocks
  | 'HA'    // Hearing Aids
  | 'SMTSG' // Smart Sunglasses
  | 'SMTFR' // Smart Glasses (Frames)
  | 'SMTWT' // Smart Watches
  | 'SVC';  // Services

// ============================================================================
// Base Product Attributes (Common to all)
// ============================================================================

export interface BaseProductAttributes {
  brandName: string;
  subbrand: string;
  modelNo: string;
}

// ============================================================================
// Category-Specific Attributes
// ============================================================================

// SG - Sunglass
export interface SunglassAttributes extends BaseProductAttributes {
  colourCode: string;
  lensSize: string;      // e.g., "52"
  bridgeWidth: string;   // e.g., "18"
  templeLength: string;  // e.g., "140"
}

// CL - Contact Lens
export interface ContactLensAttributes extends BaseProductAttributes {
  colourName?: string;   // For colored lenses
  power: string;         // e.g., "-2.00", "+1.50", "PLANO"
  pack: number;          // Number of lenses in pack (1, 2, 6, 30, 90)
  expiryDate?: string;   // ISO date string
  baseCurve?: string;    // e.g., "8.5"
  diameter?: string;     // e.g., "14.2"
  waterContent?: string; // e.g., "38%"
  lensType?: 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'YEARLY';
}

// FR - Frame
export interface FrameAttributes extends BaseProductAttributes {
  colourCode: string;
  lensSize: string;
  bridgeWidth: string;
  templeLength: string;
  frameType?: 'FULL_RIM' | 'HALF_RIM' | 'RIMLESS';
  frameMaterial?: 'METAL' | 'PLASTIC' | 'TITANIUM' | 'ACETATE' | 'MIXED';
  frameShape?: 'ROUND' | 'SQUARE' | 'RECTANGLE' | 'OVAL' | 'CAT_EYE' | 'AVIATOR' | 'WAYFARER';
  gender?: 'MALE' | 'FEMALE' | 'UNISEX' | 'KIDS';
}

// ACC - Accessories
export interface AccessoriesAttributes extends BaseProductAttributes {
  size?: string;
  pack?: number;
  expiryDate?: string;
  addOn1?: string;
  accessoryType?: 'CASE' | 'CLEANING_KIT' | 'SPRAY' | 'CLOTH' | 'NOSE_PAD' | 'SCREW_KIT' | 'CHAIN' | 'STRAP' | 'OTHER';
}

// LS - Lens (Optical)
export interface OpticalLensAttributes extends BaseProductAttributes {
  index: string;         // e.g., "1.56", "1.60", "1.67", "1.74"
  coating: string;       // e.g., "AR", "Blue Cut", "Photochromic"
  addOn1?: string;       // e.g., "UV Protection"
  addOn2?: string;       // e.g., "Scratch Resistant"
  addOn3?: string;       // e.g., "Hydrophobic"
  lensCategory: 'SINGLE_VISION' | 'BIFOCAL' | 'PROGRESSIVE' | 'OFFICE' | 'DRIVING';
  lensMaterial?: 'CR39' | 'POLYCARBONATE' | 'TRIVEX' | 'HIGH_INDEX';
}

// RG - Reading Glasses
export interface ReadingGlassesAttributes extends BaseProductAttributes {
  colourCode: string;
  lensSize: string;
  bridgeWidth: string;
  templeLength: string;
  power: string;         // e.g., "+1.00", "+1.50", "+2.00"
}

// WT - Wrist Watches
export interface WristWatchAttributes extends BaseProductAttributes {
  colourCode: string;
  dialColour: string;
  beltColour: string;
  dialSize: string;      // e.g., "38mm", "42mm"
  beltSize?: string;
  watchCategory: 'ANALOG' | 'DIGITAL' | 'CHRONOGRAPH' | 'DRESS' | 'SPORTS' | 'LUXURY';
  beltMaterial?: 'LEATHER' | 'METAL' | 'RUBBER' | 'FABRIC' | 'CERAMIC';
  waterResistance?: string; // e.g., "30M", "50M", "100M"
  movement?: 'QUARTZ' | 'AUTOMATIC' | 'MECHANICAL' | 'SOLAR';
}

// CK - Clocks
export interface ClockAttributes extends BaseProductAttributes {
  colourCode: string;
  dialColour: string;
  bodyColour: string;
  dialSize: string;
  batterySize: string;   // e.g., "AA", "AAA", "CR2032"
  clockCategory: 'WALL' | 'TABLE' | 'ALARM' | 'GRANDFATHER' | 'PENDULUM';
}

// HA - Hearing Aids
export interface HearingAidAttributes extends BaseProductAttributes {
  serialNo: string;
  machineCapacity: string; // e.g., "Mild", "Moderate", "Severe"
  machineType: 'BTE' | 'ITE' | 'ITC' | 'CIC' | 'RIC'; // Behind-the-ear, In-the-ear, etc.
  channels?: number;
  batteryType?: string;
  warrantyMonths?: number;
}

// SMTSG - Smart Sunglasses
export interface SmartSunglassAttributes extends BaseProductAttributes {
  colourCode: string;
  lensSize: string;
  bridgeWidth: string;
  templeLength: string;
  yearOfLaunch: string;
  connectivity?: 'BLUETOOTH' | 'WIFI' | 'BOTH';
  batteryLife?: string;
  features?: string[];   // e.g., ["Audio", "Camera", "AR Display"]
}

// SMTFR - Smart Glasses (Frames)
export interface SmartGlassesAttributes extends BaseProductAttributes {
  colourCode: string;
  lensSize: string;
  bridgeWidth: string;
  templeLength: string;
  yearOfLaunch: string;
  connectivity?: 'BLUETOOTH' | 'WIFI' | 'BOTH';
  batteryLife?: string;
  features?: string[];
}

// SMTWT - Smart Watches
export interface SmartWatchAttributes extends BaseProductAttributes {
  colourCode: string;
  bodyColour: string;
  beltColour: string;
  dialSize: string;
  beltSize?: string;
  yearOfLaunch: string;
  os?: 'WATCHOS' | 'WEAROS' | 'TIZEN' | 'FITBIT_OS' | 'PROPRIETARY';
  batteryLife?: string;
  waterResistance?: string;
  features?: string[];   // e.g., ["Heart Rate", "GPS", "NFC", "ECG"]
}

// SVC - Services
export interface ServiceAttributes {
  serviceName: string;
  serviceType: 'REPAIR' | 'FITTING' | 'CLEANING' | 'ADJUSTMENT' | 'CONSULTATION' | 'EYE_TEST';
  estimatedTime?: string;
  requiresAppointment?: boolean;
  description?: string;
}

// ============================================================================
// Union Type for All Product Attributes
// ============================================================================

export type ProductAttributes =
  | { category: 'SG'; attributes: SunglassAttributes }
  | { category: 'CL'; attributes: ContactLensAttributes }
  | { category: 'FR'; attributes: FrameAttributes }
  | { category: 'ACC'; attributes: AccessoriesAttributes }
  | { category: 'LS'; attributes: OpticalLensAttributes }
  | { category: 'RG'; attributes: ReadingGlassesAttributes }
  | { category: 'WT'; attributes: WristWatchAttributes }
  | { category: 'CK'; attributes: ClockAttributes }
  | { category: 'HA'; attributes: HearingAidAttributes }
  | { category: 'SMTSG'; attributes: SmartSunglassAttributes }
  | { category: 'SMTFR'; attributes: SmartGlassesAttributes }
  | { category: 'SMTWT'; attributes: SmartWatchAttributes }
  | { category: 'SVC'; attributes: ServiceAttributes };

// ============================================================================
// Category Configuration
// ============================================================================

export interface CategoryConfig {
  code: ProductCategoryCode;
  name: string;
  shortName: string;
  icon: string;
  color: string;
  bgColor: string;
  requiresPrescription: boolean;
  canAddLens: boolean;          // Can lens be added to this product (Frame, Sunglass)
  hasPower: boolean;            // Has power attribute (Contact Lens, Reading Glasses)
  hasExpiry: boolean;           // Has expiry date
  requiredFields: string[];
  optionalFields: string[];
}

export const CATEGORY_CONFIG: Record<ProductCategoryCode, CategoryConfig> = {
  FR: {
    code: 'FR',
    name: 'Frame',
    shortName: 'Spectacles',
    icon: 'Glasses',
    color: 'text-blue-600',
    bgColor: 'bg-blue-50',
    requiresPrescription: true,  // For lens fitting
    canAddLens: true,
    hasPower: false,
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength'],
    optionalFields: ['frameType', 'frameMaterial', 'frameShape', 'gender'],
  },
  SG: {
    code: 'SG',
    name: 'Sunglass',
    shortName: 'Sunglasses',
    icon: 'Sun',
    color: 'text-amber-600',
    bgColor: 'bg-amber-50',
    requiresPrescription: false, // Can have power but not mandatory
    canAddLens: true,            // For power sunglasses
    hasPower: false,
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength'],
    optionalFields: [],
  },
  CL: {
    code: 'CL',
    name: 'Contact Lens',
    shortName: 'Contact Lens',
    icon: 'Eye',
    color: 'text-green-600',
    bgColor: 'bg-green-50',
    requiresPrescription: true,
    canAddLens: false,
    hasPower: true,
    hasExpiry: true,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'power', 'pack'],
    optionalFields: ['colourName', 'expiryDate', 'baseCurve', 'diameter', 'waterContent', 'lensType'],
  },
  LS: {
    code: 'LS',
    name: 'Optical Lens',
    shortName: 'Lens',
    icon: 'Circle',
    color: 'text-cyan-600',
    bgColor: 'bg-cyan-50',
    requiresPrescription: true,
    canAddLens: false,
    hasPower: false,  // Power comes from prescription
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'index', 'coating', 'lensCategory'],
    optionalFields: ['addOn1', 'addOn2', 'addOn3', 'lensMaterial'],
  },
  RG: {
    code: 'RG',
    name: 'Reading Glasses',
    shortName: 'Readers',
    icon: 'BookOpen',
    color: 'text-indigo-600',
    bgColor: 'bg-indigo-50',
    requiresPrescription: false, // Pre-made power
    canAddLens: false,
    hasPower: true,
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength', 'power'],
    optionalFields: [],
  },
  WT: {
    code: 'WT',
    name: 'Wrist Watch',
    shortName: 'Watch',
    icon: 'Watch',
    color: 'text-purple-600',
    bgColor: 'bg-purple-50',
    requiresPrescription: false,
    canAddLens: false,
    hasPower: false,
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'dialColour', 'beltColour', 'dialSize', 'watchCategory'],
    optionalFields: ['beltSize', 'beltMaterial', 'waterResistance', 'movement'],
  },
  CK: {
    code: 'CK',
    name: 'Clock',
    shortName: 'Clock',
    icon: 'Clock',
    color: 'text-rose-600',
    bgColor: 'bg-rose-50',
    requiresPrescription: false,
    canAddLens: false,
    hasPower: false,
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'dialColour', 'bodyColour', 'dialSize', 'batterySize', 'clockCategory'],
    optionalFields: [],
  },
  ACC: {
    code: 'ACC',
    name: 'Accessories',
    shortName: 'Accessories',
    icon: 'Package',
    color: 'text-gray-600',
    bgColor: 'bg-gray-50',
    requiresPrescription: false,
    canAddLens: false,
    hasPower: false,
    hasExpiry: true,
    requiredFields: ['brandName', 'subbrand', 'modelNo'],
    optionalFields: ['size', 'pack', 'expiryDate', 'addOn1', 'accessoryType'],
  },
  HA: {
    code: 'HA',
    name: 'Hearing Aid',
    shortName: 'Hearing Aid',
    icon: 'Ear',
    color: 'text-pink-600',
    bgColor: 'bg-pink-50',
    requiresPrescription: false, // Requires audiometry instead
    canAddLens: false,
    hasPower: false,
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'serialNo', 'machineCapacity', 'machineType'],
    optionalFields: ['channels', 'batteryType', 'warrantyMonths'],
  },
  SMTSG: {
    code: 'SMTSG',
    name: 'Smart Sunglass',
    shortName: 'Smart Sunglasses',
    icon: 'Sparkles',
    color: 'text-violet-600',
    bgColor: 'bg-violet-50',
    requiresPrescription: false,
    canAddLens: true,
    hasPower: false,
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength', 'yearOfLaunch'],
    optionalFields: ['connectivity', 'batteryLife', 'features'],
  },
  SMTFR: {
    code: 'SMTFR',
    name: 'Smart Glasses',
    shortName: 'Smart Glasses',
    icon: 'Cpu',
    color: 'text-teal-600',
    bgColor: 'bg-teal-50',
    requiresPrescription: true,
    canAddLens: true,
    hasPower: false,
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength', 'yearOfLaunch'],
    optionalFields: ['connectivity', 'batteryLife', 'features'],
  },
  SMTWT: {
    code: 'SMTWT',
    name: 'Smart Watch',
    shortName: 'Smart Watch',
    icon: 'Smartphone',
    color: 'text-emerald-600',
    bgColor: 'bg-emerald-50',
    requiresPrescription: false,
    canAddLens: false,
    hasPower: false,
    hasExpiry: false,
    requiredFields: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'bodyColour', 'beltColour', 'dialSize', 'yearOfLaunch'],
    optionalFields: ['beltSize', 'os', 'batteryLife', 'waterResistance', 'features'],
  },
  SVC: {
    code: 'SVC',
    name: 'Service',
    shortName: 'Repair/Service',
    icon: 'Wrench',
    color: 'text-orange-600',
    bgColor: 'bg-orange-50',
    requiresPrescription: false,
    canAddLens: false,
    hasPower: false,
    hasExpiry: false,
    requiredFields: ['serviceName', 'serviceType'],
    optionalFields: ['estimatedTime', 'requiresAppointment', 'description'],
  },
};

// ============================================================================
// POS Category Mapping
// ============================================================================

export interface POSCategory {
  id: string;
  code: ProductCategoryCode;
  label: string;
  icon: string;
  color: string;
  bgColor: string;
  description: string;
}

export const POS_CATEGORIES: POSCategory[] = [
  // Optical - Primary Categories
  { id: 'spectacles', code: 'FR', label: 'Spectacles', icon: 'Glasses', color: 'text-blue-600', bgColor: 'bg-blue-50', description: 'Frames with prescription lenses' },
  { id: 'sunglasses', code: 'SG', label: 'Sunglasses', icon: 'Sun', color: 'text-amber-600', bgColor: 'bg-amber-50', description: 'Sunglasses with/without power' },
  { id: 'contact-lens', code: 'CL', label: 'Contact Lens', icon: 'Eye', color: 'text-green-600', bgColor: 'bg-green-50', description: 'Contact lenses' },
  { id: 'reading-glasses', code: 'RG', label: 'Reading Glasses', icon: 'BookOpen', color: 'text-indigo-600', bgColor: 'bg-indigo-50', description: 'Ready readers with pre-set power' },

  // Smart Products
  { id: 'smart-glasses', code: 'SMTFR', label: 'Smart Glasses', icon: 'Cpu', color: 'text-teal-600', bgColor: 'bg-teal-50', description: 'Smart frames with tech features' },
  { id: 'smart-sunglasses', code: 'SMTSG', label: 'Smart Sunglasses', icon: 'Sparkles', color: 'text-violet-600', bgColor: 'bg-violet-50', description: 'Smart sunglasses with audio/camera' },

  // Watches
  { id: 'watch', code: 'WT', label: 'Wrist Watch', icon: 'Watch', color: 'text-purple-600', bgColor: 'bg-purple-50', description: 'Analog & digital wrist watches' },
  { id: 'smart-watch', code: 'SMTWT', label: 'Smart Watch', icon: 'Smartphone', color: 'text-emerald-600', bgColor: 'bg-emerald-50', description: 'Smart watches & fitness bands' },

  // Clocks
  { id: 'clock', code: 'CK', label: 'Clocks', icon: 'Clock', color: 'text-rose-600', bgColor: 'bg-rose-50', description: 'Wall clocks & table clocks' },

  // Hearing
  { id: 'hearing-aid', code: 'HA', label: 'Hearing Aid', icon: 'Ear', color: 'text-pink-600', bgColor: 'bg-pink-50', description: 'Hearing aids & accessories' },

  // Accessories & Services
  { id: 'accessories', code: 'ACC', label: 'Accessories', icon: 'Package', color: 'text-gray-600', bgColor: 'bg-gray-50', description: 'Cases, cleaning kits, chains, etc.' },
  { id: 'repair', code: 'SVC', label: 'Repair/Service', icon: 'Wrench', color: 'text-orange-600', bgColor: 'bg-orange-50', description: 'Repairs, adjustments, cleaning' },
];

// Helper to get category config by POS category ID
export const getCategoryConfigByPOSId = (posId: string): CategoryConfig | undefined => {
  const posCategory = POS_CATEGORIES.find(c => c.id === posId);
  if (!posCategory) return undefined;
  return CATEGORY_CONFIG[posCategory.code];
};

// Helper to get POS category by code
export const getPOSCategoryByCode = (code: ProductCategoryCode): POSCategory | undefined => {
  return POS_CATEGORIES.find(c => c.code === code);
};
