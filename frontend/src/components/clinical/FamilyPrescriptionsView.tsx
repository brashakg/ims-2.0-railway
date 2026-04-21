// ============================================================================
// IMS 2.0 - Family-Based Prescription View
// ============================================================================

import { useState } from 'react';
import { Users, Plus, Eye, Calendar } from 'lucide-react';
import clsx from 'clsx';
import { customerApi } from '../../services/api/customers';

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


export function FamilyPrescriptionsView({
  customerId,
  familyMembers: initialFamilyMembers = [],
  prescriptions = [],
}: FamilyPrescriptionsViewProps) {
  const [members, setMembers] = useState<FamilyMember[]>(initialFamilyMembers);
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [showAddMember, setShowAddMember] = useState(false);
  const [newMemberName, setNewMemberName] = useState('');
  const [newMemberRelationship, setNewMemberRelationship] = useState('');
  const [addError, setAddError] = useState<string | null>(null);
  const [isAdding, setIsAdding] = useState(false);

  const memberPrescriptions = selectedMemberId
    ? prescriptions.filter(p => p.memberId === selectedMemberId)
    : prescriptions;

  const handleAddMember = async () => {
    if (!newMemberName.trim() || !newMemberRelationship) return;
    if (!customerId) {
      setAddError('Customer ID is missing — cannot save family member.');
      return;
    }

    setIsAdding(true);
    setAddError(null);

    try {
      const result = await customerApi.addPatient(customerId, {
        name: newMemberName.trim(),
        relation: newMemberRelationship,
      });

      const newMember: FamilyMember = {
        id: result.patient_id ?? result.patientId ?? String(Date.now()),
        name: newMemberName.trim(),
        relationship: newMemberRelationship,
        phone: '',
      };

      setMembers(prev => [...prev, newMember]);
      setNewMemberName('');
      setNewMemberRelationship('');
      setShowAddMember(false);
    } catch (err) {
      setAddError('Failed to add family member. Please try again.');
    } finally {
      setIsAdding(false);
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
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="flex items-center gap-2 text-gray-900 font-semibold">
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
          {members.map(member => (
            <button
              key={member.id}
              onClick={() => setSelectedMemberId(selectedMemberId === member.id ? null : member.id)}
              className={clsx(
                'px-4 py-2 rounded-lg font-medium transition text-sm',
                selectedMemberId === member.id
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
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
          <div className="mt-4 p-4 bg-gray-50 border border-gray-200 rounded space-y-3">
            <input
              type="text"
              placeholder="Member Name"
              value={newMemberName}
              onChange={(e) => setNewMemberName(e.target.value)}
              className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 outline-none focus:border-blue-500"
            />
            <select
              value={newMemberRelationship}
              onChange={(e) => setNewMemberRelationship(e.target.value)}
              className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 outline-none focus:border-blue-500"
            >
              <option value="">Select Relationship</option>
              <option value="Spouse">Spouse</option>
              <option value="Son">Son</option>
              <option value="Daughter">Daughter</option>
              <option value="Parent">Parent</option>
              <option value="Sibling">Sibling</option>
              <option value="Other">Other</option>
            </select>
            {addError && (
              <p className="text-sm text-red-600">{addError}</p>
            )}
            <div className="flex gap-2">
              <button
                onClick={handleAddMember}
                disabled={isAdding || !newMemberName.trim() || !newMemberRelationship}
                className="flex-1 px-3 py-2 bg-green-600 text-white rounded hover:bg-green-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isAdding ? 'Adding...' : 'Add'}
              </button>
              <button
                onClick={() => { setShowAddMember(false); setAddError(null); }}
                className="flex-1 px-3 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 transition"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Prescriptions for Selected Member or All */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="flex items-center gap-2 text-gray-900 font-semibold mb-4">
          <Eye className="w-5 h-5" />
          Prescriptions
        </h3>

        {memberPrescriptions.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
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
                      ? 'bg-red-50 bg-opacity-20 border-red-600'
                      : daysLeft < 30
                      ? 'bg-yellow-50 bg-opacity-20 border-yellow-600'
                      : 'bg-gray-50 border-gray-200'
                  )}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <h4 className="font-semibold text-gray-900">{rx.memberName}</h4>
                        <span className="text-xs text-gray-500">({rx.relationship})</span>
                        {rx.status === 'expired' && (
                          <span className="text-xs px-2 py-1 rounded bg-red-100 text-red-700">
                            EXPIRED
                          </span>
                        )}
                        {rx.status === 'active' && daysLeft < 30 && (
                          <span className="text-xs px-2 py-1 rounded bg-yellow-100 text-yellow-700">
                            EXPIRING SOON
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-gray-500 mb-2">
                        <strong>Rx ID:</strong> {rx.id} | <strong>Optometrist:</strong> {rx.optometristName}
                      </p>
                      <div className="flex items-center gap-4 text-xs text-gray-500">
                        <div className="flex items-center gap-1">
                          <Calendar className="w-3 h-3" />
                          <span>Created: {formatDate(rx.date)}</span>
                        </div>
                        <div className="flex items-center gap-1">
                          <Calendar className="w-3 h-3" />
                          <span>Valid Until: {formatDate(rx.validUntil)}</span>
                        </div>
                        {rx.status === 'active' && (
                          <div className="ml-auto font-semibold text-yellow-600">
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
