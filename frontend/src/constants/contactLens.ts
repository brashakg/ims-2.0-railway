// ============================================================================
// IMS 2.0 - Contact Lens Tracking Constants
// ============================================================================
// Track contact lens purchases, expiry, and replacement schedules

export type LensType = 'DAILY' | 'WEEKLY' | 'BIWEEKLY' | 'MONTHLY' | 'QUARTERLY' | 'YEARLY';
export type LensMaterial = 'SOFT' | 'RGP' | 'SCLERAL' | 'HYBRID';
export type LensUsage = 'DAILY_WEAR' | 'EXTENDED_WEAR' | 'CONTINUOUS_WEAR';

export interface ContactLensConfig {
  type: LensType;
  name: string;
  replacementDays: number;
  reminderDaysBefore: number;
  description: string;
  icon: string;
}

// Contact lens replacement schedules
export const LENS_TYPES: Record<LensType, ContactLensConfig> = {
  DAILY: {
    type: 'DAILY',
    name: 'Daily Disposable',
    replacementDays: 1,
    reminderDaysBefore: 7, // Remind 1 week before pack finishes
    description: 'Replace every day. Typically sold in packs of 30 or 90.',
    icon: '📅',
  },
  WEEKLY: {
    type: 'WEEKLY',
    name: 'Weekly Disposable',
    replacementDays: 7,
    reminderDaysBefore: 2,
    description: 'Replace every week (7 days)',
    icon: '📆',
  },
  BIWEEKLY: {
    type: 'BIWEEKLY',
    name: 'Bi-weekly Disposable',
    replacementDays: 14,
    reminderDaysBefore: 3,
    description: 'Replace every 2 weeks (14 days)',
    icon: '🗓️',
  },
  MONTHLY: {
    type: 'MONTHLY',
    name: 'Monthly Disposable',
    replacementDays: 30,
    reminderDaysBefore: 5,
    description: 'Replace every month (30 days)',
    icon: '📊',
  },
  QUARTERLY: {
    type: 'QUARTERLY',
    name: 'Quarterly Replacement',
    replacementDays: 90,
    reminderDaysBefore: 7,
    description: 'Replace every 3 months (90 days)',
    icon: '📈',
  },
  YEARLY: {
    type: 'YEARLY',
    name: 'Yearly Replacement',
    replacementDays: 365,
    reminderDaysBefore: 14,
    description: 'Replace every year (365 days)',
    icon: '🎯',
  },
};

// Contact lens purchase record
export interface ContactLensPurchase {
  id: string;
  customerId: string;
  customerName: string;
  customerPhone: string;
  orderId?: string;

  // Lens details
  brandName: string;
  productName: string;
  lensType: LensType;
  lensMaterial?: LensMaterial;
  lensUsage?: LensUsage;

  // Prescription
  rightEye?: {
    power: number;
    baseCurve?: number;
    diameter?: number;
  };
  leftEye?: {
    power: number;
    baseCurve?: number;
    diameter?: number;
  };

  // Purchase info
  packSize: number; // Number of lenses per pack (e.g., 30, 90)
  packsPurchased: number;
  totalLenses: number; // packSize * packsPurchased
  purchaseDate: string;
  unitPrice: number;
  totalAmount: number;

  // Expiry tracking
  manufactureDate?: string;
  shelfExpiryDate?: string; // Expiry date on packaging
  firstOpenDate?: string; // Date customer opened the pack
  replacementSchedule: {
    startDate: string;
    endDate: string; // Based on lens type and quantity
    daysRemaining: number;
  };

  // Reminders
  reminderSent: boolean;
  reminderDate?: string;
  reminderPreference: 'SMS' | 'WHATSAPP' | 'BOTH' | 'NONE';

  // Status
  status: 'ACTIVE' | 'EXPIRING_SOON' | 'EXPIRED' | 'REPLACED';
  notes?: string;
  storeId: string;
  createdAt: string;
  updatedAt: string;
}

// Calculate replacement end date
export function calculateReplacementEndDate(
  startDate: string,
  lensType: LensType,
  totalLenses: number
): string {
  const start = new Date(startDate);
  const config = LENS_TYPES[lensType];

  // For daily lenses, end date is based on total count
  if (lensType === 'DAILY') {
    start.setDate(start.getDate() + totalLenses);
  } else {
    // For reusable lenses, multiply replacement days by number of lens pairs
    const totalDays = config.replacementDays * (totalLenses / 2); // Assuming pairs
    start.setDate(start.getDate() + totalDays);
  }

  return start.toISOString().split('T')[0];
}

// Calculate days remaining until replacement
export function calculateDaysRemaining(endDate: string): number {
  const end = new Date(endDate);
  const now = new Date();
  const diffTime = end.getTime() - now.getTime();
  const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
  return Math.max(0, diffDays);
}

// Check if lens needs replacement
export function needsReplacement(daysRemaining: number): boolean {
  return daysRemaining <= 0;
}

// Check if reminder should be sent
export function shouldSendReminder(
  daysRemaining: number,
  lensType: LensType,
  reminderSent: boolean
): boolean {
  if (reminderSent) return false;
  const config = LENS_TYPES[lensType];
  return daysRemaining <= config.reminderDaysBefore && daysRemaining > 0;
}

// Get lens status
export function getLensStatus(daysRemaining: number): ContactLensPurchase['status'] {
  if (daysRemaining <= 0) return 'EXPIRED';
  if (daysRemaining <= 7) return 'EXPIRING_SOON';
  return 'ACTIVE';
}

// Format power display
export function formatPower(power: number): string {
  if (power >= 0) return `+${power.toFixed(2)}`;
  return power.toFixed(2);
}

// Popular contact lens brands in India
export const POPULAR_LENS_BRANDS = [
  'Bausch & Lomb',
  'Johnson & Johnson',
  'Alcon',
  'CooperVision',
  'Acuvue',
  'Freshlook',
  'Air Optix',
  'Biofinity',
  'Proclear',
  'Soflens',
];

// Common pack sizes
export const PACK_SIZES = [
  { value: 1, label: '1 lens' },
  { value: 6, label: '6 lenses (3 months supply)' },
  { value: 10, label: '10 lenses' },
  { value: 30, label: '30 lenses (1 month supply)' },
  { value: 90, label: '90 lenses (3 months supply)' },
  { value: 180, label: '180 lenses (6 months supply)' },
];

// Customer lens subscription
export interface LensSubscription {
  id: string;
  customerId: string;
  customerName: string;
  lensType: LensType;
  brandName: string;
  productName: string;
  rightEyePower: number;
  leftEyePower: number;
  autoReminder: boolean;
  autoOrder: boolean;
  deliveryPreference: 'PICKUP' | 'HOME_DELIVERY';
  isActive: boolean;
  nextDeliveryDate?: string;
  createdAt: string;
}

// Lens care products
export const LENS_CARE_PRODUCTS = [
  {
    name: 'Multi-purpose Solution',
    description: 'For cleaning, rinsing, and storing lenses',
    recommendedFor: ['SOFT'] as LensMaterial[],
  },
  {
    name: 'Hydrogen Peroxide Solution',
    description: 'Deep cleaning solution',
    recommendedFor: ['SOFT', 'RGP'] as LensMaterial[],
  },
  {
    name: 'RGP Solution',
    description: 'Specialized solution for rigid lenses',
    recommendedFor: ['RGP'] as LensMaterial[],
  },
  {
    name: 'Rewetting Drops',
    description: 'For dry eyes and lens lubrication',
    recommendedFor: ['SOFT', 'RGP', 'SCLERAL'] as LensMaterial[],
  },
  {
    name: 'Lens Case',
    description: 'Storage case for contact lenses',
    recommendedFor: ['SOFT', 'RGP', 'SCLERAL'] as LensMaterial[],
  },
];

// Compliance reminder
export const LENS_CARE_REMINDERS = [
  'Never sleep with daily wear lenses',
  'Replace your lens case every 3 months',
  'Never use tap water with contact lenses',
  'Wash hands before handling lenses',
  'Follow the replacement schedule',
  'Remove lenses if eyes are red or irritated',
  'Schedule regular eye checkups',
];

export default {
  LENS_TYPES,
  POPULAR_LENS_BRANDS,
  PACK_SIZES,
  LENS_CARE_PRODUCTS,
  LENS_CARE_REMINDERS,
  calculateReplacementEndDate,
  calculateDaysRemaining,
  needsReplacement,
  shouldSendReminder,
  getLensStatus,
  formatPower,
};
