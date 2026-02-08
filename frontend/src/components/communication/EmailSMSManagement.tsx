// ============================================================================
// IMS 2.0 - Email & SMS Communication Management
// ============================================================================
// Send and track email and SMS communications to customers

import { useState } from 'react';
import { Plus, Search, Send, Check, AlertCircle, Mail, MessageSquare, Eye, Trash2 } from 'lucide-react';
import clsx from 'clsx';

export interface Communication {
  id: string;
  type: 'email' | 'sms';
  recipientId: string;
  recipientName: string;
  recipientAddress: string; // email or phone
  subject?: string;
  message: string;
  status: 'draft' | 'scheduled' | 'sent' | 'failed' | 'bounced';
  sentAt?: string;
  failureReason?: string;
  templateId?: string;
  createdAt: string;
  scheduledFor?: string;
}

interface EmailSMSManagementProps {
  communications: Communication[];
  templates: { id: string; name: string; type: 'email' | 'sms' }[];
  onCreateCommunication: (comm: Omit<Communication, 'id' | 'createdAt'>) => Promise<void>;
  onUpdateCommunication: (comm: Communication) => Promise<void>;
  onDeleteCommunication: (id: string) => Promise<void>;
  onSendCommunication: (id: string) => Promise<void>;
  loading?: boolean;
}

export function EmailSMSManagement({
  communications,
  templates,
  onCreateCommunication,
  onUpdateCommunication,
  onDeleteCommunication,
  onSendCommunication,
  loading = false,
}: EmailSMSManagementProps) {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [activeTab, setActiveTab] = useState<'email' | 'sms'>('email');
  const [searchTerm, setSearchTerm] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState<Partial<Communication>>({});

  const filteredCommunications = communications
    .filter(c => c.type === activeTab)
    .filter(c =>
      c.recipientName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      c.recipientAddress.toLowerCase().includes(searchTerm.toLowerCase())
    );

  const handleSave = async () => {
    if (!formData.recipientId || !formData.recipientAddress || !formData.message) {
      alert('Please fill in all required fields');
      return;
    }

    if (editingId) {
      await Promise.resolve(onUpdateCommunication({
        ...formData,
        id: editingId,
        createdAt: formData.createdAt || '',
      } as Communication));
    } else {
      await Promise.resolve(onCreateCommunication({
        ...formData,
        createdAt: new Date().toISOString(),
        type: activeTab,
        status: 'draft',
      } as any));
    }

    setFormData({});
    setEditingId(null);
    setShowCreateModal(false);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'draft':
        return 'bg-gray-100 text-gray-700';
      case 'scheduled':
        return 'bg-blue-100 text-blue-700';
      case 'sent':
        return 'bg-green-100 text-green-700';
      case 'failed':
        return 'bg-red-100 text-red-700';
      case 'bounced':
        return 'bg-orange-100 text-orange-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'sent':
        return <Check className="w-4 h-4" />;
      case 'failed':
      case 'bounced':
        return <AlertCircle className="w-4 h-4" />;
      default:
        return <Mail className="w-4 h-4" />;
    }
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <Mail className="w-5 h-5" />
            Communications
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            {filteredCommunications.length} of {communications.length} messages
          </p>
        </div>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({ type: activeTab, status: 'draft' });
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          New Message
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200 dark:border-gray-800 flex">
        <button
          onClick={() => setActiveTab('email')}
          className={clsx(
            'flex-1 px-6 py-3 font-medium border-b-2 transition-colors flex items-center justify-center gap-2',
            activeTab === 'email'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
          )}
        >
          <Mail className="w-4 h-4" />
          Email
        </button>
        <button
          onClick={() => setActiveTab('sms')}
          className={clsx(
            'flex-1 px-6 py-3 font-medium border-b-2 transition-colors flex items-center justify-center gap-2',
            activeTab === 'sms'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
          )}
        >
          <MessageSquare className="w-4 h-4" />
          SMS
        </button>
      </div>

      {/* Search */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <div className="relative">
          <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder={activeTab === 'email' ? 'Search by recipient or email...' : 'Search by recipient or phone...'}
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
      </div>

      {/* Communications List */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading messages...</p>
          </div>
        ) : filteredCommunications.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            {activeTab === 'email' ? <Mail className="w-12 h-12 mx-auto mb-3 opacity-50" /> : <MessageSquare className="w-12 h-12 mx-auto mb-3 opacity-50" />}
            <p>No {activeTab} messages found</p>
          </div>
        ) : (
          filteredCommunications.map(comm => (
            <div key={comm.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-start justify-between gap-4 mb-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="font-semibold text-gray-900 dark:text-white truncate">
                      {comm.recipientName}
                    </h3>
                    <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium flex-shrink-0', getStatusColor(comm.status))}>
                      {getStatusIcon(comm.status)}
                      {comm.status}
                    </span>
                  </div>
                  <p className="text-sm text-gray-600 dark:text-gray-400 truncate">
                    {comm.recipientAddress}
                  </p>
                </div>
                <div className="text-right flex-shrink-0">
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {new Date(comm.createdAt).toLocaleString()}
                  </p>
                </div>
              </div>

              {/* Message Preview */}
              <div className="text-sm text-gray-700 dark:text-gray-300 p-2 bg-gray-50 dark:bg-gray-800 rounded mb-3 line-clamp-2">
                {comm.subject && <p className="font-medium text-gray-900 dark:text-white mb-1">{comm.subject}</p>}
                {comm.message}
              </div>

              {/* Meta Information */}
              <div className="text-xs text-gray-500 dark:text-gray-400 space-y-1">
                {comm.sentAt && <p>Sent: {new Date(comm.sentAt).toLocaleString()}</p>}
                {comm.scheduledFor && <p>Scheduled for: {new Date(comm.scheduledFor).toLocaleString()}</p>}
                {comm.failureReason && <p className="text-red-600 dark:text-red-400">Failure: {comm.failureReason}</p>}
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2 mt-3 flex-wrap">
                {comm.status === 'draft' && (
                  <>
                    <button
                      onClick={() => onSendCommunication(comm.id)}
                      className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700 font-medium flex items-center gap-1"
                    >
                      <Send className="w-4 h-4" />
                      Send
                    </button>
                    <button
                      onClick={() => {
                        setFormData(comm);
                        setEditingId(comm.id);
                        setShowCreateModal(true);
                      }}
                      className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                      title="Edit"
                    >
                      <Eye className="w-4 h-4" />
                    </button>
                  </>
                )}
                <button
                  onClick={() => {
                    if (confirm(`Delete this ${comm.type} message?`)) {
                      onDeleteCommunication(comm.id);
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
              {editingId ? 'Edit Message' : `Create New ${activeTab === 'email' ? 'Email' : 'SMS'}`}
            </h2>

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <input
                  type="text"
                  placeholder="Recipient Name *"
                  value={formData.recipientName || ''}
                  onChange={e => setFormData({ ...formData, recipientName: e.target.value })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <input
                  type={activeTab === 'email' ? 'email' : 'tel'}
                  placeholder={activeTab === 'email' ? 'Email Address *' : 'Phone Number *'}
                  value={formData.recipientAddress || ''}
                  onChange={e => setFormData({ ...formData, recipientAddress: e.target.value })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <input
                  type="text"
                  placeholder="Recipient ID"
                  value={formData.recipientId || ''}
                  onChange={e => setFormData({ ...formData, recipientId: e.target.value })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                {activeTab === 'email' && (
                  <input
                    type="text"
                    placeholder="Subject"
                    value={formData.subject || ''}
                    onChange={e => setFormData({ ...formData, subject: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                )}
              </div>

              {templates.filter(t => t.type === activeTab).length > 0 && (
                <select
                  onChange={e => {
                    const template = templates.find(t => t.id === e.target.value);
                    if (template) setFormData({ ...formData, templateId: e.target.value });
                  }}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                >
                  <option value="">Use template...</option>
                  {templates.filter(t => t.type === activeTab).map(t => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </select>
              )}

              <textarea
                placeholder={`${activeTab === 'email' ? 'Email' : 'Message'} Content *`}
                value={formData.message || ''}
                onChange={e => setFormData({ ...formData, message: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                rows={4}
              />

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
                  {editingId ? 'Update' : 'Create'} Message
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
