// ============================================================================
// IMS 2.0 - Loyalty Program Constants
// ============================================================================
// Point-based loyalty program for customer retention

export type LoyaltyTier = 'SILVER' | 'GOLD' | 'PLATINUM' | 'DIAMOND';

export interface LoyaltyTierConfig {
  tier: LoyaltyTier;
  name: string;
  minPoints: number;
  color: string;
  benefits: string[];
  pointsMultiplier: number; // Multiplier for earning points
  redeemRate: number; // Points needed for ₹1 discount
  icon: string;
}

// Loyalty tier configurations (inspired by Titan Encircle)
export const LOYALTY_TIERS: Record<LoyaltyTier, LoyaltyTierConfig> = {
  SILVER: {
    tier: 'SILVER',
    name: 'Silver',
    minPoints: 0,
    color: '#C0C0C0',
    benefits: [
      'Earn 1 point for every ₹10 spent',
      'Birthday bonus: 100 points',
      'SMS/WhatsApp updates on orders',
      'Exclusive member-only offers',
    ],
    pointsMultiplier: 1.0,
    redeemRate: 10, // 10 points = ₹1
    icon: '🥈',
  },
  GOLD: {
    tier: 'GOLD',
    name: 'Gold',
    minPoints: 5000,
    color: '#FFD700',
    benefits: [
      'Earn 1.2 points for every ₹10 spent',
      'Birthday bonus: 250 points',
      'Priority customer support',
      'Early access to new collections',
      'Free basic eye checkup once a year',
      '5% bonus on point redemption',
    ],
    pointsMultiplier: 1.2,
    redeemRate: 9, // 9 points = ₹1 (better rate)
    icon: '🥇',
  },
  PLATINUM: {
    tier: 'PLATINUM',
    name: 'Platinum',
    minPoints: 15000,
    color: '#E5E4E2',
    benefits: [
      'Earn 1.5 points for every ₹10 spent',
      'Birthday bonus: 500 points',
      'Dedicated relationship manager',
      'Exclusive VIP events and launches',
      'Free eye checkup twice a year',
      '10% bonus on point redemption',
      'Free home delivery',
      'Complimentary frame adjustments',
    ],
    pointsMultiplier: 1.5,
    redeemRate: 8, // 8 points = ₹1 (best rate)
    icon: '⭐',
  },
  DIAMOND: {
    tier: 'DIAMOND',
    name: 'Diamond',
    minPoints: 30000,
    color: '#B9F2FF',
    benefits: [
      'Earn 2 points for every ₹10 spent',
      'Birthday bonus: 1000 points',
      'Personalized shopping experience',
      'Access to exclusive limited editions',
      'Unlimited free eye checkups',
      '15% bonus on point redemption',
      'Priority workshop service',
      'Free lens upgrade on orders',
      'Complimentary eyewear cleaning kit',
    ],
    pointsMultiplier: 2.0,
    redeemRate: 7, // 7 points = ₹1 (premium rate)
    icon: '💎',
  },
};

// Point earning rules
export interface PointEarningRule {
  id: string;
  name: string;
  category: 'PURCHASE' | 'REFERRAL' | 'REVIEW' | 'BIRTHDAY' | 'ANNIVERSARY' | 'MILESTONE' | 'BONUS';
  description: string;
  points: number;
  isActive: boolean;
}

export const POINT_EARNING_RULES: Record<string, PointEarningRule> = {
  // Purchase-based
  PURCHASE_BASE: {
    id: 'PURCHASE_BASE',
    name: 'Purchase Points',
    category: 'PURCHASE',
    description: 'Earn points on every purchase (multiplied by tier)',
    points: 1, // Base: 1 point per ₹10
    isActive: true,
  },
  FIRST_PURCHASE_BONUS: {
    id: 'FIRST_PURCHASE_BONUS',
    name: 'First Purchase Bonus',
    category: 'BONUS',
    description: 'Welcome bonus on first purchase',
    points: 200,
    isActive: true,
  },

  // Referral program
  REFERRAL_INVITER: {
    id: 'REFERRAL_INVITER',
    name: 'Referral Reward (You)',
    category: 'REFERRAL',
    description: 'Earn when your referral makes first purchase',
    points: 500,
    isActive: true,
  },
  REFERRAL_INVITEE: {
    id: 'REFERRAL_INVITEE',
    name: 'Referral Reward (Friend)',
    category: 'REFERRAL',
    description: 'Your friend earns on their first purchase',
    points: 300,
    isActive: true,
  },

  // Review & feedback
  PRODUCT_REVIEW: {
    id: 'PRODUCT_REVIEW',
    name: 'Product Review',
    category: 'REVIEW',
    description: 'Write a product review with photo',
    points: 50,
    isActive: true,
  },
  GOOGLE_REVIEW: {
    id: 'GOOGLE_REVIEW',
    name: 'Google Review',
    category: 'REVIEW',
    description: 'Leave a Google review for our store',
    points: 100,
    isActive: true,
  },

  // Special occasions
  BIRTHDAY_BONUS: {
    id: 'BIRTHDAY_BONUS',
    name: 'Birthday Bonus',
    category: 'BIRTHDAY',
    description: 'Birthday gift points (tier-based)',
    points: 100, // Base amount, multiplied by tier
    isActive: true,
  },
  ANNIVERSARY_BONUS: {
    id: 'ANNIVERSARY_BONUS',
    name: 'Anniversary Bonus',
    category: 'ANNIVERSARY',
    description: 'Anniversary gift points',
    points: 150,
    isActive: true,
  },

  // Milestones
  TIER_UPGRADE_GOLD: {
    id: 'TIER_UPGRADE_GOLD',
    name: 'Gold Tier Achievement',
    category: 'MILESTONE',
    description: 'Bonus for reaching Gold tier',
    points: 500,
    isActive: true,
  },
  TIER_UPGRADE_PLATINUM: {
    id: 'TIER_UPGRADE_PLATINUM',
    name: 'Platinum Tier Achievement',
    category: 'MILESTONE',
    description: 'Bonus for reaching Platinum tier',
    points: 1000,
    isActive: true,
  },
  TIER_UPGRADE_DIAMOND: {
    id: 'TIER_UPGRADE_DIAMOND',
    name: 'Diamond Tier Achievement',
    category: 'MILESTONE',
    description: 'Bonus for reaching Diamond tier',
    points: 2000,
    isActive: true,
  },

  // Social media
  SOCIAL_SHARE: {
    id: 'SOCIAL_SHARE',
    name: 'Social Media Share',
    category: 'BONUS',
    description: 'Share your purchase on social media',
    points: 25,
    isActive: true,
  },
  FOLLOW_SOCIAL: {
    id: 'FOLLOW_SOCIAL',
    name: 'Follow Us',
    category: 'BONUS',
    description: 'Follow us on Instagram/Facebook',
    points: 50,
    isActive: true,
  },
};

// Calculate tier based on total points
export function calculateTier(totalPoints: number): LoyaltyTier {
  if (totalPoints >= LOYALTY_TIERS.DIAMOND.minPoints) return 'DIAMOND';
  if (totalPoints >= LOYALTY_TIERS.PLATINUM.minPoints) return 'PLATINUM';
  if (totalPoints >= LOYALTY_TIERS.GOLD.minPoints) return 'GOLD';
  return 'SILVER';
}

// Calculate points earned on purchase
export function calculatePurchasePoints(amount: number, tier: LoyaltyTier): number {
  const basePoints = Math.floor(amount / 10); // 1 point per ₹10
  const multiplier = LOYALTY_TIERS[tier].pointsMultiplier;
  return Math.floor(basePoints * multiplier);
}

// Calculate discount amount from points
export function calculatePointsDiscount(points: number, tier: LoyaltyTier): number {
  const redeemRate = LOYALTY_TIERS[tier].redeemRate;
  return Math.floor(points / redeemRate);
}

// Calculate points required for discount
export function calculatePointsRequired(discountAmount: number, tier: LoyaltyTier): number {
  const redeemRate = LOYALTY_TIERS[tier].redeemRate;
  return Math.ceil(discountAmount * redeemRate);
}

// Get next tier and points needed
export function getNextTier(currentTier: LoyaltyTier, currentPoints: number): {
  nextTier: LoyaltyTier | null;
  pointsNeeded: number;
} | null {
  const tiers: LoyaltyTier[] = ['SILVER', 'GOLD', 'PLATINUM', 'DIAMOND'];
  const currentIndex = tiers.indexOf(currentTier);

  if (currentIndex === tiers.length - 1) {
    return null; // Already at highest tier
  }

  const nextTier = tiers[currentIndex + 1];
  const pointsNeeded = LOYALTY_TIERS[nextTier].minPoints - currentPoints;

  return {
    nextTier,
    pointsNeeded: Math.max(0, pointsNeeded),
  };
}

// Loyalty transaction types
export interface LoyaltyTransaction {
  id: string;
  customerId: string;
  type: 'EARNED' | 'REDEEMED' | 'EXPIRED' | 'ADJUSTED';
  points: number;
  reason: string;
  ruleId?: string;
  orderId?: string;
  referenceNumber?: string;
  balanceBefore: number;
  balanceAfter: number;
  expiryDate?: string; // Points can expire after 1-2 years
  createdAt: string;
  createdBy?: string;
}

// Customer loyalty profile
export interface CustomerLoyaltyProfile {
  customerId: string;
  totalPointsEarned: number;
  totalPointsRedeemed: number;
  currentBalance: number;
  tier: LoyaltyTier;
  tierStartDate: string;
  nextTierPoints?: number;
  lifetimeValue: number; // Total purchase value
  memberSince: string;
  lastActivityDate: string;
  referralCode: string;
  referralCount: number;
  isActive: boolean;
}

// Validate points expiry (typically 1 year in India)
export function shouldPointsExpire(earnedDate: string, expiryMonths: number = 12): boolean {
  const earned = new Date(earnedDate);
  const now = new Date();
  const monthsDiff = (now.getFullYear() - earned.getFullYear()) * 12 + (now.getMonth() - earned.getMonth());
  return monthsDiff >= expiryMonths;
}

// Generate unique referral code
export function generateReferralCode(customerName: string, customerId: string): string {
  const namePrefix = customerName.toUpperCase().substring(0, 3).replace(/[^A-Z]/g, '');
  const idSuffix = customerId.substring(customerId.length - 4);
  return `${namePrefix}${idSuffix}`;
}

export default {
  LOYALTY_TIERS,
  POINT_EARNING_RULES,
  calculateTier,
  calculatePurchasePoints,
  calculatePointsDiscount,
  calculatePointsRequired,
  getNextTier,
  shouldPointsExpire,
  generateReferralCode,
};
