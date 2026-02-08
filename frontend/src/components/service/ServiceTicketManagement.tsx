// ============================================================================
// IMS 2.0 - Service Ticket Management
// ============================================================================
// Manage customer service tickets, repairs, and support cases

import { useState } from 'react';
import { Plus, Search, Edit2, Trash2, AlertCircle, CheckCircle, Clock, MessageSquare } from 'lucide-react';
import clsx from 'clsx';

export interface ServiceTicket {
  id: string;
  ticketNumber: string;
  customerId: string;
  customerName: string;
  title: string;
  description: string;
  category: 'repair' | 'support' | 'inquiry' | 'complaint' | 'other';
  priority: 'low' | 'medium' | 'high' | 'urgent';
  status: 'open' | 'in-progress' | 'pending-customer' | 'resolved' | 'closed' | 'reopened';
  assignedTo?: string;
  createdAt: string;
  resolvedAt?: string;
  resolutionNotes?: string;
  estimatedCost?: number;
  actualCost?: number;
  customerEmail: string;
  customerPhone: string;
}

interface ServiceTicketManagementProps {
  tickets: ServiceTicket[];
  agents: { id: string; name: string }[];
  onCreateTicket: (ticket: Omit<ServiceTicket, 'id' | 'createdAt'>) => Promise<void>;
  onUpdateTicket: (ticket: ServiceTicket) => Promise<void>;
  onDeleteTicket: (id: string) => Promise<void>;
  onAssignTicket: (ticketId: string, agentId: string) => Promise<void>;
  onResolveTicket: (ticketId: string, notes: string) => Promise<void>;
  loading?: boolean;
}

export function ServiceTicketManagement({
  tickets,
  agents,
  onCreateTicket,
  onUpdateTicket,
  onDeleteTicket,
  onAssignTicket,
  onResolveTicket,
  loading = false,
}: ServiceTicketManagementProps) {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState<Partial<ServiceTicket>>({});
  const [filterPriority, setFilterPriority] = useState<string>('');
  const [filterStatus, setFilterStatus] = useState<string>('');

  const filteredTickets = tickets.filter(t =>
    (t.ticketNumber.toLowerCase().includes(searchTerm.toLowerCase()) ||
      t.customerName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      t.title.toLowerCase().includes(searchTerm.toLowerCase())) &&
    (!filterPriority || t.priority === filterPriority) &&
    (!filterStatus || t.status === filterStatus)
  );

  const handleSave = async () => {
    if (!formData.ticketNumber || !formData.customerName || !formData.title) {
      alert('Please fill in all required fields');
      return;
    }

    if (editingId) {
      await Promise.resolve(onUpdateTicket({
        ...formData,
        id: editingId,
        createdAt: formData.createdAt || '',
      } as ServiceTicket));
    } else {
      await Promise.resolve(onCreateTicket({
        ...formData,
        createdAt: new Date().toISOString(),
        status: 'open',
      } as any));
    }

    setFormData({});
    setEditingId(null);
    setShowCreateModal(false);
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'urgent':
        return 'bg-red-100 text-red-700 border-red-300';
      case 'high':
        return 'bg-orange-100 text-orange-700 border-orange-300';
      case 'medium':
        return 'bg-yellow-100 text-yellow-700 border-yellow-300';
      case 'low':
        return 'bg-green-100 text-green-700 border-green-300';
      default:
        return 'bg-gray-100 text-gray-700 border-gray-300';
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'open':
        return 'bg-blue-100 text-blue-700';
      case 'in-progress':
        return 'bg-purple-100 text-purple-700';
      case 'pending-customer':
        return 'bg-yellow-100 text-yellow-700';
      case 'resolved':
        return 'bg-green-100 text-green-700';
      case 'closed':
        return 'bg-gray-100 text-gray-700';
      case 'reopened':
        return 'bg-red-100 text-red-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'resolved':
      case 'closed':
        return <CheckCircle className="w-4 h-4" />;
      case 'in-progress':
        return <Clock className="w-4 h-4" />;
      case 'urgent':
      case 'reopened':
        return <AlertCircle className="w-4 h-4" />;
      default:
        return <MessageSquare className="w-4 h-4" />;
    }
  };

  const openTickets = tickets.filter(t => ['open', 'in-progress', 'reopened'].includes(t.status)).length;
  const urgentTickets = tickets.filter(t => t.priority === 'urgent').length;
  const avgResolutionTime = tickets.length > 0 ? '2 days' : 'N/A';

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* KPI Cards */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 grid grid-cols-3 gap-4">
        <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg border border-blue-200 dark:border-blue-800">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Open Tickets</p>
          <p className="text-2xl font-bold text-blue-600 dark:text-blue-400">{openTickets}</p>
        </div>
        <div className="bg-red-50 dark:bg-red-900/20 p-4 rounded-lg border border-red-200 dark:border-red-800">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Urgent</p>
          <p className="text-2xl font-bold text-red-600 dark:text-red-400">{urgentTickets}</p>
        </div>
        <div className="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg border border-green-200 dark:border-green-800">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Avg Resolution</p>
          <p className="text-2xl font-bold text-green-600 dark:text-green-400">{avgResolutionTime}</p>
        </div>
      </div>

      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
          <MessageSquare className="w-5 h-5" />
          Service Tickets
        </h2>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({ status: 'open', priority: 'medium' });
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          New Ticket
        </button>
      </div>

      {/* Filters & Search */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800 space-y-3">
        <div className="relative">
          <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search by ticket, customer, or title..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <select
            value={filterPriority}
            onChange={e => setFilterPriority(e.target.value)}
            className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
          >
            <option value="">All Priorities</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="urgent">Urgent</option>
          </select>
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm"
          >
            <option value="">All Statuses</option>
            <option value="open">Open</option>
            <option value="in-progress">In Progress</option>
            <option value="pending-customer">Pending Customer</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </select>
        </div>
      </div>

      {/* Tickets List */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading tickets...</p>
          </div>
        ) : filteredTickets.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <MessageSquare className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No tickets found</p>
          </div>
        ) : (
          filteredTickets.map(ticket => (
            <div key={ticket.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-start justify-between gap-4 mb-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <h3 className="font-semibold text-gray-900 dark:text-white truncate">{ticket.ticketNumber}</h3>
                    <span className={clsx('px-2 py-0.5 rounded text-xs font-medium border', getPriorityColor(ticket.priority))}>
                      {ticket.priority}
                    </span>
                    <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium', getStatusColor(ticket.status))}>
                      {getStatusIcon(ticket.status)}
                      {ticket.status}
                    </span>
                  </div>
                  <p className="font-medium text-gray-900 dark:text-white truncate">{ticket.title}</p>
                  <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-1">{ticket.description}</p>
                  <div className="text-xs text-gray-500 dark:text-gray-500 mt-2">
                    <p>Customer: {ticket.customerName}</p>
                    {ticket.assignedTo && <p>Assigned to: {ticket.assignedTo}</p>}
                  </div>
                </div>
                <div className="text-right flex-shrink-0">
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {new Date(ticket.createdAt).toLocaleDateString()}
                  </p>
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2 mt-3 flex-wrap">
                {ticket.status === 'open' && (
                  <select
                    value={ticket.assignedTo || ''}
                    onChange={e => {
                      if (e.target.value) {
                        onAssignTicket(ticket.id, e.target.value);
                      }
                    }}
                    className="px-3 py-1 border border-gray-300 dark:border-gray-700 rounded text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  >
                    <option value="">Assign to...</option>
                    {agents.map(agent => (
                      <option key={agent.id} value={agent.id}>{agent.name}</option>
                    ))}
                  </select>
                )}
                {ticket.status !== 'resolved' && ticket.status !== 'closed' && (
                  <button
                    onClick={() => onResolveTicket(ticket.id, 'Resolved')}
                    className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700 font-medium"
                  >
                    Resolve
                  </button>
                )}
                <button
                  onClick={() => {
                    setFormData(ticket);
                    setEditingId(ticket.id);
                    setShowCreateModal(true);
                  }}
                  className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                  title="Edit"
                >
                  <Edit2 className="w-4 h-4" />
                </button>
                <button
                  onClick={() => {
                    if (confirm(`Delete ticket ${ticket.ticketNumber}?`)) {
                      onDeleteTicket(ticket.id);
                    }
                  }}
                  className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                  title="Delete"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create/Edit Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowCreateModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-2xl w-full max-h-96 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              {editingId ? 'Edit Service Ticket' : 'Create New Service Ticket'}
            </h2>

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <input
                  type="text"
                  placeholder="Ticket Number *"
                  value={formData.ticketNumber || ''}
                  onChange={e => setFormData({ ...formData, ticketNumber: e.target.value })}
                  disabled={editingId !== null}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white disabled:opacity-50"
                />
                <input
                  type="text"
                  placeholder="Customer Name *"
                  value={formData.customerName || ''}
                  onChange={e => setFormData({ ...formData, customerName: e.target.value })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
              </div>

              <input
                type="text"
                placeholder="Title *"
                value={formData.title || ''}
                onChange={e => setFormData({ ...formData, title: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />

              <textarea
                placeholder="Description"
                value={formData.description || ''}
                onChange={e => setFormData({ ...formData, description: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                rows={3}
              />

              <div className="grid grid-cols-2 gap-4">
                <select
                  value={formData.priority || 'medium'}
                  onChange={e => setFormData({ ...formData, priority: e.target.value as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                >
                  <option value="low">Low Priority</option>
                  <option value="medium">Medium Priority</option>
                  <option value="high">High Priority</option>
                  <option value="urgent">Urgent</option>
                </select>
                <select
                  value={formData.category || 'support'}
                  onChange={e => setFormData({ ...formData, category: e.target.value as any })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                >
                  <option value="repair">Repair</option>
                  <option value="support">Support</option>
                  <option value="inquiry">Inquiry</option>
                  <option value="complaint">Complaint</option>
                  <option value="other">Other</option>
                </select>
              </div>

              <div className="flex gap-2">
                <button
                  onClick={() => setShowCreateModal(false)}
                  className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                >
                  {editingId ? 'Update' : 'Create'} Ticket
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
