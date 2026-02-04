// ============================================================================
// IMS 2.0 - Order Details Panel for POS
// ============================================================================
// Captures delivery date, time, notes, and order flags

import { FileText, Calendar, Clock, AlertTriangle, Zap } from 'lucide-react';

interface OrderDetailsData {
  deliveryDate: string;
  deliveryTime: string;
  salesPerson: string;
  notes: string;
  isExpress: boolean;
  isUrgent: boolean;
}

interface OrderDetailsPanelProps {
  orderDetails: OrderDetailsData;
  onChange: (details: OrderDetailsData) => void;
}

export function OrderDetailsPanel({ orderDetails, onChange }: OrderDetailsPanelProps) {
  const handleChange = (field: keyof OrderDetailsData, value: string | boolean) => {
    onChange({
      ...orderDetails,
      [field]: value,
    });
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-4">
        <FileText className="w-4 h-4 text-bv-red-600" />
        <h3 className="font-medium text-gray-900">Order Details</h3>
      </div>

      <div className="space-y-4">
        {/* Delivery Date & Time */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              <Calendar className="w-3 h-3 inline mr-1" />
              Delivery Date
            </label>
            <input
              type="date"
              value={orderDetails.deliveryDate}
              onChange={(e) => handleChange('deliveryDate', e.target.value)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              <Clock className="w-3 h-3 inline mr-1" />
              Delivery Time
            </label>
            <select
              value={orderDetails.deliveryTime}
              onChange={(e) => handleChange('deliveryTime', e.target.value)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
            >
              <option value="">Select time</option>
              <option value="10:00">10:00 AM</option>
              <option value="11:00">11:00 AM</option>
              <option value="12:00">12:00 PM</option>
              <option value="13:00">1:00 PM</option>
              <option value="14:00">2:00 PM</option>
              <option value="15:00">3:00 PM</option>
              <option value="16:00">4:00 PM</option>
              <option value="17:00">5:00 PM</option>
              <option value="18:00">6:00 PM</option>
              <option value="19:00">7:00 PM</option>
              <option value="20:00">8:00 PM</option>
            </select>
          </div>
        </div>

        {/* Priority Flags */}
        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => handleChange('isExpress', !orderDetails.isExpress)}
            className={`flex-1 px-3 py-2 rounded-lg border flex items-center justify-center gap-2 text-sm transition-colors ${
              orderDetails.isExpress
                ? 'bg-amber-100 border-amber-300 text-amber-700'
                : 'bg-gray-50 border-gray-200 text-gray-600 hover:bg-gray-100'
            }`}
          >
            <Zap className="w-4 h-4" />
            Express
          </button>
          <button
            type="button"
            onClick={() => handleChange('isUrgent', !orderDetails.isUrgent)}
            className={`flex-1 px-3 py-2 rounded-lg border flex items-center justify-center gap-2 text-sm transition-colors ${
              orderDetails.isUrgent
                ? 'bg-red-100 border-red-300 text-red-700'
                : 'bg-gray-50 border-gray-200 text-gray-600 hover:bg-gray-100'
            }`}
          >
            <AlertTriangle className="w-4 h-4" />
            Urgent
          </button>
        </div>

        {/* Notes */}
        <div>
          <label className="text-xs text-gray-500 block mb-1">Notes</label>
          <textarea
            value={orderDetails.notes}
            onChange={(e) => handleChange('notes', e.target.value)}
            placeholder="Special instructions, customer requests..."
            rows={2}
            className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none resize-none"
          />
        </div>
      </div>
    </div>
  );
}

export default OrderDetailsPanel;
