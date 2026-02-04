"""
IMS 2.0 - MongoDB Schemas & Indexes
====================================
Collection schemas, validators, and index definitions
"""
from typing import Dict, List, Any

# ============================================================================
# COLLECTION SCHEMAS (MongoDB JSON Schema Validators)
# ============================================================================

USER_SCHEMA = {
    "bsonType": "object",
    "required": ["user_id", "username", "email", "roles", "store_ids", "is_active", "created_at"],
    "properties": {
        "user_id": {"bsonType": "string"},
        "username": {"bsonType": "string", "minLength": 3, "maxLength": 50},
        "email": {"bsonType": "string"},
        "password_hash": {"bsonType": "string"},
        "full_name": {"bsonType": "string"},
        "phone": {"bsonType": "string"},
        "roles": {
            "bsonType": "array",
            "items": {
                "enum": ["SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", 
                        "STORE_MANAGER", "AREA_MANAGER", "CATALOG_MANAGER", 
                        "ACCOUNTANT", "ADMIN", "SUPERADMIN"]
            }
        },
        "store_ids": {"bsonType": "array", "items": {"bsonType": "string"}},
        "primary_store_id": {"bsonType": "string"},
        "discount_cap": {"bsonType": "double", "minimum": 0, "maximum": 100},
        "is_active": {"bsonType": "bool"},
        "geo_restricted": {"bsonType": "bool"},
        "allowed_coordinates": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "lat": {"bsonType": "double"},
                    "lng": {"bsonType": "double"},
                    "radius_meters": {"bsonType": "int"}
                }
            }
        },
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "last_login": {"bsonType": "date"}
    }
}

STORE_SCHEMA = {
    "bsonType": "object",
    "required": ["store_id", "store_code", "store_name", "brand", "is_active"],
    "properties": {
        "store_id": {"bsonType": "string"},
        "store_code": {"bsonType": "string", "minLength": 2, "maxLength": 10},
        "store_name": {"bsonType": "string"},
        "brand": {"enum": ["BETTER_VISION", "WIZOPT"]},
        "address": {"bsonType": "string"},
        "city": {"bsonType": "string"},
        "state": {"bsonType": "string"},
        "pincode": {"bsonType": "string"},
        "phone": {"bsonType": "string"},
        "email": {"bsonType": "string"},
        "gstin": {"bsonType": "string"},
        "gst_state_code": {"bsonType": "string"},
        "coordinates": {
            "bsonType": "object",
            "properties": {
                "lat": {"bsonType": "double"},
                "lng": {"bsonType": "double"}
            }
        },
        "enabled_categories": {
            "bsonType": "array",
            "items": {"enum": ["FRAME", "SUNGLASS", "READING_GLASSES", "OPTICAL_LENS",
                              "CONTACT_LENS", "COLORED_CONTACT_LENS", "WATCH", "SMARTWATCH",
                              "SMARTGLASSES", "WALL_CLOCK", "ACCESSORIES", "SERVICES"]}
        },
        "is_active": {"bsonType": "bool"},
        "is_hq": {"bsonType": "bool"},
        "created_at": {"bsonType": "date"}
    }
}

PRODUCT_SCHEMA = {
    "bsonType": "object",
    "required": ["product_id", "sku", "category", "brand", "model", "mrp", "offer_price", "is_active"],
    "properties": {
        "product_id": {"bsonType": "string"},
        "sku": {"bsonType": "string"},
        "category": {
            "enum": ["FRAME", "SUNGLASS", "READING_GLASSES", "OPTICAL_LENS",
                    "CONTACT_LENS", "COLORED_CONTACT_LENS", "WATCH", "SMARTWATCH",
                    "SMARTGLASSES", "WALL_CLOCK", "ACCESSORIES", "SERVICES"]
        },
        "brand": {"bsonType": "string"},
        "model": {"bsonType": "string"},
        "variant": {"bsonType": "string"},
        "color": {"bsonType": "string"},
        "size": {"bsonType": "string"},
        "material": {"bsonType": "string"},
        "gender": {"enum": ["MALE", "FEMALE", "UNISEX", "KIDS"]},
        "mrp": {"bsonType": "decimal"},
        "offer_price": {"bsonType": "decimal"},
        "cost_price": {"bsonType": "decimal"},
        "hsn_code": {"bsonType": "string"},
        "tax_rate": {"bsonType": "double"},
        "images": {"bsonType": "array", "items": {"bsonType": "string"}},
        "attributes": {"bsonType": "object"},  # Category-specific attributes
        "is_active": {"bsonType": "bool"},
        "is_discountable": {"bsonType": "bool"},
        "discount_category": {"enum": ["MASS", "PREMIUM", "LUXURY", "NON_DISCOUNTABLE"]},
        "created_at": {"bsonType": "date"},
        "created_by": {"bsonType": "string"}
    }
}

STOCK_UNIT_SCHEMA = {
    "bsonType": "object",
    "required": ["stock_id", "product_id", "store_id", "quantity", "status"],
    "properties": {
        "stock_id": {"bsonType": "string"},
        "product_id": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "barcode": {"bsonType": "string"},
        "quantity": {"bsonType": "int", "minimum": 0},
        "reserved_quantity": {"bsonType": "int", "minimum": 0},
        "location_code": {"bsonType": "string"},  # Counter/Shelf location
        "batch_code": {"bsonType": "string"},
        "expiry_date": {"bsonType": "date"},
        "status": {"enum": ["AVAILABLE", "RESERVED", "SOLD", "DAMAGED", "RETURNED", "TRANSFERRED"]},
        "assigned_to": {"bsonType": "string"},  # Salesperson assignment
        "grn_id": {"bsonType": "string"},
        "barcode_printed": {"bsonType": "bool"},
        "received_at": {"bsonType": "date"},
        "last_counted_at": {"bsonType": "date"}
    }
}

CUSTOMER_SCHEMA = {
    "bsonType": "object",
    "required": ["customer_id", "customer_type", "name", "mobile", "created_at"],
    "properties": {
        "customer_id": {"bsonType": "string"},
        "customer_type": {"enum": ["B2C", "B2B"]},
        "name": {"bsonType": "string"},
        "mobile": {"bsonType": "string"},
        "email": {"bsonType": "string"},
        "gstin": {"bsonType": "string"},
        "pan": {"bsonType": "string"},
        "billing_address": {
            "bsonType": "object",
            "properties": {
                "line1": {"bsonType": "string"},
                "line2": {"bsonType": "string"},
                "city": {"bsonType": "string"},
                "state": {"bsonType": "string"},
                "pincode": {"bsonType": "string"}
            }
        },
        "shipping_address": {"bsonType": "object"},
        "patients": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "patient_id": {"bsonType": "string"},
                    "name": {"bsonType": "string"},
                    "mobile": {"bsonType": "string"},
                    "dob": {"bsonType": "date"},
                    "anniversary": {"bsonType": "date"}
                }
            }
        },
        "loyalty_points": {"bsonType": "int"},
        "store_credit": {"bsonType": "decimal"},
        "total_purchases": {"bsonType": "decimal"},
        "created_at": {"bsonType": "date"},
        "created_by": {"bsonType": "string"},
        "home_store_id": {"bsonType": "string"}
    }
}

PRESCRIPTION_SCHEMA = {
    "bsonType": "object",
    "required": ["prescription_id", "patient_id", "customer_id", "prescription_date", "source"],
    "properties": {
        "prescription_id": {"bsonType": "string"},
        "prescription_number": {"bsonType": "string"},
        "patient_id": {"bsonType": "string"},
        "customer_id": {"bsonType": "string"},
        "prescription_date": {"bsonType": "date"},
        "validity_months": {"bsonType": "int"},
        "source": {"enum": ["TESTED_AT_STORE", "FROM_DOCTOR"]},
        "optometrist_id": {"bsonType": "string"},
        "optometrist_name": {"bsonType": "string"},
        "right_eye": {
            "bsonType": "object",
            "properties": {
                "sph": {"bsonType": "string"},
                "cyl": {"bsonType": "string"},
                "axis": {"bsonType": "int"},
                "add": {"bsonType": "string"},
                "pd": {"bsonType": "string"},
                "prism": {"bsonType": "string"},
                "base": {"bsonType": "string"},
                "acuity": {"bsonType": "string"}
            }
        },
        "left_eye": {"bsonType": "object"},
        "lens_recommendation": {"bsonType": "string"},
        "coating_recommendation": {"bsonType": "string"},
        "remarks": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "created_at": {"bsonType": "date"}
    }
}

ORDER_SCHEMA = {
    "bsonType": "object",
    "required": ["order_id", "order_number", "customer_id", "store_id", "status", "created_at"],
    "properties": {
        "order_id": {"bsonType": "string"},
        "order_number": {"bsonType": "string"},
        "customer_id": {"bsonType": "string"},
        "patient_id": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "salesperson_id": {"bsonType": "string"},
        "items": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "item_id": {"bsonType": "string"},
                    "item_type": {"enum": ["FRAME", "SUNGLASS", "READING_GLASSES", "LENS", "CONTACT_LENS",
                                          "COLORED_CONTACT_LENS", "WATCH", "SMARTWATCH", "SMARTGLASSES",
                                          "WALL_CLOCK", "ACCESSORY", "SERVICE"]},
                    "product_id": {"bsonType": "string"},
                    "product_name": {"bsonType": "string"},
                    "quantity": {"bsonType": "int"},
                    "unit_price": {"bsonType": "decimal"},
                    "discount_percent": {"bsonType": "double"},
                    "discount_amount": {"bsonType": "decimal"},
                    "tax_rate": {"bsonType": "double"},
                    "tax_amount": {"bsonType": "decimal"},
                    "total": {"bsonType": "decimal"},
                    "prescription_id": {"bsonType": "string"}
                }
            }
        },
        "subtotal": {"bsonType": "decimal"},
        "discount_total": {"bsonType": "decimal"},
        "tax_total": {"bsonType": "decimal"},
        "grand_total": {"bsonType": "decimal"},
        "payments": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "payment_id": {"bsonType": "string"},
                    "method": {"enum": ["CASH", "UPI", "CARD", "BANK_TRANSFER", "EMI", "CREDIT", "GIFT_VOUCHER"]},
                    "amount": {"bsonType": "decimal"},
                    "reference": {"bsonType": "string"},
                    "received_at": {"bsonType": "date"}
                }
            }
        },
        "amount_paid": {"bsonType": "decimal"},
        "balance_due": {"bsonType": "decimal"},
        "status": {
            "enum": ["DRAFT", "CONFIRMED", "PROCESSING", "READY", "DELIVERED", "CANCELLED", "RETURNED"]
        },
        "payment_status": {"enum": ["UNPAID", "PARTIAL", "PAID", "REFUNDED"]},
        "expected_delivery": {"bsonType": "date"},
        "delivered_at": {"bsonType": "date"},
        "invoice_number": {"bsonType": "string"},
        "invoice_date": {"bsonType": "date"},
        "notes": {"bsonType": "string"},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"}
    }
}

VENDOR_SCHEMA = {
    "bsonType": "object",
    "required": ["vendor_id", "vendor_code", "legal_name", "vendor_type", "gstin_status"],
    "properties": {
        "vendor_id": {"bsonType": "string"},
        "vendor_code": {"bsonType": "string"},
        "legal_name": {"bsonType": "string"},
        "trade_name": {"bsonType": "string"},
        "vendor_type": {"enum": ["INDIAN", "INTERNATIONAL"]},
        "gstin_status": {"enum": ["REGISTERED", "UNREGISTERED", "COMPOSITE"]},
        "gstin": {"bsonType": "string"},
        "pan": {"bsonType": "string"},
        "address": {"bsonType": "string"},
        "city": {"bsonType": "string"},
        "state": {"bsonType": "string"},
        "pincode": {"bsonType": "string"},
        "country": {"bsonType": "string"},
        "email": {"bsonType": "string"},
        "mobile": {"bsonType": "string"},
        "contacts": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "name": {"bsonType": "string"},
                    "designation": {"bsonType": "string"},
                    "mobile": {"bsonType": "string"},
                    "email": {"bsonType": "string"},
                    "is_primary": {"bsonType": "bool"}
                }
            }
        },
        "credit_days": {"bsonType": "int"},
        "opening_balance": {"bsonType": "decimal"},
        "current_balance": {"bsonType": "decimal"},
        "moq_products": {"bsonType": "object"},
        "is_active": {"bsonType": "bool"},
        "created_at": {"bsonType": "date"}
    }
}

PURCHASE_ORDER_SCHEMA = {
    "bsonType": "object",
    "required": ["po_id", "po_number", "vendor_id", "status", "created_at"],
    "properties": {
        "po_id": {"bsonType": "string"},
        "po_number": {"bsonType": "string"},
        "vendor_id": {"bsonType": "string"},
        "vendor_name": {"bsonType": "string"},
        "delivery_store_id": {"bsonType": "string"},
        "delivery_address": {"bsonType": "string"},
        "expected_date": {"bsonType": "date"},
        "items": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "item_id": {"bsonType": "string"},
                    "product_id": {"bsonType": "string"},
                    "product_name": {"bsonType": "string"},
                    "sku": {"bsonType": "string"},
                    "ordered_qty": {"bsonType": "int"},
                    "received_qty": {"bsonType": "int"},
                    "unit_price": {"bsonType": "decimal"},
                    "tax_rate": {"bsonType": "double"}
                }
            }
        },
        "subtotal": {"bsonType": "decimal"},
        "tax_amount": {"bsonType": "decimal"},
        "total_amount": {"bsonType": "decimal"},
        "status": {"enum": ["DRAFT", "SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED", "FULLY_RECEIVED", "CANCELLED"]},
        "created_by": {"bsonType": "string"},
        "approved_by": {"bsonType": "string"},
        "sent_at": {"bsonType": "date"},
        "created_at": {"bsonType": "date"}
    }
}

GRN_SCHEMA = {
    "bsonType": "object",
    "required": ["grn_id", "grn_number", "vendor_id", "store_id", "status"],
    "properties": {
        "grn_id": {"bsonType": "string"},
        "grn_number": {"bsonType": "string"},
        "po_id": {"bsonType": "string"},
        "po_number": {"bsonType": "string"},
        "vendor_id": {"bsonType": "string"},
        "vendor_name": {"bsonType": "string"},
        "vendor_invoice_no": {"bsonType": "string"},
        "vendor_invoice_date": {"bsonType": "date"},
        "document_type": {"enum": ["INVOICE", "DC"]},
        "store_id": {"bsonType": "string"},
        "received_by": {"bsonType": "string"},
        "items": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "item_id": {"bsonType": "string"},
                    "po_item_id": {"bsonType": "string"},
                    "product_id": {"bsonType": "string"},
                    "product_name": {"bsonType": "string"},
                    "expected_qty": {"bsonType": "int"},
                    "received_qty": {"bsonType": "int"},
                    "accepted_qty": {"bsonType": "int"},
                    "rejected_qty": {"bsonType": "int"},
                    "rejection_reason": {"bsonType": "string"},
                    "barcode_printed": {"bsonType": "bool"}
                }
            }
        },
        "total_expected": {"bsonType": "int"},
        "total_received": {"bsonType": "int"},
        "total_accepted": {"bsonType": "int"},
        "status": {"enum": ["DRAFT", "PENDING_QC", "QC_PASSED", "QC_FAILED", "ACCEPTED", "DISPUTED"]},
        "has_mismatch": {"bsonType": "bool"},
        "mismatch_escalated": {"bsonType": "bool"},
        "escalation_note": {"bsonType": "string"},
        "accepted_by": {"bsonType": "string"},
        "accepted_at": {"bsonType": "date"},
        "created_at": {"bsonType": "date"}
    }
}

TASK_SCHEMA = {
    "bsonType": "object",
    "required": ["task_id", "title", "category", "priority", "assigned_to", "status"],
    "properties": {
        "task_id": {"bsonType": "string"},
        "task_number": {"bsonType": "string"},
        "title": {"bsonType": "string"},
        "description": {"bsonType": "string"},
        "category": {"bsonType": "string"},
        "priority": {"enum": ["P0", "P1", "P2", "P3", "P4"]},
        "source": {"enum": ["SYSTEM", "USER", "SOP"]},
        "assigned_to": {"bsonType": "string"},
        "assigned_by": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "due_at": {"bsonType": "date"},
        "status": {"enum": ["OPEN", "IN_PROGRESS", "COMPLETED", "ESCALATED", "CANCELLED"]},
        "escalation_level": {"bsonType": "int"},
        "escalated_to": {"bsonType": "string"},
        "escalated_at": {"bsonType": "date"},
        "completed_at": {"bsonType": "date"},
        "completion_notes": {"bsonType": "string"},
        "linked_entity_type": {"bsonType": "string"},
        "linked_entity_id": {"bsonType": "string"},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"}
    }
}

EXPENSE_SCHEMA = {
    "bsonType": "object",
    "required": ["expense_id", "expense_number", "employee_id", "category", "amount", "status"],
    "properties": {
        "expense_id": {"bsonType": "string"},
        "expense_number": {"bsonType": "string"},
        "employee_id": {"bsonType": "string"},
        "employee_name": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "category": {"enum": ["TRAVEL", "FOOD", "COURIER", "REPAIRS", "OFFICE_SUPPLIES", "CLIENT_RELATED", "PETTY_CASH", "OTHER"]},
        "amount": {"bsonType": "decimal"},
        "description": {"bsonType": "string"},
        "expense_date": {"bsonType": "date"},
        "bill_uploads": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "file_name": {"bsonType": "string"},
                    "file_path": {"bsonType": "string"},
                    "file_hash": {"bsonType": "string"}
                }
            }
        },
        "has_bill": {"bsonType": "bool"},
        "bill_waived": {"bsonType": "bool"},
        "status": {"enum": ["DRAFT", "SUBMITTED", "PENDING_APPROVAL", "APPROVED", "REJECTED", "PAID", "CANCELLED"]},
        "approved_by": {"bsonType": "string"},
        "approved_at": {"bsonType": "date"},
        "rejection_reason": {"bsonType": "string"},
        "paid_at": {"bsonType": "date"},
        "payment_reference": {"bsonType": "string"},
        "advance_id": {"bsonType": "string"},
        "created_at": {"bsonType": "date"}
    }
}

AUDIT_LOG_SCHEMA = {
    "bsonType": "object",
    "required": ["log_id", "timestamp", "user_id", "action", "module", "entity_type", "entity_id"],
    "properties": {
        "log_id": {"bsonType": "string"},
        "timestamp": {"bsonType": "date"},
        "user_id": {"bsonType": "string"},
        "user_name": {"bsonType": "string"},
        "user_role": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "store_name": {"bsonType": "string"},
        "ip_address": {"bsonType": "string"},
        "action": {"bsonType": "string"},
        "module": {"bsonType": "string"},
        "entity_type": {"bsonType": "string"},
        "entity_id": {"bsonType": "string"},
        "entity_name": {"bsonType": "string"},
        "severity": {"enum": ["INFO", "WARNING", "CRITICAL"]},
        "description": {"bsonType": "string"},
        "previous_value": {"bsonType": "string"},
        "new_value": {"bsonType": "string"},
        "changed_fields": {"bsonType": "array", "items": {"bsonType": "string"}},
        "metadata": {"bsonType": "object"}
    }
}

NOTIFICATION_SCHEMA = {
    "bsonType": "object",
    "required": ["notification_id", "notification_type", "user_id", "title", "status"],
    "properties": {
        "notification_id": {"bsonType": "string"},
        "notification_type": {"bsonType": "string"},
        "user_id": {"bsonType": "string"},
        "title": {"bsonType": "string"},
        "message": {"bsonType": "string"},
        "entity_type": {"bsonType": "string"},
        "entity_id": {"bsonType": "string"},
        "action_url": {"bsonType": "string"},
        "channels": {"bsonType": "array", "items": {"bsonType": "string"}},
        "priority": {"enum": ["LOW", "NORMAL", "HIGH", "URGENT"]},
        "status": {"enum": ["PENDING", "SENT", "DELIVERED", "READ", "FAILED"]},
        "sent_at": {"bsonType": "date"},
        "read_at": {"bsonType": "date"},
        "created_at": {"bsonType": "date"}
    }
}


# ============================================================================
# INDEX DEFINITIONS
# ============================================================================

INDEXES = {
    "users": [
        {"keys": [("username", 1)], "unique": True},
        {"keys": [("email", 1)], "unique": True},
        {"keys": [("store_ids", 1)]},
        {"keys": [("roles", 1)]},
        {"keys": [("is_active", 1)]}
    ],
    "stores": [
        {"keys": [("store_code", 1)], "unique": True},
        {"keys": [("brand", 1)]},
        {"keys": [("city", 1)]},
        {"keys": [("is_active", 1)]}
    ],
    "products": [
        {"keys": [("sku", 1)], "unique": True},
        {"keys": [("category", 1)]},
        {"keys": [("brand", 1)]},
        {"keys": [("is_active", 1)]},
        {"keys": [("brand", 1), ("category", 1)]}
    ],
    "stock_units": [
        {"keys": [("barcode", 1)], "unique": True, "sparse": True},
        {"keys": [("product_id", 1), ("store_id", 1)]},
        {"keys": [("store_id", 1), ("status", 1)]},
        {"keys": [("assigned_to", 1)]},
        {"keys": [("expiry_date", 1)], "sparse": True}
    ],
    "customers": [
        {"keys": [("mobile", 1)], "unique": True},
        {"keys": [("email", 1)], "sparse": True},
        {"keys": [("gstin", 1)], "sparse": True},
        {"keys": [("home_store_id", 1)]},
        {"keys": [("created_at", -1)]}
    ],
    "prescriptions": [
        {"keys": [("prescription_number", 1)], "unique": True},
        {"keys": [("patient_id", 1)]},
        {"keys": [("customer_id", 1)]},
        {"keys": [("optometrist_id", 1)]},
        {"keys": [("prescription_date", -1)]}
    ],
    "orders": [
        {"keys": [("order_number", 1)], "unique": True},
        {"keys": [("customer_id", 1)]},
        {"keys": [("store_id", 1), ("created_at", -1)]},
        {"keys": [("salesperson_id", 1), ("created_at", -1)]},
        {"keys": [("status", 1)]},
        {"keys": [("payment_status", 1)]},
        {"keys": [("created_at", -1)]}
    ],
    "vendors": [
        {"keys": [("vendor_code", 1)], "unique": True},
        {"keys": [("gstin", 1)], "sparse": True},
        {"keys": [("is_active", 1)]}
    ],
    "purchase_orders": [
        {"keys": [("po_number", 1)], "unique": True},
        {"keys": [("vendor_id", 1)]},
        {"keys": [("status", 1)]},
        {"keys": [("created_at", -1)]}
    ],
    "grns": [
        {"keys": [("grn_number", 1)], "unique": True},
        {"keys": [("po_id", 1)]},
        {"keys": [("vendor_id", 1)]},
        {"keys": [("store_id", 1)]},
        {"keys": [("status", 1)]},
        {"keys": [("created_at", -1)]}
    ],
    "tasks": [
        {"keys": [("task_number", 1)], "unique": True},
        {"keys": [("assigned_to", 1), ("status", 1)]},
        {"keys": [("store_id", 1), ("status", 1)]},
        {"keys": [("priority", 1), ("due_at", 1)]},
        {"keys": [("due_at", 1)]},
        {"keys": [("linked_entity_type", 1), ("linked_entity_id", 1)]}
    ],
    "expenses": [
        {"keys": [("expense_number", 1)], "unique": True},
        {"keys": [("employee_id", 1), ("status", 1)]},
        {"keys": [("store_id", 1), ("status", 1)]},
        {"keys": [("expense_date", -1)]},
        {"keys": [("status", 1)]}
    ],
    "audit_logs": [
        {"keys": [("timestamp", -1)]},
        {"keys": [("user_id", 1), ("timestamp", -1)]},
        {"keys": [("store_id", 1), ("timestamp", -1)]},
        {"keys": [("action", 1), ("timestamp", -1)]},
        {"keys": [("entity_type", 1), ("entity_id", 1)]},
        {"keys": [("severity", 1), ("timestamp", -1)]}
    ],
    "notifications": [
        {"keys": [("user_id", 1), ("status", 1)]},
        {"keys": [("user_id", 1), ("created_at", -1)]},
        {"keys": [("status", 1)]},
        {"keys": [("created_at", -1)]}
    ]
}

# ============================================================================
# COLLECTION CONFIGURATION
# ============================================================================

COLLECTIONS = {
    "users": {"schema": USER_SCHEMA, "indexes": INDEXES["users"]},
    "stores": {"schema": STORE_SCHEMA, "indexes": INDEXES["stores"]},
    "products": {"schema": PRODUCT_SCHEMA, "indexes": INDEXES["products"]},
    "stock_units": {"schema": STOCK_UNIT_SCHEMA, "indexes": INDEXES["stock_units"]},
    "customers": {"schema": CUSTOMER_SCHEMA, "indexes": INDEXES["customers"]},
    "prescriptions": {"schema": PRESCRIPTION_SCHEMA, "indexes": INDEXES["prescriptions"]},
    "orders": {"schema": ORDER_SCHEMA, "indexes": INDEXES["orders"]},
    "vendors": {"schema": VENDOR_SCHEMA, "indexes": INDEXES["vendors"]},
    "purchase_orders": {"schema": PURCHASE_ORDER_SCHEMA, "indexes": INDEXES["purchase_orders"]},
    "grns": {"schema": GRN_SCHEMA, "indexes": INDEXES["grns"]},
    "tasks": {"schema": TASK_SCHEMA, "indexes": INDEXES["tasks"]},
    "expenses": {"schema": EXPENSE_SCHEMA, "indexes": INDEXES["expenses"]},
    "audit_logs": {"schema": AUDIT_LOG_SCHEMA, "indexes": INDEXES["audit_logs"]},
    "notifications": {"schema": NOTIFICATION_SCHEMA, "indexes": INDEXES["notifications"]}
}


def get_all_schemas() -> Dict[str, Dict]:
    """Get all collection schemas"""
    return {name: config["schema"] for name, config in COLLECTIONS.items()}


def get_all_indexes() -> Dict[str, List]:
    """Get all collection indexes"""
    return {name: config["indexes"] for name, config in COLLECTIONS.items()}


if __name__ == "__main__":
    print("=" * 60)
    print("IMS 2.0 DATABASE SCHEMAS")
    print("=" * 60)
    
    print(f"\n📦 Collections: {len(COLLECTIONS)}")
    for name, config in COLLECTIONS.items():
        print(f"  • {name}: {len(config['indexes'])} indexes")
    
    total_indexes = sum(len(c["indexes"]) for c in COLLECTIONS.values())
    print(f"\n📊 Total Indexes: {total_indexes}")


# Additional schemas for complete coverage

ATTENDANCE_SCHEMA = {
    "bsonType": "object",
    "required": ["attendance_id", "employee_id", "store_id", "date"],
    "properties": {
        "attendance_id": {"bsonType": "string"},
        "employee_id": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "date": {"bsonType": "date"},
        "check_in": {"bsonType": "date"},
        "check_out": {"bsonType": "date"},
        "status": {"enum": ["PRESENT", "ABSENT", "HALF_DAY", "LEAVE", "HOLIDAY"]},
        "is_late": {"bsonType": "bool"},
        "late_minutes": {"bsonType": "int"},
        "overtime_minutes": {"bsonType": "int"},
        "notes": {"bsonType": "string"}
    }
}

LEAVE_SCHEMA = {
    "bsonType": "object",
    "required": ["leave_id", "employee_id", "leave_type", "from_date", "to_date", "status"],
    "properties": {
        "leave_id": {"bsonType": "string"},
        "employee_id": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "leave_type": {"enum": ["CASUAL", "SICK", "EARNED", "UNPAID", "MATERNITY", "PATERNITY"]},
        "from_date": {"bsonType": "date"},
        "to_date": {"bsonType": "date"},
        "days": {"bsonType": "int"},
        "reason": {"bsonType": "string"},
        "status": {"enum": ["PENDING", "APPROVED", "REJECTED", "CANCELLED"]},
        "approved_by": {"bsonType": "string"},
        "approved_at": {"bsonType": "date"},
        "rejection_reason": {"bsonType": "string"},
        "created_at": {"bsonType": "date"}
    }
}

PAYROLL_SCHEMA = {
    "bsonType": "object",
    "required": ["payroll_id", "employee_id", "year", "month", "status"],
    "properties": {
        "payroll_id": {"bsonType": "string"},
        "employee_id": {"bsonType": "string"},
        "employee_name": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "year": {"bsonType": "int"},
        "month": {"bsonType": "int"},
        "basic_salary": {"bsonType": "decimal"},
        "allowances": {"bsonType": "decimal"},
        "deductions": {"bsonType": "decimal"},
        "incentives": {"bsonType": "decimal"},
        "advance_deduction": {"bsonType": "decimal"},
        "net_salary": {"bsonType": "decimal"},
        "days_worked": {"bsonType": "int"},
        "days_absent": {"bsonType": "int"},
        "late_count": {"bsonType": "int"},
        "status": {"enum": ["DRAFT", "PENDING", "APPROVED", "PAID"]},
        "approved_by": {"bsonType": "string"},
        "approved_at": {"bsonType": "date"},
        "paid_at": {"bsonType": "date"},
        "payment_reference": {"bsonType": "string"},
        "created_at": {"bsonType": "date"}
    }
}

WORKSHOP_JOB_SCHEMA = {
    "bsonType": "object",
    "required": ["job_id", "job_number", "order_id", "store_id", "status"],
    "properties": {
        "job_id": {"bsonType": "string"},
        "job_number": {"bsonType": "string"},
        "order_id": {"bsonType": "string"},
        "order_number": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "customer_name": {"bsonType": "string"},
        "customer_phone": {"bsonType": "string"},
        "frame_details": {"bsonType": "object"},
        "lens_details": {"bsonType": "object"},
        "prescription_id": {"bsonType": "string"},
        "r_power": {"bsonType": "string"},
        "l_power": {"bsonType": "string"},
        "fitting_instructions": {"bsonType": "string"},
        "special_notes": {"bsonType": "string"},
        "technician_id": {"bsonType": "string"},
        "status": {"enum": ["PENDING", "IN_PROGRESS", "QC_FAILED", "READY", "DELIVERED", "CANCELLED"]},
        "expected_date": {"bsonType": "date"},
        "completed_at": {"bsonType": "date"},
        "qc_passed": {"bsonType": "bool"},
        "qc_notes": {"bsonType": "string"},
        "qc_by": {"bsonType": "string"},
        "qc_at": {"bsonType": "date"},
        "created_at": {"bsonType": "date"}
    }
}

ADVANCE_SCHEMA = {
    "bsonType": "object",
    "required": ["advance_id", "advance_number", "employee_id", "amount", "status"],
    "properties": {
        "advance_id": {"bsonType": "string"},
        "advance_number": {"bsonType": "string"},
        "employee_id": {"bsonType": "string"},
        "employee_name": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "advance_type": {"enum": ["SALARY_ADVANCE", "TRAVEL_ADVANCE", "SPECIAL_ADVANCE"]},
        "amount": {"bsonType": "decimal"},
        "purpose": {"bsonType": "string"},
        "requested_date": {"bsonType": "date"},
        "expected_settlement_date": {"bsonType": "date"},
        "status": {"enum": ["REQUESTED", "APPROVED", "DISBURSED", "PARTIALLY_SETTLED", "FULLY_SETTLED", "REJECTED"]},
        "approved_by": {"bsonType": "string"},
        "approved_at": {"bsonType": "date"},
        "disbursed_at": {"bsonType": "date"},
        "disbursement_reference": {"bsonType": "string"},
        "settled_amount": {"bsonType": "decimal"},
        "settlement_expenses": {"bsonType": "array", "items": {"bsonType": "string"}},
        "created_at": {"bsonType": "date"}
    }
}

# Add to COLLECTIONS
COLLECTIONS.update({
    "attendance": {"schema": ATTENDANCE_SCHEMA, "indexes": [
        {"keys": [("employee_id", 1), ("date", 1)], "unique": True},
        {"keys": [("store_id", 1), ("date", 1)]},
        {"keys": [("date", -1)]}
    ]},
    "leaves": {"schema": LEAVE_SCHEMA, "indexes": [
        {"keys": [("employee_id", 1), ("from_date", -1)]},
        {"keys": [("store_id", 1), ("status", 1)]},
        {"keys": [("status", 1)]}
    ]},
    "payroll": {"schema": PAYROLL_SCHEMA, "indexes": [
        {"keys": [("employee_id", 1), ("year", 1), ("month", 1)], "unique": True},
        {"keys": [("store_id", 1), ("year", 1), ("month", 1)]},
        {"keys": [("status", 1)]}
    ]},
    "workshop_jobs": {"schema": WORKSHOP_JOB_SCHEMA, "indexes": [
        {"keys": [("job_number", 1)], "unique": True},
        {"keys": [("order_id", 1)]},
        {"keys": [("store_id", 1), ("status", 1)]},
        {"keys": [("technician_id", 1), ("status", 1)]},
        {"keys": [("expected_date", 1)]}
    ]},
    "advances": {"schema": ADVANCE_SCHEMA, "indexes": [
        {"keys": [("advance_number", 1)], "unique": True},
        {"keys": [("employee_id", 1), ("status", 1)]},
        {"keys": [("status", 1)]}
    ]}
})
