// ============================================================================
// IMS 2.0 - Customer API
// ============================================================================

import api from './client';

export const customerApi = {
  getCustomers: async (params?: { search?: string; page?: number; pageSize?: number; storeId?: string; limit?: number }) => {
    const response = await api.get('/customers', { params });
    return response.data;
  },

  getCustomer: async (customerId: string) => {
    const response = await api.get(`/customers/${customerId}`);
    return response.data;
  },

  createCustomer: async (data: Partial<import('../../types').Customer>) => {
    const response = await api.post('/customers', data);
    return response.data;
  },

  updateCustomer: async (customerId: string, data: Partial<import('../../types').Customer>) => {
    const response = await api.put(`/customers/${customerId}`, data);
    return response.data;
  },

  searchByPhone: async (phone: string) => {
    const response = await api.get('/customers/search/phone', { params: { phone } });
    return response.data;
  },

  addPatient: async (customerId: string, patient: Partial<import('../../types').Patient>) => {
    const response = await api.post(`/customers/${customerId}/patients`, patient);
    return response.data;
  },
};
