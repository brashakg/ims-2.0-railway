// ============================================================================
// IMS 2.0 - Notification Constants and Templates
// ============================================================================
// SMS/WhatsApp notification templates for optical retail

export type NotificationProvider = 'MSG91' | 'TWILIO' | 'GUPSHUP';
export type NotificationChannel = 'SMS' | 'WHATSAPP' | 'BOTH';
export type NotificationCategory =
  | 'TRANSACTIONAL'  // Order confirmations, payment receipts
  | 'SERVICE'        // Service updates, appointment reminders
  | 'PROMOTIONAL'    // Marketing messages
  | 'REMINDER'       // Follow-up reminders, expiry alerts
  | 'GREETING';      // Birthday, anniversary wishes

export interface NotificationTemplate {
  id: string;
  name: string;
  category: NotificationCategory;
  channel: NotificationChannel;
  subject?: string; // For WhatsApp
  template: string;
  variables: string[]; // Available variables like {customerName}, {orderNumber}
  dltTemplateId?: string; // DLT Template ID for TRAI compliance
  isActive: boolean;
}

// Notification templates for optical retail
export const NOTIFICATION_TEMPLATES: Record<string, NotificationTemplate> = {
  // Order Related
  ORDER_CONFIRMED: {
    id: 'ORDER_CONFIRMED',
    name: 'Order Confirmation',
    category: 'TRANSACTIONAL',
    channel: 'BOTH',
    subject: 'Order Confirmed',
    template: 'Dear {customerName}, your order #{orderNumber} for ₹{amount} has been confirmed. Expected delivery: {deliveryDate}. Track your order at {trackingLink}. - {storeName}',
    variables: ['customerName', 'orderNumber', 'amount', 'deliveryDate', 'trackingLink', 'storeName'],
    isActive: true,
  },
  ORDER_READY: {
    id: 'ORDER_READY',
    name: 'Order Ready for Pickup',
    category: 'TRANSACTIONAL',
    channel: 'BOTH',
    subject: 'Your Order is Ready!',
    template: 'Dear {customerName}, great news! Your order #{orderNumber} is ready for pickup at {storeName}. Please collect it at your earliest convenience. - {storeAddress}',
    variables: ['customerName', 'orderNumber', 'storeName', 'storeAddress'],
    isActive: true,
  },
  ORDER_DELIVERED: {
    id: 'ORDER_DELIVERED',
    name: 'Order Delivered',
    category: 'TRANSACTIONAL',
    channel: 'BOTH',
    subject: 'Order Delivered',
    template: 'Dear {customerName}, your order #{orderNumber} has been delivered. Thank you for choosing {storeName}. We hope you enjoy your new eyewear!',
    variables: ['customerName', 'orderNumber', 'storeName'],
    isActive: true,
  },
  PAYMENT_RECEIVED: {
    id: 'PAYMENT_RECEIVED',
    name: 'Payment Receipt',
    category: 'TRANSACTIONAL',
    channel: 'SMS',
    template: 'Payment of ₹{amount} received for order #{orderNumber}. Balance: ₹{balance}. Thank you! - {storeName}',
    variables: ['amount', 'orderNumber', 'balance', 'storeName'],
    isActive: true,
  },

  // Appointment Related
  APPOINTMENT_CONFIRMED: {
    id: 'APPOINTMENT_CONFIRMED',
    name: 'Appointment Confirmation',
    category: 'SERVICE',
    channel: 'BOTH',
    subject: 'Appointment Confirmed',
    template: 'Dear {customerName}, your eye test appointment is confirmed for {appointmentDate} at {appointmentTime} with {optometristName}. See you soon! - {storeName}',
    variables: ['customerName', 'appointmentDate', 'appointmentTime', 'optometristName', 'storeName'],
    isActive: true,
  },
  APPOINTMENT_REMINDER: {
    id: 'APPOINTMENT_REMINDER',
    name: 'Appointment Reminder (1 day before)',
    category: 'REMINDER',
    channel: 'BOTH',
    subject: 'Appointment Reminder',
    template: 'Dear {customerName}, this is a reminder for your eye test appointment tomorrow at {appointmentTime} at {storeName}. Call {storePhone} to reschedule.',
    variables: ['customerName', 'appointmentTime', 'storeName', 'storePhone'],
    isActive: true,
  },

  // Contact Lens Related
  LENS_EXPIRY_REMINDER: {
    id: 'LENS_EXPIRY_REMINDER',
    name: 'Contact Lens Expiry Reminder',
    category: 'REMINDER',
    channel: 'BOTH',
    subject: 'Time to Replace Your Contact Lenses',
    template: 'Dear {customerName}, your contact lenses purchased on {purchaseDate} are due for replacement. Visit {storeName} or order online at {orderLink}',
    variables: ['customerName', 'purchaseDate', 'storeName', 'orderLink'],
    isActive: true,
  },
  LENS_STOCK_AVAILABLE: {
    id: 'LENS_STOCK_AVAILABLE',
    name: 'Contact Lens Back in Stock',
    category: 'SERVICE',
    channel: 'WHATSAPP',
    subject: 'Your Favorite Lenses Are Back!',
    template: 'Dear {customerName}, the contact lenses you were looking for ({lensName}) are now back in stock at {storeName}. Order now!',
    variables: ['customerName', 'lensName', 'storeName'],
    isActive: true,
  },

  // Prescription Related
  PRESCRIPTION_EXPIRY: {
    id: 'PRESCRIPTION_EXPIRY',
    name: 'Prescription Expiry Alert',
    category: 'REMINDER',
    channel: 'BOTH',
    subject: 'Time for Your Eye Checkup',
    template: 'Dear {customerName}, your eye prescription from {prescriptionDate} is due for renewal. Book your eye test at {storeName}. Call {storePhone}',
    variables: ['customerName', 'prescriptionDate', 'storeName', 'storePhone'],
    isActive: true,
  },

  // Checkup Reminders
  ANNUAL_CHECKUP_REMINDER: {
    id: 'ANNUAL_CHECKUP_REMINDER',
    name: 'Annual Eye Checkup Reminder',
    category: 'REMINDER',
    channel: 'BOTH',
    subject: 'Annual Eye Checkup Due',
    template: 'Dear {customerName}, it\'s been a year since your last eye test on {lastTestDate}. Regular checkups are important for eye health. Book your appointment at {storeName} - {storePhone}',
    variables: ['customerName', 'lastTestDate', 'storeName', 'storePhone'],
    isActive: true,
  },

  // Greetings
  BIRTHDAY_WISH: {
    id: 'BIRTHDAY_WISH',
    name: 'Birthday Wishes',
    category: 'GREETING',
    channel: 'WHATSAPP',
    subject: 'Happy Birthday!',
    template: 'Happy Birthday {customerName}! 🎉 Wishing you a wonderful year ahead. Enjoy {discountPercent}% off on your next purchase at {storeName}. Valid till {validityDate}',
    variables: ['customerName', 'discountPercent', 'storeName', 'validityDate'],
    isActive: true,
  },
  ANNIVERSARY_WISH: {
    id: 'ANNIVERSARY_WISH',
    name: 'Anniversary Wishes',
    category: 'GREETING',
    channel: 'WHATSAPP',
    subject: 'Happy Anniversary!',
    template: 'Happy Anniversary {customerName}! 💐 May you have many more wonderful years together. Get {discountPercent}% off at {storeName}. Valid till {validityDate}',
    variables: ['customerName', 'discountPercent', 'storeName', 'validityDate'],
    isActive: true,
  },

  // Promotional
  PROMOTIONAL_OFFER: {
    id: 'PROMOTIONAL_OFFER',
    name: 'Promotional Offer',
    category: 'PROMOTIONAL',
    channel: 'BOTH',
    subject: 'Special Offer Just for You!',
    template: '{offerTitle}: Get {discountPercent}% off on {productCategory} at {storeName}. Valid till {validityDate}. Visit us or call {storePhone}',
    variables: ['offerTitle', 'discountPercent', 'productCategory', 'storeName', 'validityDate', 'storePhone'],
    isActive: false, // Promotional disabled by default, enable based on DND preferences
  },
  NEW_COLLECTION: {
    id: 'NEW_COLLECTION',
    name: 'New Collection Launch',
    category: 'PROMOTIONAL',
    channel: 'WHATSAPP',
    subject: 'New Collection Alert!',
    template: 'Dear {customerName}, our latest collection from {brandName} is now available at {storeName}! Visit us to explore the newest designs in eyewear.',
    variables: ['customerName', 'brandName', 'storeName'],
    isActive: false,
  },

  // Workshop Updates
  JOB_READY: {
    id: 'JOB_READY',
    name: 'Spectacle/Lens Job Ready',
    category: 'SERVICE',
    channel: 'BOTH',
    subject: 'Your Spectacles Are Ready!',
    template: 'Dear {customerName}, your spectacles (Job #{jobNumber}) are ready for delivery. Please collect them from {storeName}. - {storeAddress}',
    variables: ['customerName', 'jobNumber', 'storeName', 'storeAddress'],
    isActive: true,
  },
  JOB_DELAYED: {
    id: 'JOB_DELAYED',
    name: 'Job Delay Notification',
    category: 'SERVICE',
    channel: 'SMS',
    template: 'Dear {customerName}, your order #{jobNumber} is delayed. New expected date: {newDate}. We apologize for the inconvenience. - {storeName}',
    variables: ['customerName', 'jobNumber', 'newDate', 'storeName'],
    isActive: true,
  },
};

// Provider configurations
export interface NotificationProviderConfig {
  provider: NotificationProvider;
  apiKey?: string;
  apiSecret?: string;
  senderId?: string;
  webhookUrl?: string;
  isActive: boolean;
}

// DND (Do Not Disturb) preferences
export interface DNDPreferences {
  customerId: string;
  allowTransactional: boolean;
  allowService: boolean;
  allowPromotional: boolean;
  allowReminders: boolean;
  allowGreetings: boolean;
  preferredChannel: NotificationChannel;
  quietHoursStart?: string; // HH:MM format
  quietHoursEnd?: string;   // HH:MM format
}

// Notification log entry
export interface NotificationLog {
  id: string;
  templateId: string;
  customerId: string;
  customerPhone: string;
  channel: 'SMS' | 'WHATSAPP';
  message: string;
  status: 'SENT' | 'DELIVERED' | 'FAILED' | 'PENDING';
  sentAt: string;
  deliveredAt?: string;
  failureReason?: string;
  cost?: number;
  provider: NotificationProvider;
}

// Utility function to replace variables in template
export function populateTemplate(template: string, variables: Record<string, string>): string {
  let message = template;
  Object.entries(variables).forEach(([key, value]) => {
    message = message.replace(new RegExp(`\\{${key}\\}`, 'g'), value);
  });
  return message;
}

// Check if notification should be sent based on DND preferences
export function shouldSendNotification(
  templateCategory: NotificationCategory,
  dndPreferences: DNDPreferences,
  currentTime?: Date
): boolean {
  // Check category preferences
  switch (templateCategory) {
    case 'TRANSACTIONAL':
      if (!dndPreferences.allowTransactional) return false;
      break;
    case 'SERVICE':
      if (!dndPreferences.allowService) return false;
      break;
    case 'PROMOTIONAL':
      if (!dndPreferences.allowPromotional) return false;
      break;
    case 'REMINDER':
      if (!dndPreferences.allowReminders) return false;
      break;
    case 'GREETING':
      if (!dndPreferences.allowGreetings) return false;
      break;
  }

  // Check quiet hours (except for critical transactional messages)
  if (templateCategory !== 'TRANSACTIONAL' && dndPreferences.quietHoursStart && dndPreferences.quietHoursEnd) {
    const now = currentTime || new Date();
    const currentHour = now.getHours();
    const currentMinute = now.getMinutes();
    const currentTimeInMinutes = currentHour * 60 + currentMinute;

    const [startHour, startMinute] = dndPreferences.quietHoursStart.split(':').map(Number);
    const [endHour, endMinute] = dndPreferences.quietHoursEnd.split(':').map(Number);
    const startTimeInMinutes = startHour * 60 + startMinute;
    const endTimeInMinutes = endHour * 60 + endMinute;

    if (currentTimeInMinutes >= startTimeInMinutes && currentTimeInMinutes <= endTimeInMinutes) {
      return false;
    }
  }

  return true;
}

// ----------------------------------------------------------------------------
// Template picker options (audit L1-F11)
// ----------------------------------------------------------------------------
// ONE answer to "which message templates can this screen offer?", built from
// the server's effective catalog (GET /settings/notifications/templates ->
// `catalog`: its built-in templates plus every saved one, each marked
// sendable or not). The Reminders picker used to list only SAVED rows, so on
// a fresh database it offered nothing and no reminder could be created until
// a template was toggled on the Notifications screen.
//
// A template is SELECTABLE only when the server can build its message. The
// rest stay listed, greyed out, with the reason: choosing one would queue the
// literal text "[Template X not found]" to a customer.
//
// A BUILT-IN that is switched off on the Notifications screen stays
// selectable, with a note: the server still sends its built-in wording (the
// switch only stops the saved wording being used). The picker says what the
// sender does; whether "off" should stop sends is not decided here.

export interface TemplatePickerOption {
  id: string;
  label: string;
  selectable: boolean;
}

interface CatalogRow {
  template_id?: string;
  is_enabled?: boolean;
  sendable?: boolean;
}

export function templatePickerOptions(resp: unknown): TemplatePickerOption[] {
  const body = (resp ?? {}) as { catalog?: CatalogRow[]; templates?: CatalogRow[] };
  // An older server sends no `catalog`: fall back to its saved rows, which is
  // what this picker offered before.
  const rows: CatalogRow[] = Array.isArray(body.catalog)
    ? body.catalog
    : Array.isArray(body.templates)
      ? body.templates.map((t) => ({ ...t, sendable: true }))
      : [];
  const nameOf = (id: string) => NOTIFICATION_TEMPLATES[id]?.name ?? id;
  const seen = new Set<string>();
  const out: TemplatePickerOption[] = [];
  for (const r of rows) {
    const id = r.template_id;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const selectable = r.sendable !== false;
    const off = r.is_enabled === false;
    const note = selectable
      ? off
        ? ' (off in Notifications: built-in wording is used)'
        : ''
      : off
        ? ' (turned off in Notifications)'
        : ' (no message text saved yet)';
    out.push({ id, label: nameOf(id) + note, selectable });
  }
  // Templates the Notifications screen lists but the server holds no wording
  // for: shown so they do not silently vanish, not selectable until saved.
  for (const t of Object.values(NOTIFICATION_TEMPLATES)) {
    if (seen.has(t.id)) continue;
    out.push({ id: t.id, label: `${t.name} (no message text saved yet)`, selectable: false });
  }
  return out;
}

// Get template by ID
export function getTemplate(templateId: string): NotificationTemplate | null {
  return NOTIFICATION_TEMPLATES[templateId] || null;
}

// Get all templates by category
export function getTemplatesByCategory(category: NotificationCategory): NotificationTemplate[] {
  return Object.values(NOTIFICATION_TEMPLATES).filter(t => t.category === category);
}

export default {
  NOTIFICATION_TEMPLATES,
  populateTemplate,
  shouldSendNotification,
  getTemplate,
  getTemplatesByCategory,
};
