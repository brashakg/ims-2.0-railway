// ============================================================================
// IMS 2.0 - Shared test fixtures
// ============================================================================
// Stable mock entities for unit/integration tests. Kept under src/utils so the
// app tsconfig's *.test.* exclusion does not strip it (it is imported BY tests,
// it is not itself a test file). Nothing in the production bundle imports this.

export interface MockUser {
  id: string;
  username: string;
  email: string;
  name: string;
  roles: string[];
  activeRole: string;
  storeId: string | null;
}

export const mockUsers: Record<'admin' | 'staff', MockUser> = {
  admin: {
    id: 'usr_admin',
    username: 'admin',
    email: 'admin@ims.local',
    name: 'Avinash (CEO)',
    roles: ['ADMIN'],
    activeRole: 'ADMIN',
    storeId: null,
  },
  staff: {
    id: 'usr_staff',
    username: 'staff1',
    email: 'staff@ims.local',
    name: 'Counter Staff',
    roles: ['STAFF'],
    activeRole: 'STAFF',
    storeId: 'store_ranchi',
  },
};
