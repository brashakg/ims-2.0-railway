// Every screen inside the app shell, one module group at a time. App.tsx
// mounts this under the AppLayout route, and AppLayout's breadcrumbs match
// a parent path against it, so a crumb asks the very gates the router
// enforces (ProtectedRoute's gateAllows).
import { dashboardRoutes } from './dashboardRoutes';
import { approvalRoutes } from './approvalRoutes';
import { posRoutes } from './posRoutes';
import { customerRoutes } from './customerRoutes';
import { onlineStoreRoutes } from './onlineStoreRoutes';
import { inventoryRoutes } from './inventoryRoutes';
import { orderRoutes } from './orderRoutes';
import { incentiveRoutes } from './incentiveRoutes';
import { clinicalRoutes } from './clinicalRoutes';
import { workshopRoutes } from './workshopRoutes';
import { purchaseRoutes } from './purchaseRoutes';
import { taskRoutes } from './taskRoutes';
import { hrRoutes } from './hrRoutes';
import { reportRoutes } from './reportRoutes';
import { settingsRoutes } from './settingsRoutes';
import { catalogRoutes } from './catalogRoutes';
import { financeRoutes } from './financeRoutes';

export const appRoutes = (
  <>
    {dashboardRoutes}
    {approvalRoutes}
    {posRoutes}
    {customerRoutes}
    {onlineStoreRoutes}
    {inventoryRoutes}
    {orderRoutes}
    {incentiveRoutes}
    {clinicalRoutes}
    {workshopRoutes}
    {purchaseRoutes}
    {taskRoutes}
    {hrRoutes}
    {reportRoutes}
    {settingsRoutes}
    {catalogRoutes}
    {financeRoutes}
  </>
);
