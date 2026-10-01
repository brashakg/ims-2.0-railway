// Shared by LoyaltyLayout and its sections (Wave 6 B12 split of the old
// LoyaltyProgram page). Tier metadata, the two formatters and the outlet
// context the layout hands each section. Moved byte-identical from
// LoyaltyProgram.tsx.

import { useOutletContext } from 'react-router-dom';
import type { LoyaltyProgramStats, LoyaltyRewardCreate, LoyaltySettings } from '../../../services/api/loyalty';

// Visual metadata only (badge / colour / benefits). The real numeric
// thresholds + point multipliers come from the loyalty engine
// (loyaltyApi.getSettings -> tier_thresholds / tier_multipliers), so the UI
// never disagrees with what actually earns/tiers a customer. 4 tiers — the
// engine has no "Diamond".
interface LoyaltyTierMeta {
  name: string;
  key: string; // engine tier key (UPPER) for thresholds/multipliers lookup
  color: string;
  bgColor: string;
  badge: string;
  benefits: string[];
}

const LOYALTY_TIERS: LoyaltyTierMeta[] = [
  {
    name: 'Bronze',
    key: 'BRONZE',
    color: 'text-amber-600',
    bgColor: 'bg-amber-50 border-amber-200',
    badge: '🥉',
    benefits: ['Base points per purchase', 'Monthly newsletter', 'Email promotions'],
  },
  {
    name: 'Silver',
    key: 'SILVER',
    color: 'text-gray-700',
    bgColor: 'bg-gray-50 border-gray-200',
    badge: '🥈',
    benefits: ['Birthday offer', 'Priority support', 'Free shipping'],
  },
  {
    name: 'Gold',
    key: 'GOLD',
    color: 'text-yellow-600',
    bgColor: 'bg-yellow-50 border-yellow-200',
    badge: '🥇',
    benefits: ['Exclusive sales', 'Loyalty discount', 'VIP support'],
  },
  {
    name: 'Platinum',
    key: 'PLATINUM',
    color: 'text-blue-600',
    bgColor: 'bg-blue-50 border-blue-200',
    badge: '💎',
    benefits: ['Top loyalty discount', 'Personal account manager', 'Event invites'],
  },
];

const fmtCompact = (n: number) =>
  new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 }).format(n || 0);
const tierCount = (stats: LoyaltyProgramStats | null, tierName: string) =>
  stats?.by_tier?.[tierName.toUpperCase()] ?? 0;

// What the layout loads once (program stats + engine settings) and every
// section reads through <Outlet context>.
interface LoyaltyOutletContext {
  stats: LoyaltyProgramStats | null;
  settings: LoyaltySettings | null;
  // The Rewards 'New reward' draft lives in the layout so a half-typed form
  // survives a visit to Overview / Tiers (as it did between the old tabs).
  rewardDraft: RewardDraft;
}

interface RewardDraft {
  showAddReward: boolean;
  setShowAddReward: React.Dispatch<React.SetStateAction<boolean>>;
  newReward: LoyaltyRewardCreate;
  setNewReward: React.Dispatch<React.SetStateAction<LoyaltyRewardCreate>>;
}

const BLANK_REWARD: LoyaltyRewardCreate = {
  name: '',
  type: 'DISCOUNT',
  point_cost: 100,
  description: '',
};

const useLoyaltyContext = () => useOutletContext<LoyaltyOutletContext>();

export { BLANK_REWARD, LOYALTY_TIERS, fmtCompact, tierCount, useLoyaltyContext };
export type { LoyaltyOutletContext, RewardDraft };
