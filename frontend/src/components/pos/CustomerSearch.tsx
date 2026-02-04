// ============================================================================
// IMS 2.0 - Customer Search Component
// ============================================================================

import { useState, useCallback } from 'react';
import { Search, UserPlus, Phone, User, ChevronRight } from 'lucide-react';
import type { Customer } from '../../types';

interface CustomerSearchProps {
  onSelect: (customer: Customer) => void;
}

// Mock customer data for demo
const mockCustomers: Customer[] = [
  {
    id: 'cust-001',
    name: 'Rajesh Kumar',
    phone: '9876543210',
    email: 'rajesh@email.com',
    customerType: 'B2C',
    patients: [
      { id: 'pat-001', customerId: 'cust-001', name: 'Rajesh Kumar', relation: 'Self' },
      { id: 'pat-002', customerId: 'cust-001', name: 'Priya Kumar', relation: 'Wife' },
    ],
    createdAt: '2024-01-15',
  },
  {
    id: 'cust-002',
    name: 'Sunita Devi',
    phone: '9123456789',
    email: 'sunita@email.com',
    customerType: 'B2C',
    patients: [
      { id: 'pat-003', customerId: 'cust-002', name: 'Sunita Devi', relation: 'Self' },
    ],
    createdAt: '2024-02-20',
  },
  {
    id: 'cust-003',
    name: 'Amit Sharma',
    phone: '9988776655',
    customerType: 'B2C',
    patients: [
      { id: 'pat-004', customerId: 'cust-003', name: 'Amit Sharma', relation: 'Self' },
      { id: 'pat-005', customerId: 'cust-003', name: 'Ravi Sharma', relation: 'Son' },
      { id: 'pat-006', customerId: 'cust-003', name: 'Meena Sharma', relation: 'Daughter' },
    ],
    createdAt: '2024-03-10',
  },
];

export function CustomerSearch({ onSelect }: CustomerSearchProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Customer[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newCustomer, setNewCustomer] = useState({ name: '', phone: '', email: '' });

  // Search handler
  const handleSearch = useCallback(async (query: string) => {
    setSearchQuery(query);

    if (query.length < 3) {
      setSearchResults([]);
      return;
    }

    setIsSearching(true);

    // Simulate API call - replace with actual API
    setTimeout(() => {
      const results = mockCustomers.filter(
        c =>
          c.phone.includes(query) ||
          c.name.toLowerCase().includes(query.toLowerCase())
      );
      setSearchResults(results);
      setIsSearching(false);
    }, 300);
  }, []);

  // Create new customer
  const handleCreateCustomer = useCallback(async () => {
    if (!newCustomer.name || !newCustomer.phone) return;

    // Simulate API call - replace with actual API
    const customer: Customer = {
      id: `cust-${Date.now()}`,
      name: newCustomer.name,
      phone: newCustomer.phone,
      email: newCustomer.email || undefined,
      customerType: 'B2C',
      patients: [
        {
          id: `pat-${Date.now()}`,
          customerId: `cust-${Date.now()}`,
          name: newCustomer.name,
          relation: 'Self',
        },
      ],
      createdAt: new Date().toISOString(),
    };

    onSelect(customer);
    setShowCreateForm(false);
    setNewCustomer({ name: '', phone: '', email: '' });
  }, [newCustomer, onSelect]);

  // Render create form
  if (showCreateForm) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-medium text-gray-900">New Customer</h3>
          <button
            onClick={() => setShowCreateForm(false)}
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            Cancel
          </button>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Name *
            </label>
            <input
              type="text"
              value={newCustomer.name}
              onChange={e => setNewCustomer(prev => ({ ...prev, name: e.target.value }))}
              className="input-field"
              placeholder="Customer name"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Phone *
            </label>
            <input
              type="tel"
              value={newCustomer.phone}
              onChange={e => setNewCustomer(prev => ({ ...prev, phone: e.target.value }))}
              className="input-field"
              placeholder="10-digit mobile"
              maxLength={10}
            />
          </div>
          <div className="col-span-2">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Email (Optional)
            </label>
            <input
              type="email"
              value={newCustomer.email}
              onChange={e => setNewCustomer(prev => ({ ...prev, email: e.target.value }))}
              className="input-field"
              placeholder="email@example.com"
            />
          </div>
        </div>

        <button
          onClick={handleCreateCustomer}
          disabled={!newCustomer.name || !newCustomer.phone || newCustomer.phone.length !== 10}
          className="btn-primary w-full"
        >
          Create & Continue
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h3 className="font-medium text-gray-900">Customer</h3>
        <span className="text-sm text-gray-500">Search by phone or name</span>
      </div>

      {/* Search Input */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
        <input
          type="text"
          value={searchQuery}
          onChange={e => handleSearch(e.target.value)}
          className="input-field pl-10 pr-4"
          placeholder="Enter phone number or name..."
          autoFocus
        />
        {isSearching && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <div className="w-5 h-5 border-2 border-bv-red-600 border-t-transparent rounded-full animate-spin" />
          </div>
        )}
      </div>

      {/* Search Results */}
      {searchResults.length > 0 && (
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100 max-h-60 overflow-y-auto">
          {searchResults.map(customer => (
            <button
              key={customer.id}
              onClick={() => onSelect(customer)}
              className="w-full flex items-center gap-3 p-3 hover:bg-gray-50 transition-colors text-left"
            >
              <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center">
                <User className="w-5 h-5 text-gray-500" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-medium text-gray-900 truncate">{customer.name}</p>
                <p className="text-sm text-gray-500 flex items-center gap-1">
                  <Phone className="w-3 h-3" />
                  {customer.phone}
                </p>
              </div>
              {customer.patients && customer.patients.length > 1 && (
                <span className="badge bg-gray-100 text-gray-600">
                  {customer.patients.length} patients
                </span>
              )}
              <ChevronRight className="w-5 h-5 text-gray-400" />
            </button>
          ))}
        </div>
      )}

      {/* No Results */}
      {searchQuery.length >= 3 && !isSearching && searchResults.length === 0 && (
        <div className="text-center py-6 border border-dashed border-gray-300 rounded-lg">
          <User className="w-10 h-10 text-gray-300 mx-auto mb-2" />
          <p className="text-gray-500 mb-3">No customer found</p>
          <button
            onClick={() => {
              setShowCreateForm(true);
              setNewCustomer(prev => ({
                ...prev,
                phone: searchQuery.match(/^\d+$/) ? searchQuery : '',
                name: !searchQuery.match(/^\d+$/) ? searchQuery : '',
              }));
            }}
            className="btn-primary inline-flex items-center gap-2"
          >
            <UserPlus className="w-4 h-4" />
            Create New Customer
          </button>
        </div>
      )}

      {/* Quick Add Button */}
      {searchQuery.length < 3 && (
        <button
          onClick={() => setShowCreateForm(true)}
          className="w-full py-3 border border-dashed border-gray-300 rounded-lg text-gray-500 hover:border-bv-red-300 hover:text-bv-red-600 transition-colors flex items-center justify-center gap-2"
        >
          <UserPlus className="w-5 h-5" />
          Add New Customer
        </button>
      )}
    </div>
  );
}

export default CustomerSearch;
