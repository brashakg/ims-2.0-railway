// ============================================================================
// IMS 2.0 - RFM Customer Segmentation
// ============================================================================
// Recency, Frequency, Monetary analysis for customer classification
// Based on Salesforce/SAP CRM patterns adapted for Indian optical retail

export interface RFMScore {
  recency: number;   // 1-5 (5 = most recent)
  frequency: number; // 1-5 (5 = most frequent)
  monetary: number;  // 1-5 (5 = highest spend)
  total: number;     // sum of R+F+M
  segment: RFMSegment;
  label: string;
  color: string;
  bgColor: string;
  action: string;
}

export type RFMSegment =
  | 'CHAMPION'
  | 'LOYAL'
  | 'POTENTIAL_LOYALIST'
  | 'NEW_CUSTOMER'
  | 'PROMISING'
  | 'NEEDS_ATTENTION'
  | 'ABOUT_TO_SLEEP'
  | 'AT_RISK'
  | 'CANT_LOSE'
  | 'HIBERNATING'
  | 'LOST';

export interface CustomerRFMData {
  customerId: string;
  customerName: string;
  phone: string;
  lastPurchaseDate: string | null;
  totalOrders: number;
  totalSpend: number;
  daysSinceLastPurchase: number;
}

const SEGMENT_CONFIG: Record<RFMSegment, { label: string; color: string; bgColor: string; action: string }> = {
  CHAMPION: {
    label: 'Champion',
    color: 'text-green-700',
    bgColor: 'bg-green-100',
    action: 'Reward with exclusive offers. Request referrals. Invite to preview new collections.',
  },
  LOYAL: {
    label: 'Loyal Customer',
    color: 'text-blue-700',
    bgColor: 'bg-blue-100',
    action: 'Upsell premium lenses/coatings. Offer loyalty points bonus. Send birthday offers.',
  },
  POTENTIAL_LOYALIST: {
    label: 'Potential Loyalist',
    color: 'text-cyan-700',
    bgColor: 'bg-cyan-100',
    action: 'Offer progressive lens upgrade. Send eye care tips. Recommend annual eye test.',
  },
  NEW_CUSTOMER: {
    label: 'New Customer',
    color: 'text-purple-700',
    bgColor: 'bg-purple-100',
    action: 'Welcome offer. Educate about services. Follow up after first purchase.',
  },
  PROMISING: {
    label: 'Promising',
    color: 'text-indigo-700',
    bgColor: 'bg-indigo-100',
    action: 'Cross-sell sunglasses/contact lenses. Offer family discount packages.',
  },
  NEEDS_ATTENTION: {
    label: 'Needs Attention',
    color: 'text-yellow-700',
    bgColor: 'bg-yellow-100',
    action: 'Send eye test reminder. Offer special discount on next visit. WhatsApp follow-up.',
  },
  ABOUT_TO_SLEEP: {
    label: 'About to Sleep',
    color: 'text-orange-700',
    bgColor: 'bg-orange-100',
    action: 'Urgent recall - eye test overdue. Limited time offer. Personal call from store.',
  },
  AT_RISK: {
    label: 'At Risk',
    color: 'text-red-600',
    bgColor: 'bg-red-100',
    action: 'Win-back campaign. Significant discount. Address any complaints.',
  },
  CANT_LOSE: {
    label: "Can't Lose Them",
    color: 'text-red-700',
    bgColor: 'bg-red-200',
    action: 'Personal outreach by store manager. VIP offers. Investigate why they stopped visiting.',
  },
  HIBERNATING: {
    label: 'Hibernating',
    color: 'text-gray-600',
    bgColor: 'bg-gray-200',
    action: 'Reactivation offer. Eye health awareness campaign. New collection announcement.',
  },
  LOST: {
    label: 'Lost',
    color: 'text-gray-500',
    bgColor: 'bg-gray-100',
    action: 'Survey to understand why. Major promotional offer if cost-effective.',
  },
};

/**
 * Calculate RFM scores for a customer
 * Optical retail typically has longer purchase cycles (12-24 months)
 */
export function calculateRFMScore(customer: CustomerRFMData): RFMScore {
  const r = scoreRecency(customer.daysSinceLastPurchase);
  const f = scoreFrequency(customer.totalOrders);
  const m = scoreMonetary(customer.totalSpend);

  const segment = classifySegment(r, f, m);
  const config = SEGMENT_CONFIG[segment];

  return {
    recency: r,
    frequency: f,
    monetary: m,
    total: r + f + m,
    segment,
    ...config,
  };
}

/**
 * Recency scoring (optical retail: 1-2 year cycles)
 * Score 5: 0-90 days (3 months - very recent)
 * Score 4: 91-180 days (6 months)
 * Score 3: 181-365 days (1 year - normal cycle)
 * Score 2: 366-730 days (2 years - overdue)
 * Score 1: 730+ days (lost)
 */
function scoreRecency(daysSince: number): number {
  if (daysSince <= 90) return 5;
  if (daysSince <= 180) return 4;
  if (daysSince <= 365) return 3;
  if (daysSince <= 730) return 2;
  return 1;
}

/**
 * Frequency scoring (optical retail)
 * Score 5: 5+ orders (very frequent - family buyer or repeat customer)
 * Score 4: 4 orders
 * Score 3: 3 orders
 * Score 2: 2 orders
 * Score 1: 1 order
 */
function scoreFrequency(orderCount: number): number {
  if (orderCount >= 5) return 5;
  if (orderCount >= 4) return 4;
  if (orderCount >= 3) return 3;
  if (orderCount >= 2) return 2;
  return 1;
}

/**
 * Monetary scoring (Indian optical retail pricing)
 * Score 5: ₹50,000+ (premium/luxury eyewear)
 * Score 4: ₹25,000-50,000 (mid-premium)
 * Score 3: ₹10,000-25,000 (standard)
 * Score 2: ₹5,000-10,000 (budget)
 * Score 1: <₹5,000 (economy)
 */
function scoreMonetary(totalSpend: number): number {
  if (totalSpend >= 50000) return 5;
  if (totalSpend >= 25000) return 4;
  if (totalSpend >= 10000) return 3;
  if (totalSpend >= 5000) return 2;
  return 1;
}

/**
 * Classify customer into segment based on RFM scores
 * Based on industry-standard RFM segmentation matrix
 */
function classifySegment(r: number, f: number, m: number): RFMSegment {
  // Champions: High R, High F, High M
  if (r >= 4 && f >= 4 && m >= 4) return 'CHAMPION';

  // Loyal: High F, moderate+ R
  if (f >= 4 && r >= 3) return 'LOYAL';

  // Can't Lose: Low R, High F, High M (were best customers, now inactive)
  if (r <= 2 && f >= 4 && m >= 4) return 'CANT_LOSE';

  // At Risk: Low R, moderate F, moderate+ M
  if (r <= 2 && f >= 3 && m >= 3) return 'AT_RISK';

  // Potential Loyalist: High R, moderate F
  if (r >= 4 && f >= 2 && f <= 3) return 'POTENTIAL_LOYALIST';

  // New Customer: High R, Low F (just started)
  if (r >= 4 && f === 1) return 'NEW_CUSTOMER';

  // Promising: Moderate R, Low F, moderate+ M
  if (r >= 3 && f <= 2 && m >= 3) return 'PROMISING';

  // Needs Attention: Moderate R, Moderate F
  if (r === 3 && f >= 2) return 'NEEDS_ATTENTION';

  // About to Sleep: Low-moderate R, Low F
  if (r === 2 && f <= 2) return 'ABOUT_TO_SLEEP';

  // Hibernating: Very low R, any F
  if (r === 1 && f >= 2) return 'HIBERNATING';

  // Lost: Very low R, Low F
  return 'LOST';
}

/**
 * Get segment distribution summary for dashboard
 */
export function getSegmentSummary(scores: RFMScore[]): { segment: RFMSegment; count: number; percentage: number; config: typeof SEGMENT_CONFIG[RFMSegment] }[] {
  const segmentCounts = new Map<RFMSegment, number>();

  scores.forEach(score => {
    segmentCounts.set(score.segment, (segmentCounts.get(score.segment) || 0) + 1);
  });

  const total = scores.length || 1;

  return Array.from(segmentCounts.entries())
    .map(([segment, count]) => ({
      segment,
      count,
      percentage: Math.round((count / total) * 100),
      config: SEGMENT_CONFIG[segment],
    }))
    .sort((a, b) => b.count - a.count);
}

/**
 * Get all segment configs for display
 */
export function getAllSegmentConfigs() {
  return SEGMENT_CONFIG;
}
