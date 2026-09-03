// ============================================================================
// IMS 2.0 - HR > Leaderboard  (/hr/leaderboard)
// ============================================================================
// The old HRPage `activeTab === 'leaderboard'` block on its own URL. Thin by
// design: CommissionLeaderboard owns the data and the markup; this only feeds
// it the active store, which the tab did inline.
//
// Week-off Swaps and Shifts needed no such wrapper - their components take no
// props, so hrRoutes.tsx lazy-loads them straight into the route.

import { CommissionLeaderboard } from '../../components/hr/CommissionLeaderboard';
import { useAuth } from '../../context/AuthContext';

export function HRLeaderboardPage() {
  const { user } = useAuth();
  return (
    <div className="card p-4">
      <CommissionLeaderboard storeId={user?.activeStoreId} />
    </div>
  );
}

export default HRLeaderboardPage;
