// D53: a shop-day with more than one cash drawer. Each drawer is shown with
// its OWN count and (once a manager locked it) its own variance. Drawers are
// never added together and no whole-day variance is shown.
import type { TillDrawerSummary } from '../../services/api/reports';

const rupees = (paisa: number) => `₹${Math.round(paisa / 100).toLocaleString('en-IN')}`;

export default function TillDrawerList({
  drawers,
  testId,
}: {
  drawers?: TillDrawerSummary[] | null;
  testId?: string;
}) {
  if (!drawers?.length) return null;
  return (
    <ul data-testid={testId} className="text-xs text-gray-600 mt-1 space-y-0.5">
      {drawers.map((d, i) => (
        <li key={d.session_id}>
          Drawer {i + 1}:{' '}
          {d.counted_paisa == null ? 'not counted' : `counted ${rupees(d.counted_paisa)}`}
          {d.variance_paisa != null
            ? `, variance ${d.variance_paisa > 0 ? '+' : d.variance_paisa < 0 ? '-' : ''}${rupees(Math.abs(d.variance_paisa))}`
            : d.counted_paisa != null && d.status !== 'LOCKED'
              ? ', variance shown once a manager locks it'
              : ''}
        </li>
      ))}
    </ul>
  );
}
