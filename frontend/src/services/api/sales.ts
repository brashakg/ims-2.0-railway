// ============================================================================
// IMS 2.0 - Sales / Billing / Orders API
// ============================================================================

import api from './client';

// ============================================================================
// Order API
// ============================================================================

export const orderApi = {
  getOrders: async (params?: { storeId?: string; status?: string; date?: string; from_date?: string; to_date?: string; customerId?: string; limit?: number; skip?: number }) => {
    // Convert camelCase storeId/customerId → snake_case for the FastAPI Query
    // params. Without this the backend silently ignored ?storeId and fell
    // back to the user's token store, so the topbar store-switch never
    // changed the orders list (always showed the admin's home store).
    const { storeId, customerId, ...rest } = params ?? {};
    const apiParams = {
      ...rest,
      ...(storeId ? { store_id: storeId } : {}),
      ...(customerId ? { customer_id: customerId } : {}),
    };
    const response = await api.get('/orders', { params: apiParams });
    return response.data;
  },

  getOrder: async (orderId: string) => {
    const response = await api.get(`/orders/${orderId}`);
    return response.data;
  },

  // C-5 (DELTA 3): optional `idempotencyKey` -> sent as the `Idempotency-Key`
  // request header so a double-clicked / retried "Pay now" reuses the key and
  // the backend returns the SAME order instead of creating a duplicate.
  createOrder: async (
    data: Partial<import('../../types').Order>,
    idempotencyKey?: string,
  ) => {
    const config = idempotencyKey
      ? { headers: { 'Idempotency-Key': idempotencyKey } }
      : undefined;
    const response = await api.post('/orders', data, config);
    return response.data;
  },

  addOrderItem: async (orderId: string, item: Partial<import('../../types').OrderItem>) => {
    const response = await api.post(`/orders/${orderId}/items`, item);
    return response.data;
  },

  removeOrderItem: async (orderId: string, itemId: string) => {
    const response = await api.delete(`/orders/${orderId}/items/${itemId}`);
    return response.data;
  },

  addPayment: async (orderId: string, payment: Partial<import('../../types').Payment>) => {
    const response = await api.post(`/orders/${orderId}/payments`, payment);
    return response.data;
  },

  confirmOrder: async (orderId: string) => {
    const response = await api.post(`/orders/${orderId}/confirm`);
    return response.data;
  },

  deliverOrder: async (orderId: string) => {
    const response = await api.post(`/orders/${orderId}/deliver`);
    return response.data;
  },

  // POS-11: the backend cancel endpoint reads `reason` as a query param
  // (reason: str = Query(..., min_length=10)), not from the request body.
  // Sending { reason } as a JSON body was silently ignored and the endpoint
  // failed with "field required" for the query param. Pass it correctly.
  cancelOrder: async (orderId: string, reason: string) => {
    const response = await api.post(`/orders/${orderId}/cancel`, null, {
      params: { reason },
    });
    return response.data;
  },
};

// ============================================================================
// Prescription API
// ============================================================================

// Map backend snake_case prescription doc → frontend Prescription shape.
// Audit Run #4 found Customers prescription cards rendering as
// `QR: undefined`, `Invalid Date`, `by Unknown`, all fields blank — the
// frontend expected `testDate / rightEye.sphere` while the backend ships
// `test_date / right_eye.sph`. Mapper kept beside the API call so every
// caller gets the canonical shape.
function mapEye(eye: any): any {
  if (!eye || typeof eye !== 'object') return undefined;
  return {
    sphere: eye.sphere ?? eye.sph,
    cylinder: eye.cylinder ?? eye.cyl,
    axis: eye.axis,
    pd: eye.pd,
    add: eye.add ?? eye.add_power,
  };
}

export function mapRx(rx: any): any {
  if (!rx || typeof rx !== 'object') return rx;
  return {
    ...rx,
    id: rx.id ?? rx.prescription_id ?? rx._id,
    testDate: rx.testDate ?? rx.test_date ?? rx.created_at,
    expiryDate: rx.expiryDate ?? rx.expiry_date,
    optometristId: rx.optometristId ?? rx.optometrist_id,
    optometristName: rx.optometristName ?? rx.optometrist_name ?? rx.doctor_name,
    customerId: rx.customerId ?? rx.customer_id,
    patientId: rx.patientId ?? rx.patient_id,
    storeId: rx.storeId ?? rx.store_id,
    rightEye: mapEye(rx.rightEye ?? rx.right_eye),
    leftEye: mapEye(rx.leftEye ?? rx.left_eye),
    notes: rx.notes,
  };
}

// --- Prescription CREATE normalisation -------------------------------------
// The POS PrescriptionForm emits FLAT keys (sph_od/cyl_od/axis_od/add_od/pd_od
// and the _os left-eye equivalents). The backend PrescriptionCreate requires
// NESTED right_eye / left_eye EyeData objects, with sph/cyl/add/pd as strings
// and axis as an int. Convert here so callers can forward the form data as-is.
function _rxStr(v: unknown): string | undefined {
  if (v === undefined || v === null || v === '') return undefined;
  return String(v);
}
function _rxAxis(v: unknown): number | undefined {
  if (v === undefined || v === null || v === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? Math.round(n) : undefined;
}
function _buildEye(d: any, suffix: 'od' | 'os') {
  return {
    sph: _rxStr(d[`sph_${suffix}`]),
    cyl: _rxStr(d[`cyl_${suffix}`]),
    axis: _rxAxis(d[`axis_${suffix}`]),
    add: _rxStr(d[`add_${suffix}`]),
    pd: _rxStr(d[`pd_${suffix}`]),
    // Parity with the clinical Final-Rx: visual acuity, prism, base.
    prism: _rxStr(d[`prism_${suffix}`]),
    base: _rxStr(d[`base_${suffix}`]),
    acuity: _rxStr(d[`va_${suffix}`]),
  };
}
function toPrescriptionCreatePayload(data: any): any {
  if (!data || typeof data !== 'object') return data;
  // Already nested (e.g. a caller that built right_eye/left_eye directly).
  if (data.right_eye || data.rightEye) return data;
  const hasFlat = ['sph_od', 'cyl_od', 'axis_od', 'sph_os', 'cyl_os', 'axis_os'].some(
    (k) => k in data
  );
  if (!hasFlat) return data;
  const {
    sph_od: _sph_od, cyl_od: _cyl_od, axis_od: _axis_od, add_od: _add_od, pd_od: _pd_od,
    sph_os: _sph_os, cyl_os: _cyl_os, axis_os: _axis_os, add_os: _add_os, pd_os: _pd_os,
    // New spectacle parity flat keys (folded into right_eye/left_eye below).
    prism_od: _prism_od, base_od: _base_od, va_od: _va_od,
    prism_os: _prism_os, base_os: _base_os, va_os: _va_os,
    issue_date: _issue_date, expiry_date: _expiry_date, doctor_name: _doctor_name,
    lens_type: _lens_type,
    ...rest
  } = data;
  return {
    // rest carries patient_id, customer_id, store_id, source, optometrist_id,
    // validity_months, ipd, next_checkup, etc.
    ...rest,
    right_eye: _buildEye(data, 'od'),
    left_eye: _buildEye(data, 'os'),
    // The form's lens_type IS the backend's lens_recommendation; don't clobber
    // an explicit lens_recommendation the caller may already have set.
    ...(_lens_type && !('lens_recommendation' in rest)
      ? { lens_recommendation: _lens_type }
      : {}),
  };
}

export const prescriptionApi = {
  // The real Rx library list (store-scoped, role-gated AUTHENTICATED). Powers
  // the Prescriptions page across DATES, replacing the old "map today's
  // eye-tests" hack. Filters: store, inclusive date window (from/to as ISO
  // YYYY-MM-DD), optional customer_id, pagination. Rows are normalised to the
  // camelCase Prescription shape via mapRx. Returns { prescriptions, total }.
  listPrescriptions: async (opts?: {
    storeId?: string;
    customerId?: string;
    from?: string;
    to?: string;
    skip?: number;
    limit?: number;
  }) => {
    const params: Record<string, string | number> = {};
    if (opts?.storeId) params.store_id = opts.storeId;
    if (opts?.customerId) params.customer_id = opts.customerId;
    if (opts?.from) params.from_date = opts.from;
    if (opts?.to) params.to_date = opts.to;
    if (opts?.skip !== undefined) params.skip = opts.skip;
    if (opts?.limit !== undefined) params.limit = opts.limit;
    const response = await api.get('/prescriptions', { params });
    const data = response.data;
    const list = Array.isArray(data?.prescriptions) ? data.prescriptions : [];
    return {
      prescriptions: list.map(mapRx),
      total: typeof data?.total === 'number' ? data.total : list.length,
    };
  },

  getPrescriptions: async (patientOrCustomerId: string) => {
    // Try patient_id first; if empty, fall back to customer_id
    let response = await api.get('/prescriptions', { params: { patient_id: patientOrCustomerId } });
    let data = response.data;
    const rxList = data?.prescriptions || data || [];
    if (Array.isArray(rxList) && rxList.length === 0) {
      // No results by patient_id — try as customer_id
      response = await api.get('/prescriptions', { params: { customer_id: patientOrCustomerId } });
      data = response.data;
    }
    // Normalise to camelCase before handing to the page.
    if (data?.prescriptions && Array.isArray(data.prescriptions)) {
      return { ...data, prescriptions: data.prescriptions.map(mapRx) };
    }
    if (Array.isArray(data)) {
      return data.map(mapRx);
    }
    return data;
  },

  getPrescription: async (prescriptionId: string) => {
    const response = await api.get(`/prescriptions/${prescriptionId}`);
    return mapRx(response.data);
  },

  createPrescription: async (data: any) => {
    // Flat POS-form keys -> nested right_eye/left_eye. Without this the POST
    // 422'd with "field required: right_eye, left_eye", which is why adding a
    // prescription failed wherever the PrescriptionForm was used.
    const response = await api.post('/prescriptions', toPrescriptionCreatePayload(data));
    return response.data;
  },

  // Edit an existing prescription (clinic Edit flow). PUT updates only the
  // mutable Rx fields; identity/provenance (patient_id/customer_id/store_id)
  // is immutable server-side. Accepts the SAME flat PrescriptionForm keys as
  // createPrescription and normalises them to nested right_eye/left_eye.
  updatePrescription: async (prescriptionId: string, data: any) => {
    const payload = toPrescriptionCreatePayload(data);
    // PrescriptionUpdate doesn't take identity fields; strip them so an edit
    // never tries (and silently no-ops) to reassign the Rx.
    const { patient_id: _p, customer_id: _c, store_id: _s, source: _src, rx_kind: _k, ...editable } = payload || {};
    const response = await api.put(`/prescriptions/${prescriptionId}`, editable);
    return response.data;
  },

  validatePrescription: async (prescriptionId: string) => {
    const response = await api.get(`/prescriptions/${prescriptionId}/validate`);
    return response.data;
  },

  // ----- 4-version Rx model (May 2026) -------------------------------
  // Each prescription captures four states: before_testing, after_testing,
  // manual, final. `final` mirrors back to top-level on finalize for
  // backwards compat with the existing POS/Order code.
  getVersions: async (prescriptionId: string) => {
    const response = await api.get(`/prescriptions/${prescriptionId}/versions`);
    return response.data as {
      prescription_id: string;
      status: 'in_progress' | 'finalized';
      versions: {
        before_testing: PrescriptionVersionData | null;
        after_testing: PrescriptionVersionData | null;
        manual: PrescriptionVersionData | null;
        final: PrescriptionVersionData | null;
      };
      finalized_at?: string;
    };
  },

  patchVersion: async (
    prescriptionId: string,
    versionName: 'before_testing' | 'after_testing' | 'manual' | 'final',
    payload: Partial<PrescriptionVersionData>,
  ) => {
    const response = await api.patch(
      `/prescriptions/${prescriptionId}/version/${versionName}`,
      payload,
    );
    return response.data;
  },

  finalizePrescription: async (prescriptionId: string) => {
    const response = await api.post(`/prescriptions/${prescriptionId}/finalize`);
    return response.data;
  },

  getProgression: async (customerId: string) => {
    const response = await api.get(`/prescriptions/customer/${customerId}/progression`);
    return response.data as {
      customer_id: string;
      deltas: Array<{
        from_date: string;
        to_date: string;
        right_eye: { sphere?: number | null; cylinder?: number | null; axis?: number | null; addition?: number | null };
        left_eye: { sphere?: number | null; cylinder?: number | null; axis?: number | null; addition?: number | null };
        pd?: number | null;
      }>;
      visits: number;
    };
  },

  // Family Rx view: a customer account's prescriptions grouped by family member
  // (patient), each row annotated with expiry_date + is_valid. Patients with no
  // Rx are still listed; legacy/imported Rx whose patient_id isn't on the account
  // surface under an "Unlinked patient" group. Backend ships raw snake_case
  // prescription docs (right_eye.sph / left_eye.sph etc.) — the FamilyRxPage
  // tolerates both snake/camel via its own reader, so we pass them through as-is.
  getFamilyRx: async (customerId: string) => {
    const response = await api.get(`/prescriptions/family/${customerId}`);
    return response.data as FamilyRxResponse;
  },

  // Printable Rx card (CL-aware): the backend renders a contact-lens card when
  // the Rx is rx_kind=CONTACT_LENS, else the spectacle card. Returns the HTML
  // string; the caller writes it into a new window (auth token already attached
  // by the api client, unlike a raw window.open of the URL).
  getPrintHtml: async (prescriptionId: string): Promise<string> => {
    const response = await api.get(`/prescriptions/${prescriptionId}/print`);
    const data = response.data;
    return (data?.html as string) ?? '';
  },
};

// ---- Family Rx response shape (GET /prescriptions/family/{customer_id}) ------
export interface FamilyRxPrescription {
  prescription_id?: string;
  patient_id?: string | null;
  patient_name?: string | null;
  test_date?: string | null;
  created_at?: string | null;
  optometrist_name?: string | null;
  doctor_name?: string | null;
  validity_months?: number | null;
  expiry_date: string | null;
  is_valid: boolean | null;
  // Eye blocks arrive snake_case from Mongo; keys vary (sph/sphere, cyl/cylinder,
  // add/addition). Kept loose so the renderer can read whichever is present.
  right_eye?: Record<string, unknown> | null;
  left_eye?: Record<string, unknown> | null;
  pd?: string | number | null;
  [key: string]: unknown;
}

export interface FamilyRxMember {
  patient_id: string | null;
  name: string | null;
  relation: string | null;
  dob: string | null;
  prescription_count: number;
  valid_count: number;
  latest: FamilyRxPrescription | null;
  prescriptions: FamilyRxPrescription[];
}

export interface FamilyRxResponse {
  customer_id: string;
  customer_name?: string | null;
  members: FamilyRxMember[];
  member_count: number;
  total_prescriptions: number;
}

export interface PrescriptionEyeData {
  sphere?: number | null;
  cylinder?: number | null;
  axis?: number | null;
  addition?: number | null;
  va?: string | null;
}

export interface PrescriptionVersionData {
  right_eye?: PrescriptionEyeData | null;
  left_eye?: PrescriptionEyeData | null;
  pd?: number | null;
  source?: string | null;
  override_reason?: string | null;
  signed_off_by?: string | null;
  captured_by?: string | null;
  captured_at?: string | null;
}

// ============================================================================
// Workshop API
// ============================================================================

export const workshopApi = {
  getJobs: async (storeId: string, status?: string) => {
    const response = await api.get('/workshop/jobs', { params: { store_id: storeId, status } });
    return response.data;
  },

  getJob: async (jobId: string) => {
    const response = await api.get(`/workshop/jobs/${jobId}`);
    return response.data;
  },

  updateJobStatus: async (jobId: string, status: string, notes?: string) => {
    const response = await api.patch(`/workshop/jobs/${jobId}/status`, { status, notes });
    return response.data;
  },

  // Lens-order lifecycle (NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED).
  // Forward-only; backend rejects skips/backwards with 400.
  updateLensStatus: async (jobId: string, status: string) => {
    const response = await api.post(`/workshop/jobs/${jobId}/lens-status`, { status });
    return response.data as {
      job_id: string;
      lens_status: string;
      message: string;
    };
  },

  // Notify the customer their job is ready for pickup (WhatsApp + in-app log).
  // Fail-soft on the backend; whatsapp_status is SENT | SIMULATED | FAILED | no_phone.
  notifyReady: async (jobId: string) => {
    const response = await api.post(`/workshop/jobs/${jobId}/notify-ready`);
    return response.data as {
      job_id: string;
      ready_notified_at: string;
      whatsapp_status: string;
      notification_logged: boolean;
      message: string;
    };
  },

  assignJob: async (jobId: string, staffId: string) => {
    const response = await api.post(`/workshop/jobs/${jobId}/assign`, { staff_id: staffId });
    return response.data;
  },

  // Vendor / lens-lab capture (May 2026 — vendor portal feature).
  // PATCH a job with the external lab's reference ID + tracking URL.
  patchJobVendor: async (
    jobId: string,
    payload: { vendor_id?: string | null; vendor_order_id?: string | null; vendor_tracking_url?: string | null },
  ) => {
    const response = await api.patch(`/workshop/jobs/${jobId}/vendor`, payload);
    return response.data;
  },

  // POST a vendor status update from the IMS-side admin (mirrors what
  // the lab can do via the public portal). Source stamped 'ims_user'.
  postJobVendorStatus: async (
    jobId: string,
    payload: { status: string; note?: string },
  ) => {
    const response = await api.post(`/workshop/jobs/${jobId}/vendor-status`, payload);
    return response.data;
  },

  createJob: async (data: {
    order_id: string;
    frame_details: Record<string, any>;
    lens_details: Record<string, any>;
    prescription_id: string;
    fitting_instructions?: string;
    special_notes?: string;
    expected_date: string;
  }) => {
    const response = await api.post('/workshop/jobs', data);
    return response.data;
  },

  // Phase 6.4 — single-call workshop KPIs for the dashboard header.
  // Replaces 4 client-side list calls + local counting with one small payload.
  getDashboardKpis: async (storeId?: string) => {
    const response = await api.get('/workshop/dashboard-kpis', {
      params: storeId ? { store_id: storeId } : {},
    });
    return response.data as {
      pending: number;
      in_progress: number;
      qc_failed: number;
      ready_for_pickup: number;
      overdue: number;
      completed_today: number;
      delivered_today: number;
      avg_turnaround_days: number | null;
      store_id: string | null;
      as_of: string;
    };
  },

  // Phase 6.8 — attach / update lens fitting details on a workshop job.
  // Called from the POS LensFittingFormModal right after an Rx order is
  // created, so the workshop tech sees the full fitting measurements
  // + sales confirmation when they pick up the job.
  updateFittingDetails: async (
    jobId: string,
    fittingDetails: {
      dia?: string;
      fh?: string;
      b_size?: string;
      dbl?: string;
      tint?: string;
      base_curve?: string;
      coating?: string;
      other?: string;
      vendor_order_id?: string;
      order_date?: string;
      order_time?: string;
      ordered_by?: string;
      ordered_by_name?: string;
      expected_lens_receive_date?: string;
      confirmed_by_sales: boolean;
      confirmed_at?: string;
    },
  ) => {
    const response = await api.patch(`/workshop/jobs/${jobId}/fitting-details`, {
      fitting_details: fittingDetails,
    });
    return response.data;
  },

  // QC a COMPLETED (or re-QC a QC_FAILED) job. passed=true -> READY,
  // passed=false -> QC_FAILED. The notes carry the checklist outcome.
  // Backend reads `passed` + `notes` as query params (POST, no body).
  qcJob: async (jobId: string, passed: boolean, notes?: string) => {
    const response = await api.post(`/workshop/jobs/${jobId}/qc`, null, {
      params: { passed, ...(notes ? { notes } : {}) },
    });
    return response.data as {
      job_id: string;
      status: 'READY' | 'QC_FAILED';
      qc_passed: boolean;
      message: string;
    };
  },

  // Phase 6.9 — structured per-item QC checklist. Sends each check item
  // (key, label, passed, optional note) to the dedicated /qc-checklist
  // endpoint which stores them with reviewer identity + timestamps.
  qcChecklist: async (
    jobId: string,
    checklist: Array<{ key: string; label: string; passed: boolean; note?: string }>,
    overallNotes?: string,
    waived?: boolean,
    waiveReason?: string,
  ) => {
    const response = await api.post(`/workshop/jobs/${jobId}/qc-checklist`, {
      checklist,
      overall_notes: overallNotes,
      waived: waived ?? false,
      waive_reason: waiveReason,
    });
    return response.data as {
      job_id: string;
      status: 'READY' | 'QC_FAILED';
      qc_passed: boolean;
      all_items_passed: boolean;
      waived: boolean;
      checklist: Array<{
        key: string;
        label: string;
        passed: boolean;
        note: string;
        checked_by: string;
        checked_at: string;
      }>;
      message: string;
    };
  },

  // Send a QC_FAILED job back to the bench (QC_FAILED -> IN_PROGRESS).
  // Backend reads `notes` as a query param.
  reworkJob: async (jobId: string, notes?: string) => {
    const response = await api.post(`/workshop/jobs/${jobId}/rework`, null, {
      params: notes ? { notes } : {},
    });
    return response.data as {
      job_id: string;
      status: 'IN_PROGRESS';
      rework_count: number;
      message: string;
    };
  },

  // Phase 6.4 — pending jobs report with aging buckets + per-tech breakdown.
  getPendingJobsReport: async (storeId?: string) => {
    const response = await api.get('/reports/workshop/pending-jobs', {
      params: storeId ? { store_id: storeId } : {},
    });
    return response.data as {
      data: Array<{
        job_id: string;
        job_number: string | null;
        order_id: string | null;
        status: string;
        technician_id: string | null;
        expected_date: string | null;
        created_at: string | null;
        age_days: number | null;
        aging_bucket: '0-3d' | '3-7d' | '7+d';
        is_overdue: boolean;
      }>;
      summary: {
        total_pending: number;
        overdue: number;
        by_aging_bucket: Record<'0-3d' | '3-7d' | '7+d', number>;
        by_technician: Array<{ technician_id: string; count: number }>;
      };
    };
  },
};

// ============================================================================
// Admin API - Discount Rules
// ============================================================================

export const adminDiscountApi = {
  getDiscountRules: async () => {
    const response = await api.get('/admin/discounts/rules');
    return response.data;
  },

  getRoleDiscountCaps: async () => {
    const response = await api.get('/admin/discounts/role-caps');
    return response.data;
  },

  setRoleDiscountCap: async (role: string, maxDiscount: number) => {
    const response = await api.post('/admin/discounts/role-caps', { role, max_discount: maxDiscount });
    return response.data;
  },

  // The caps the POS ACTUALLY enforces, sourced from code constants
  // (role_caps.py + pricing_caps.py). Read-only -- there is no setter, because
  // changing a cap is a code change + deploy, not a DB write.
  getEnforcedDiscountCaps: async () => {
    const response = await api.get('/admin/discounts/enforced-caps');
    return response.data as {
      source: string;
      note: string;
      role_caps: Record<string, number>;
      category_caps: Record<string, number>;
      luxury_brand_caps: Record<string, number>;
    };
  },

  getTierDiscounts: async () => {
    const response = await api.get('/admin/discounts/tier-discounts');
    return response.data;
  },

  setTierDiscount: async (tier: string, discount: number) => {
    const response = await api.post('/admin/discounts/tier-discounts', { tier, discount });
    return response.data;
  },

  createPromoCode: async (data: {
    code: string;
    discountType: 'PERCENTAGE' | 'FIXED';
    discountValue: number;
    minPurchase?: number;
    maxDiscount?: number;
    validFrom: string;
    validTo: string;
    usageLimit?: number;
    categories?: string[];
  }) => {
    const response = await api.post('/admin/discounts/promo-codes', data);
    return response.data;
  },

  getPromoCodes: async (params?: { active?: boolean }) => {
    const response = await api.get('/admin/discounts/promo-codes', { params });
    return response.data;
  },

  deletePromoCode: async (codeId: string) => {
    const response = await api.delete(`/admin/discounts/promo-codes/${codeId}`);
    return response.data;
  },
};
