// ============================================================================
// IMS 2.0 - Family-Based Prescription View
// ============================================================================

import { useState } from 'react';
import { Users, Plus, Eye, Calendar } from 'lucide-react';
import clsx from 'clsx';

interface FamilyMember {
  id: string;
  name: string;
  relationship: string;
  phone: string;
  age?: number;
}

interface FamilyPrescription {
  id: string;
  memberId: string;
  memberName: string;
  relationship: string;
  date: string;
  optometristName: string;
  validUntil: string;
  status: 'active' | 'expired';
}

interface FamilyPrescriptionsViewProps {
  customerId: string;
  customerName: string;
  customerPhone: string;
  familyMembers?: FamilyMember[];
  prescriptions?: FamilyPrescription[];
}

const SAMPLE_FAMILY: FamilyMember[] = [
  { id: 'm-001', name: 'Rajesh Kumar', relationship: 'Self', phone: '9876543210', age: 42 },
  { id: 'm-002', name: 'Anjali Kumar', relationship: 'Wife', phone: '9876543210', age: 40 },
  { id: 'm-003', name: 'Arjun Kumar', relationship: 'Son', phone: '9876543210', age: 18 },
  { id: 'm-004', name: 'Priya Kumar', relationship: 'Daughter', phone: '9876543210', age: 15 },
];

const SAMPLE_PRESCRIPTIONS: FamilyPrescription[] = [
  { id: 'rx-001', memberId: 'm-001', memberName: 'Rajesh Kumar', relationship: 'Self', date: new Date(Date.now() - 30 * 24 * 60 * 60000).toISOString(), optometristName: 'Dr. Sharma', validUntil: new Date(Date.now() + 700 * 24 * 60 * 60000).toISOString(), status: 'active' },
  { id: 'rx-002', memberId: 'm-002', memberName: 'Anjali Kumar', relationship: 'Wife', date: new Date(Date.now() - 60 * 24 * 60 * 60000).toISOString(), optometristName: 'Dr. Patel', validUntil: new Date(Date.now() + 670 * 24 * 60 * 60000).toISOString(), status: 'active' },
  { id: 'rx-003', memberId: 'm-003', memberName: 'Arjun Kumar', relationship: 'Son', date: new Date(Date.now() - 120 * 24 * 60 * 60000).toISOString(), optometristName: 'Dr. Sharma', validUntil: new Date(Date.now() - 5 * 24 * 60 * 60000).toISOString(), status: 'expired' },
  { id: 'rx-004', memberId: 'm-004', memberName: 'Priya Kumar', relationship: 'Daughter', date: new Date(Date.now() - 200 * 24 * 60 * 60000).toISOString(), optometristName: 'Dr. Kumar', validUntil: new Date(Date.now() + 500 * 24 * 60 * 60000).toISOString(), status: 'active' },
];

export function FamilyPrescriptionsView({
  familyMembers = SAMPLE_FAMILY,
  prescriptions = SAMPLE_PRESCRIPTIONS,
}: FamilyPrescriptionsViewProps) {
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [showAddMember, setShowAddMember] = useState(false);
  const [newMemberName, setNewMemberName] = useState('');
  const [newMemberRelationship, setNewMemberRelationship] = useState('');

  const memberPrescriptions = selectedMemberId
    ? prescriptions.filter(p => p.memberId === selectedMemberId)
    : prescriptions;

  const handleAddMember = () => {
    if (newMemberName && newMemberRelationship) {
      // TODO: Call API to add family member
      setNewMemberName('');
      setNewMemberRelationship('');
      setShowAddMember(false);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return new Intl.DateTimeFormat('en-IN', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    }).format(date);
  };

  const daysUntilExpiry = (validUntil: string) => {
    const diff = new Date(validUntil).getTime() - new Date().getTime();
    return Math.ceil(diff / (24 * 60 * 60000));
  };

  return (
    <div className="space-y-6">
      {/* Family Members Tabs */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="flex items-center gap-2 text-white font-semibold">
            <Users className="w-5 h-5" />
            Family Members
          </h3>
          <button
            onClick={() => setShowAddMember(true)}
            className="flex items-center gap-2 px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 transition"
          >
            <Plus className="w-4 h-4" />
            Add Member
          </button>
        </div>

        {/* Family Members List */}
        <div className="flex gap-2 flex-wrap">
          {familyMembers.map(member => (
            <button
              key={member.id}
              onClick={() => setSelectedMemberId(selectedMemberId === member.id ? null : member.id)}
              className={clsx(
                'px-4 py-2 rounded-lg font-medium transition text-sm',
                selectedMemberId === member.id
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              )}
            >
              <div className="text-left">
                <div>{member.name}</div>
                <div className="text-xs text-opacity-75">{member.relationship}</div>
              </div>
            </button>
          ))}
        </div>

        {/* Add Member Form */}
        {showAddMember && (
          <div className="mt-4 p-4 bg-gray-700 rounded space-y-3">
            <input
              type="text"
              placeholder="Member Name"
              value={newMemberName}
              onChange={(e) => setNewMemberName(e.target.value)}
              className="w-full px-3 py-2 bg-gray-600 border border-gray-500 rounded text-white placeholder-gray-400 outline-none focus:border-blue-500"
            />
            <select
              value={newMemberRelationship}
              onChange={(e) => setNewMemberRelationship(e.target.value)}
              className="w-full px-3 py-2 bg-gray-600 border border-gray-500 rounded text-white outline-none focus:border-blue-500"
            >
              <option value="">Select Relationship</option>
              <option value="Spouse">Spouse</option>
              <option value="Son">Son</option>
              <option value="Daughter">Daughter</option>
              <option value="Parent">Parent</option>
              <option value="Sibling">Sibling</option>
              <option value="Other">Other</option>
            </select>
            <div className="flex gap-2">
              <button
                onClick={handleAddMember}
                className="flex-1 px-3 py-2 bg-green-600 text-white rounded hover:bg-green-700 transition"
              >
                Add
              </button>
              <button
                onClick={() => setShowAddMember(false)}
                className="flex-1 px-3 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 transition"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Prescriptions for Selected Member or All */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <h3 className="flex items-center gap-2 text-white font-semibold mb-4">
          <Eye className="w-5 h-5" />
          Prescriptions
        </h3>

        {memberPrescriptions.length === 0 ? (
          <div className="text-center py-8 text-gray-400">
            <p>No prescriptions found</p>
          </div>
        ) : (
          <div className="space-y-3">
            {memberPrescriptions.map(rx => {
              const daysLeft = daysUntilExpiry(rx.validUntil);
              return (
                <div
                  key={rx.id}
                  className={clsx(
                    'p-4 rounded-lg border-2 transition',
                    rx.status === 'expired'
                      ? 'bg-red-900 bg-opacity-20 border-red-600'
                      : daysLeft < 30
                      ? 'bg-yellow-900 bg-opacity-20 border-yellow-600'
                      : 'bg-gray-700 border-gray-600'
                  )}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <h4 className="font-semibold text-white">{rx.memberName}</h4>
                        <span className="text-xs text-gray-400">({rx.relationship})</span>
                        {rx.status === 'expired' && (
                          <span className="text-xs px-2 py-1 rounded bg-red-900 text-red-200">
                            EXPIRED
                          </span>
                        )}
                        {rx.status === 'active' && daysLeft < 30 && (
                          <span className="text-xs px-2 py-1 rounded bg-yellow-900 text-yellow-200">
                            EXPIRING SOON
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-gray-400 mb-2">
                        <strong>Rx ID:</strong> {rx.id} | <strong>Optometrist:</strong> {rx.optometristName}
                      </p>
                      <div className="flex items-center gap-4 text-xs text-gray-400">
                        <div className="flex items-center gap-1">
                          <Calendar className="w-3 h-3" />
                          <span>Created: {formatDate(rx.date)}</span>
                        </div>
                        <div className="flex items-center gap-1">
                          <Calendar className="w-3 h-3" />
                          <span>Valid Until: {formatDate(rx.validUntil)}</span>
                        </div>
                        {rx.status === 'active' && (
                          <div className="ml-auto font-semibold text-yellow-400">
                            {daysLeft} days remaining
                          </div>
                        )}
                      </div>
                    </div>
                    <button className="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 transition ml-4">
                      View Details
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
