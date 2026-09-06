// ============================================================================
// IMS 2.0 - Workshop: search + status/priority filter bar
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet).

import { Search } from 'lucide-react';
import { STATUS_CONFIG } from './shared';
import type { WorkshopPageState } from './useWorkshopPage';

export function WorkshopFilters({ page }: { page: WorkshopPageState }) {
  const { searchQuery, setSearchQuery, statusFilter, setStatusFilter, priorityFilter, setPriorityFilter } = page;
  return (
    <>
      {/* Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="input-field pl-10"
              placeholder="Search by job number, customer, order..."
            />
          </div>
          <div className="flex gap-2 flex-wrap">
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as typeof statusFilter)}
              className="input-field w-auto"
              title="Filter by status"
            >
              <option value="ACTIVE">Active Jobs</option>
              <option value="ALL">All Status</option>
              {Object.entries(STATUS_CONFIG).map(([status, config]) => (
                <option key={status} value={status}>{config.label}</option>
              ))}
            </select>
            <select
              value={priorityFilter}
              onChange={e => setPriorityFilter(e.target.value as typeof priorityFilter)}
              className="input-field w-auto"
              title="Filter by priority"
            >
              <option value="ALL">All Priority</option>
              <option value="URGENT">Urgent</option>
              <option value="EXPRESS">Express</option>
              <option value="NORMAL">Normal</option>
            </select>
          </div>
        </div>
      </div>

    </>
  );
}
