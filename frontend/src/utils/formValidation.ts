// ============================================================================
// IMS 2.0 - Form Validation Utilities
// ============================================================================

export interface ValidationError {
  field: string;
  message: string;
}

export function validateEmail(email: string): string | null {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!email) return 'Email is required';
  if (!emailRegex.test(email)) return 'Please enter a valid email address';
  return null;
}

export function validatePhoneNumber(phone: string): string | null {
  const phoneRegex = /^[0-9]{10}$/;
  if (!phone) return 'Phone number is required';
  if (!phoneRegex.test(phone.replace(/\D/g, ''))) {
    return 'Please enter a valid 10-digit phone number';
  }
  return null;
}

export function validateGSTNumber(gst: string): string | null {
  const gstRegex = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
  if (!gst) return 'GST number is required';
  if (!gstRegex.test(gst.toUpperCase())) {
    return 'Please enter a valid 15-digit GST number';
  }
  return null;
}

export function validatePAN(pan: string): string | null {
  const panRegex = /^[A-Z]{5}[0-9]{4}[A-Z]{1}$/;
  if (!pan) return 'PAN is required';
  if (!panRegex.test(pan.toUpperCase())) {
    return 'Please enter a valid 10-character PAN';
  }
  return null;
}

export function validatePincode(pincode: string): string | null {
  const pincodeRegex = /^[0-9]{6}$/;
  if (!pincode) return 'Pincode is required';
  if (!pincodeRegex.test(pincode)) {
    return 'Please enter a valid 6-digit pincode';
  }
  return null;
}

export function validatePassword(password: string): string | null {
  if (!password) return 'Password is required';
  if (password.length < 8) return 'Password must be at least 8 characters';
  if (!/[A-Z]/.test(password)) return 'Password must contain at least one uppercase letter';
  if (!/[a-z]/.test(password)) return 'Password must contain at least one lowercase letter';
  if (!/[0-9]/.test(password)) return 'Password must contain at least one digit';
  return null;
}

export function validateUsername(username: string): string | null {
  if (!username) return 'Username is required';
  if (username.length < 3) return 'Username must be at least 3 characters';
  if (!/^[a-zA-Z0-9._-]+$/.test(username)) {
    return 'Username can only contain letters, numbers, dots, hyphens, and underscores';
  }
  return null;
}

export function validateRequiredField(value: string, fieldName: string): string | null {
  if (!value || !value.trim()) return `${fieldName} is required`;
  return null;
}

export function validateMinLength(value: string, minLength: number, fieldName: string): string | null {
  if (value && value.length < minLength) {
    return `${fieldName} must be at least ${minLength} characters`;
  }
  return null;
}

export function validateMaxLength(value: string, maxLength: number, fieldName: string): string | null {
  if (value && value.length > maxLength) {
    return `${fieldName} must not exceed ${maxLength} characters`;
  }
  return null;
}
