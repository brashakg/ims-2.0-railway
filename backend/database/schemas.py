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
                # DESIGN_MANAGER (lowest-privilege ecom design-queue role, BVI
                # Phase 1) is additive: a new member of the role matrix, no
                # existing gate changed. See api/services/rbac_policy.ALL_ROLES.
                "enum": ["SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF",
                        "STORE_MANAGER", "AREA_MANAGER", "CATALOG_MANAGER",
                        "DESIGN_MANAGER", "ACCOUNTANT", "ADMIN", "SUPERADMIN",
                        "INVESTOR"]
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
        "entity_id": {"bsonType": "string"},  # legal entity that owns this store (payroll/GST grouping)
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
            # PM (N5): HEARING_AID added for parity with gst_rates.py + the
            # AddProductPage UI + the PM category registry. NOTE: this $jsonSchema is
            # documentation/parity only -- startup uses ensure_indexes(), NOT
            # collMod/run_migrations, so no validator is enforced at runtime (HA was
            # never actually code-121 rejected). The PM engine's own category
            # registry is what gates writes; this keeps the schema doc in sync.
            #
            # Unification step-8: the canonical category taxonomy lives in
            # services/product_master.canonical_categories(). This literal enum
            # MIRRORS that list (kept as a literal because a $jsonSchema is a
            # static DB-validator document, not Python-evaluated against the
            # registry). test_unification_8_category_registry asserts this enum
            # stays equal to the registry so the two can never silently drift.
            "enum": ["FRAME", "SUNGLASS", "READING_GLASSES", "OPTICAL_LENS",
                    "CONTACT_LENS", "COLORED_CONTACT_LENS", "WATCH", "SMARTWATCH",
                    "SMARTGLASSES", "WALL_CLOCK", "ACCESSORIES", "SERVICES",
                    "HEARING_AID"]
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
        "gst_rate": {"bsonType": "double"},
        "images": {"bsonType": "array", "items": {"bsonType": "string"}},
        "attributes": {"bsonType": "object"},  # Category-specific attributes
        # ------------------------------------------------------------------
        # Contact-lens (CL) identity fields. All OPTIONAL + additive so they
        # only apply to CONTACT_LENS / COLORED_CONTACT_LENS products and never
        # break existing non-CL docs. These exact names are shared with the
        # CL-Rx / sale modules. HSN 90013000 / 5% GST (GST 2.0) for CL categories.
        "cl_series": {"bsonType": "string"},  # e.g. Acuvue Oasys
        "modality": {"enum": ["DAILY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY", "YEARLY", "COLOR"]},
        "base_curve": {"bsonType": "double"},  # BC, e.g. 8.6
        "diameter": {"bsonType": "double"},  # DIA, e.g. 14.2
        "cl_power": {"bsonType": "double"},  # SKU nominal power (per-eye set on Rx/sale)
        "cl_cyl": {"bsonType": "double"},  # toric cylinder
        "cl_axis": {"bsonType": "int", "minimum": 0, "maximum": 180},  # toric axis
        "cl_add": {"bsonType": "double"},  # multifocal add
        "pack_size": {"bsonType": "int", "minimum": 1},  # lenses per box
        "is_active": {"bsonType": "bool"},
        "is_discountable": {"bsonType": "bool"},
        # PM (N5): SERVICE added to match pricing_caps.CATEGORY_DISCOUNT_CAPS (10%
        # cap tier) -- SERVICE was a valid cap at POS/pricing but rejected by this
        # validator, so a product carrying discount_category="SERVICE" 121'd on save.
        "discount_category": {"enum": ["MASS", "PREMIUM", "LUXURY", "SERVICE", "NON_DISCOUNTABLE"]},
        # ------------------------------------------------------------------
        # PM (N5) unified product master: link to the PIM superset doc and the
        # short SKU-prefix code. Both OPTIONAL + additive so existing (legacy)
        # product rows -- which carry neither -- continue to validate unchanged.
        "pim_product_id": {"bsonType": "string"},  # FK -> catalog_products.id
        "sku_prefix": {"bsonType": "string"},       # short code (FR/SG/CL/...) for grid grouping
        "country_of_origin": {"bsonType": "string"},
        "warranty_months": {"bsonType": "int", "minimum": 0},
        "weight_grams": {"bsonType": "double"},
        # Mirror-sync compensation log (PM triple-write): per-target best-effort
        # status recorded back on the Mongo spine. Never blocks the spine write.
        "sync_status": {"bsonType": "object"},
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
        "batch_code": {"bsonType": "string"},  # batch / lot identifier (CL FEFO)
        "lot": {"bsonType": "string"},  # alias accepted alongside batch_code
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
    # `mobile` is NOT required: TechCherry-imported customers store the
    # number under `phone` (and the store under `preferred_store_id`),
    # so requiring the legacy keys would reject every imported doc. Both
    # variants are declared as optional string properties below.
    "required": ["customer_id", "customer_type", "name", "created_at"],
    "properties": {
        "customer_id": {"bsonType": "string"},
        "customer_type": {"enum": ["B2C", "B2B"]},
        "name": {"bsonType": "string"},
        "mobile": {"bsonType": "string"},
        "phone": {"bsonType": "string"},
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
        "home_store_id": {"bsonType": "string"},
        "preferred_store_id": {"bsonType": "string"}
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
        # rx_kind discriminates spectacle vs contact-lens Rx. Optional +
        # absent on every legacy doc (which is treated as SPECTACLE).
        "rx_kind": {"enum": ["SPECTACLE", "CONTACT_LENS"]},
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
        # ---- Contact-lens (CL) block. All optional; only set when CONTACT_LENS.
        # Fit by base-curve + diameter (not PD); cl_cyl/cl_axis for toric,
        # cl_add for multifocal. Mirrors the CL inventory product field names.
        "cl_right": {"bsonType": "object"},
        "cl_left": {"bsonType": "object"},
        "cl_brand": {"bsonType": "string"},
        "cl_series": {"bsonType": "string"},
        "modality": {"enum": ["DAILY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY", "YEARLY", "COLOR"]},
        "color": {"bsonType": "string"},
        "cl_product_id": {"bsonType": "string"},
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

# ----------------------------------------------------------------------------
# Display fixture / placement (v2-2a). User-requested optical-retail system:
# every SKU lives on a SPECIFIC fixture (wall/counter/pillar/drawer/fridge/...)
# inside the store. `display_fixtures` is the master list of physical fixtures
# per store; `display_placements` maps each SKU to the fixture(s) it sits on,
# with the quantity at that fixture + a free-form human position ("shelf-2 .
# slot-04"). One SKU can have MULTIPLE placement rows -- typically one primary
# display + one back-stock (drawer / fridge) -- so qty stacks across rows,
# not within a row. The GRN Receive flow (v2-2c) and Stock count sheet (v2-2c)
# both compose against these collections.
# ----------------------------------------------------------------------------

DISPLAY_FIXTURE_SCHEMA = {
    "bsonType": "object",
    "required": ["fixture_id", "store_id", "code", "name", "type", "floor",
                 "zone", "capacity", "is_active"],
    "properties": {
        "fixture_id": {"bsonType": "string"},  # slug, e.g. wd-01
        "store_id": {"bsonType": "string"},
        "code": {"bsonType": "string"},  # WD-01 / W-01 / C-02 (shown to staff)
        "name": {"bsonType": "string"},
        # Physical type. window/wall/pillar/counter/cabinet are customer-zone;
        # drawer is back-stock; fridge is temp-controlled (CL chamber).
        "type": {"enum": ["window", "wall", "pillar", "counter", "cabinet",
                          "gondola", "drawer", "fridge"]},
        "floor": {"enum": ["ground", "storage", "clinic"]},
        "zone": {"enum": ["A", "B", "C", "-"]},  # - = back-stock, not in customer zones
        "capacity": {"bsonType": "int", "minimum": 1},  # max units this fixture holds
        "lockable": {"bsonType": "bool"},
        # Which catalog types this fixture is designed for: any subset of
        # ["Frame", "Lens", "CL", "Access."]. The GRN modal filters per-line.
        "merch": {"bsonType": "array", "items": {"bsonType": "string"}},
        "last_audit_at": {"bsonType": "date"},  # set by count-sheet workflow
        # Optional flags -- present only when relevant:
        "mannequin": {"bsonType": "bool"},  # window/wall with mannequin
        "spotlit": {"bsonType": "bool"},  # has dedicated spotlight
        "temp_ctrl": {"bsonType": "string"},  # e.g. "2-8C" for CL fridge
        "no_qr": {"bsonType": "bool"},  # legacy fixture cannot accept QR codes
        "key_holder": {"bsonType": "string"},  # who holds the key, e.g. "SM only"
        "is_active": {"bsonType": "bool"},
        "notes": {"bsonType": "string"},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "created_by": {"bsonType": "string"}
    }
}

DISPLAY_PLACEMENT_SCHEMA = {
    "bsonType": "object",
    "required": ["placement_id", "sku", "store_id", "fixture_id", "qty"],
    "properties": {
        "placement_id": {"bsonType": "string"},
        "sku": {"bsonType": "string"},  # FK to products.sku (human handle)
        "store_id": {"bsonType": "string"},  # must match fixture's store_id
        "fixture_id": {"bsonType": "string"},  # FK to display_fixtures.fixture_id
        "qty": {"bsonType": "int", "minimum": 1},
        # Free-form human-readable spot WITHIN the fixture -- shelf and slot,
        # bin and tray, or whatever convention the floor uses. Optional.
        "position": {"bsonType": "string"},
        # One placement per (sku, store) can be the primary (customer-facing
        # display); others are back-stock. Enforced at write time, not at DB.
        "is_primary": {"bsonType": "bool"},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "created_by": {"bsonType": "string"},
        "last_moved_at": {"bsonType": "date"}
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
        {"keys": [("brand", 1), ("category", 1)]},
        # PM (N5) unified product master. All additive + sparse/optional so they
        # never reject or re-index legacy rows that lack the field.
        {"keys": [("pim_product_id", 1)], "sparse": True},  # FK lookup to catalog_products
        {"keys": [("sku_prefix", 1)]},                        # category-grid grouping
        {"keys": [("category", 1), ("brand", 1), ("model", 1), ("color", 1), ("size", 1)]}
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
        {"keys": [("created_at", -1)]},
        # Unification step 1: UNIQUE partial backstop so a Shopify webhook
        # retry / double-delivery can never double-book an online order.
        # PARTIAL: only docs that actually carry a string shopify_order_id are
        # constrained -- legacy / offline orders (field absent) are exempt.
        # Built at startup by api/services/shopify_ingest.
        # ensure_shopify_order_index (wired in main.py's lifespan); declared
        # here with the SAME name/options for documentation + migrations parity.
        {
            "keys": [("shopify_order_id", 1)],
            "unique": True,
            "partialFilterExpression": {"shopify_order_id": {"$type": "string"}},
            "name": "uniq_shopify_order_id",
        },
        # Collections Phase 1 (merchandising engine): per-product sales lookups
        # over order lines -- "orders containing product X, newest first" --
        # powering collection analytics / bestseller ranking without a full
        # collection scan. Multikey on the embedded items array; non-unique.
        {"keys": [("items.product_id", 1), ("created_at", -1)]}
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
        {"keys": [("created_at", -1)]},
        # F8 PO-vs-GRN variance: the aged-backorder TASKMASTER sweep and the
        # variance report both scan open POs (SENT/ACKNOWLEDGED/
        # PARTIALLY_RECEIVED) past their expected_date. A {status, expected_date}
        # compound index keeps that scan efficient.
        {"keys": [("status", 1), ("expected_date", 1)]}
    ],
    "grns": [
        {"keys": [("grn_number", 1)], "unique": True},
        {"keys": [("po_id", 1)]},
        {"keys": [("vendor_id", 1)]},
        {"keys": [("store_id", 1)]},
        {"keys": [("status", 1)]},
        {"keys": [("created_at", -1)]},
        # F9 P3: DB-level backstop for the app-level DC-number duplicate check
        # in vendors.create_grn (check-then-insert is racy across workers).
        # PARTIAL: only DELIVERY_CHALLAN rows that actually carry a dc_number
        # are constrained -- STANDARD GRNs (dc_number null) are exempt.
        # Creation is FAIL-SOFT everywhere it is built (migrations.py
        # _create_index returns a failure result; connection.py ensure_indexes
        # collects a warning): prod may still hold duplicate DC rows until the
        # dedup script runs, and that must only warn, never abort startup.
        {
            "keys": [("vendor_id", 1), ("dc_number", 1), ("store_id", 1)],
            "unique": True,
            "partialFilterExpression": {
                "grn_subtype": "DELIVERY_CHALLAN",
                "dc_number": {"$exists": True},
            },
            "name": "uniq_dc_vendor_number_store",
        }
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
        # UNIQUE seq is the DB-level guard against a duplicate sequence number
        # in the tamper-evident hash-chain (audit_chain.py). SPARSE so the
        # fail-soft UNCHAINED rows (written without `seq` when the chain head
        # can't be advanced) don't collide on a single null key.
        {"keys": [("seq", 1)], "unique": True, "sparse": True},
        {"keys": [("log_id", 1)], "unique": True, "sparse": True},
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
    ],
    "display_fixtures": [
        # One code per store -- two fixtures in the same store can't share a
        # code (W-01 must be unique within Bokaro, but Bokaro W-01 and Pune
        # W-01 can coexist).
        {"keys": [("store_id", 1), ("code", 1)], "unique": True},
        {"keys": [("store_id", 1), ("is_active", 1)]},
        {"keys": [("store_id", 1), ("type", 1), ("zone", 1)]}
    ],
    "display_placements": [
        # List all placements for a SKU at a store -- the hot path on the
        # Display Layout tab + product detail pane.
        {"keys": [("sku", 1), ("store_id", 1)]},
        # List all SKUs at a fixture -- powers the fixture side panel + the
        # Stock count sheet (groups by fixture).
        {"keys": [("fixture_id", 1)]},
        # One row per (sku, fixture) combo: stacking happens by bumping qty,
        # not by inserting a duplicate row. Stops accidental double-writes
        # from the GRN modal and the move endpoint.
        {"keys": [("store_id", 1), ("sku", 1), ("fixture_id", 1)], "unique": True}
    ],
    # SENTINEL system-health telemetry -- a row every ~60s tick, so it grows
    # UNBOUNDED. The TTL index auto-expires rows older than 14 days server-side.
    # It is CREATED by database/connection.py ensure_indexes (the live startup
    # path); health_checks has no JSON-schema validator so it is intentionally
    # NOT in COLLECTIONS. The TTL build needs >=500MB free disk; until it can
    # build, the SENTINEL in-tick prune (agents/implementations/sentinel.
    # prune_health_checks) bounds the collection.
    "health_checks": [
        {"keys": [("timestamp", 1)], "expireAfterSeconds": 1209600, "name": "ttl_timestamp"},
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
    "notifications": {"schema": NOTIFICATION_SCHEMA, "indexes": INDEXES["notifications"]},
    "display_fixtures": {
        "schema": DISPLAY_FIXTURE_SCHEMA,
        "indexes": INDEXES["display_fixtures"]
    },
    "display_placements": {
        "schema": DISPLAY_PLACEMENT_SCHEMA,
        "indexes": INDEXES["display_placements"]
    }
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
    
    print(f"\n[DB] Collections: {len(COLLECTIONS)}")
    for name, config in COLLECTIONS.items():
        print(f"  - {name}: {len(config['indexes'])} indexes")

    total_indexes = sum(len(c["indexes"]) for c in COLLECTIONS.values())
    print(f"\n[DB] Total Indexes: {total_indexes}")


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
        "status": {"enum": ["PRESENT", "ABSENT", "HALF_DAY", "LEAVE", "HOLIDAY", "WEEK_OFF", "LWP"]},
        "is_late": {"bsonType": "bool"},
        "late_minutes": {"bsonType": "int"},
        "shift_id": {"bsonType": "string"},
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

# A named work shift (e.g. "Morning 10-7") with a grace window and weekly-off
# day(s). One shift can be assigned to many employees. weekly_off uses Python's
# weekday() convention: Monday=0 .. Sunday=6.
SHIFT_SCHEMA = {
    "bsonType": "object",
    "required": ["shift_id", "name", "start_time", "end_time"],
    "properties": {
        "shift_id": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "name": {"bsonType": "string"},
        "start_time": {"bsonType": "string"},        # "HH:MM" 24h
        "end_time": {"bsonType": "string"},          # "HH:MM" 24h
        "grace_minutes": {"bsonType": "int"},        # minutes past start before "late"
        "weekly_off": {"bsonType": "array"},         # list of int weekdays (Mon=0..Sun=6)
        "is_active": {"bsonType": "bool"},
        "created_by": {"bsonType": "string"},
        "created_at": {"bsonType": "date"}
    }
}

# A request by an employee to move their weekly-off from one date to another.
# Manager-approved (requester cannot approve their own). Record-only: it does
# not mutate payroll.
WEEKOFF_SWAP_SCHEMA = {
    "bsonType": "object",
    "required": ["swap_id", "employee_id", "from_date", "to_date", "status"],
    "properties": {
        "swap_id": {"bsonType": "string"},
        "employee_id": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "from_date": {"bsonType": "string"},         # the scheduled week-off being given up
        "to_date": {"bsonType": "string"},           # the new week-off date requested
        "reason": {"bsonType": "string"},
        "status": {"enum": ["PENDING", "APPROVED", "REJECTED", "CANCELLED"]},
        "requested_by": {"bsonType": "string"},
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
    "shifts": {"schema": SHIFT_SCHEMA, "indexes": [
        {"keys": [("shift_id", 1)], "unique": True},
        {"keys": [("store_id", 1), ("is_active", 1)]}
    ]},
    "weekoff_swaps": {"schema": WEEKOFF_SWAP_SCHEMA, "indexes": [
        {"keys": [("swap_id", 1)], "unique": True},
        {"keys": [("employee_id", 1), ("status", 1)]},
        {"keys": [("store_id", 1), ("status", 1)]}
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


# ============================================================================
# PAYROLL FOUNDATION SCHEMAS (entity model, salary master, PT slabs)
# ============================================================================

# A legal entity (PAN). One entity can hold multiple GSTINs (one per state).
# Statutory payroll filings (PF/ESI/PT/TDS) are grouped per entity.
ENTITY_SCHEMA = {
    "bsonType": "object",
    "required": ["entity_id", "name", "is_active"],
    "properties": {
        "entity_id": {"bsonType": "string"},
        "name": {"bsonType": "string"},          # display name e.g. "Better Vision (Chas/Bokaro)"
        "legal_name": {"bsonType": "string"},    # registered legal name
        "pan": {"bsonType": "string"},
        "tan": {"bsonType": "string"},           # for TDS returns (24Q)
        "registered_address": {"bsonType": "string"},
        "gstins": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "gstin": {"bsonType": "string"},
                    "state_code": {"bsonType": "string"},
                    "state_name": {"bsonType": "string"},
                },
            },
        },
        "pf": {
            "bsonType": "object",
            "properties": {
                "registered": {"bsonType": "bool"},
                "establishment_code": {"bsonType": "string"},
            },
        },
        "esi": {
            "bsonType": "object",
            "properties": {
                "registered": {"bsonType": "bool"},
                "code": {"bsonType": "string"},
            },
        },
        "pt_registrations": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "state_code": {"bsonType": "string"},
                    "registration_number": {"bsonType": "string"},
                },
            },
        },
        "bank_account_no": {"bsonType": "string"},
        "bank_ifsc": {"bsonType": "string"},
        "bank_name": {"bsonType": "string"},
        "is_active": {"bsonType": "bool"},
        "created_at": {"bsonType": "string"},
        "updated_at": {"bsonType": "string"},
        "created_by": {"bsonType": "string"},
    },
}

# Per-employee Structured CTC + statutory configuration. Monthly amounts.
SALARY_CONFIG_SCHEMA = {
    "bsonType": "object",
    "required": ["employee_id", "basic"],
    "properties": {
        "config_id": {"bsonType": "string"},
        "employee_id": {"bsonType": "string"},
        "entity_id": {"bsonType": "string"},     # legal entity employing this person
        "store_id": {"bsonType": "string"},      # primary work store (drives PT state)
        "designation": {"bsonType": "string"},
        "department": {"bsonType": "string"},
        "date_of_joining": {"bsonType": "string"},
        # Earnings (monthly, structured CTC)
        "basic": {"bsonType": "double", "minimum": 0},
        "hra": {"bsonType": "double", "minimum": 0},
        "conveyance": {"bsonType": "double", "minimum": 0},
        "medical": {"bsonType": "double", "minimum": 0},
        "special_allowance": {"bsonType": "double", "minimum": 0},
        "other_allowances": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "name": {"bsonType": "string"},
                    "amount": {"bsonType": "double"},
                },
            },
        },
        # Statutory toggles + params
        "pf_applicable": {"bsonType": "bool"},
        "pf_wage_ceiling_cap": {"bsonType": "bool"},  # PF on min(basic, 15000) if True
        "esi_applicable": {"bsonType": "bool"},       # null/absent -> auto by gross <= 21000
        "pt_applicable": {"bsonType": "bool"},
        "tds_monthly": {"bsonType": "double", "minimum": 0},  # manual monthly TDS
        # Statutory IDs
        "uan": {"bsonType": "string"},                # PF Universal Account Number
        "esi_ip_number": {"bsonType": "string"},
        "pan": {"bsonType": "string"},
        # Bank (salary register / transfer)
        "bank_account_no": {"bsonType": "string"},
        "bank_ifsc": {"bsonType": "string"},
        "bank_name": {"bsonType": "string"},
        "is_active": {"bsonType": "bool"},
        "created_at": {"bsonType": "string"},
        "updated_at": {"bsonType": "string"},
        "created_by": {"bsonType": "string"},
    },
}

# State-wise Professional Tax slabs. EDITABLE — PT rules change; seeded with
# sensible defaults that the accountant must verify. `basis` says whether the
# slab thresholds are evaluated on MONTHLY or ANNUAL gross. Each slab item:
#   {min, max (null=infinity), amount, amount_february (optional), gender ("ANY"|"MALE"|"FEMALE")}
PT_SLAB_SCHEMA = {
    "bsonType": "object",
    "required": ["state_code", "slabs"],
    "properties": {
        "state_code": {"bsonType": "string"},
        "state_name": {"bsonType": "string"},
        "basis": {"bsonType": "string"},        # "MONTHLY" or "ANNUAL"
        "gender_aware": {"bsonType": "bool"},
        "slabs": {"bsonType": "array", "items": {"bsonType": "object"}},
        "notes": {"bsonType": "string"},
        "updated_at": {"bsonType": "string"},
    },
}

COLLECTIONS.update({
    "entities": {"schema": ENTITY_SCHEMA, "indexes": [
        {"keys": [("entity_id", 1)], "unique": True},
        {"keys": [("is_active", 1)]},
    ]},
    "salary_config": {"schema": SALARY_CONFIG_SCHEMA, "indexes": [
        {"keys": [("employee_id", 1)], "unique": True},
        {"keys": [("entity_id", 1)]},
        {"keys": [("store_id", 1)]},
    ]},
    "pt_slabs": {"schema": PT_SLAB_SCHEMA, "indexes": [
        {"keys": [("state_code", 1)], "unique": True},
    ]},
})


# ============================================================================
# LENS CATALOG SCHEMAS (Branch B' sub-PR 1 - lens-catalog rebuild)
# ============================================================================
# Owner-typed lens lines (brand x series x index x material x lens_type x
# coating combos) replace the per-SKU `products` rows the legacy Power Grid
# tried to map. Each LINE has a 3D power matrix (sph x cyl x add) of per-cell
# stock rows. Multifocals key on (sph, cyl, add); SV has add=null.
# See docs/LENS_CATALOG_REBUILD_SPEC.md for the full contract + owner Q&A
# (PR #270). Decisions baked into the shape here:
#   Q1 single `coating: str` column (not array) -- combos like DUAL_COAT are
#      their own coating codes; the owner edits the coating list in Settings.
#   Q2 (sph, cyl, add) cell key on lens_stock_lines. add is null on SV.
#   Q3 no migration -- owner re-enters via UI + bulk-import CSV/JSON matrix.
#   Q4 atomic reserve/commit/release using Mongo CAS on (on_hand - reserved).
#   Q5 lens_enum_config = single source of truth for editable enum lists.
#   Q6 seeded technical-dimension defaults only (indexes/materials/types/
#      coatings); brands + series start empty.

LENS_CATALOG_SCHEMA = {
    "bsonType": "object",
    "required": ["lens_line_id", "brand", "series", "index", "material",
                 "lens_type", "coating", "mrp", "is_active"],
    "properties": {
        # Slug: brand-series-index-material-lens_type-coating, lower-kebab.
        # Built by slugify_lens_line() in lens_catalog_validation.py.
        "lens_line_id": {"bsonType": "string"},
        "brand": {"bsonType": "string"},
        "series": {"bsonType": "string"},
        # Refractive index (1.50 / 1.56 / 1.60 / 1.67 / 1.74). Validated
        # against the live lens_enum_config["indexes"] list on write.
        "index": {"bsonType": "double", "minimum": 1.0, "maximum": 3.0},
        # Material code (CR39 / POLY / MR8 / MR174 / TRIVEX / GLASS / ...).
        # Validated against lens_enum_config["materials"].
        "material": {"bsonType": "string"},
        # Lens type (SV / BIFOCAL / PROGRESSIVE / OFFICE / READING / ...).
        # Validated against lens_enum_config["lens_types"].
        "lens_type": {"bsonType": "string"},
        # Q1: single string. Combos are their own codes (DUAL_COAT, etc.).
        # Validated against lens_enum_config["coatings"].
        "coating": {"bsonType": "string"},
        # Power range the LINE supports. Used by the cell-create validator
        # to refuse cells outside the supported sph/cyl/add boundaries.
        "sph_range": {
            "bsonType": "object",
            "properties": {
                "min": {"bsonType": "double"},
                "max": {"bsonType": "double"},
                "step": {"bsonType": "double", "minimum": 0.0},
            },
        },
        "cyl_range": {
            "bsonType": "object",
            "properties": {
                "min": {"bsonType": "double"},
                "max": {"bsonType": "double"},
                "step": {"bsonType": "double", "minimum": 0.0},
            },
        },
        # has_add discriminates SV (false -> add must be null in stock cells)
        # vs multifocal (true -> add_range required and cells must carry add).
        "has_add": {"bsonType": "bool"},
        "add_range": {
            "bsonType": ["object", "null"],
            "properties": {
                "min": {"bsonType": "double"},
                "max": {"bsonType": "double"},
                "step": {"bsonType": "double", "minimum": 0.0},
            },
        },
        # Default MRP for the line. Per-power-band overrides land in mrp_table
        # (deferred to sub-PR B'2; today this field carries the catalogue
        # default + the cell-level resolver falls back to it).
        "mrp": {"bsonType": "double", "minimum": 0},
        "cost_price": {"bsonType": "double", "minimum": 0},
        # Optional power-banded MRP table -- shape deferred to B'2; today we
        # accept any array so the field can ride along on imports.
        "mrp_table": {"bsonType": ["array", "null"]},
        # GST. Defaults to 5% per the editable hsn_gst_master; per-line
        # override only when the owner has a reason (e.g. polarized lens
        # banded into a different HSN).
        "gst_rate": {"bsonType": "double", "minimum": 0, "maximum": 28},
        "hsn_code": {"bsonType": "string"},
        "is_active": {"bsonType": "bool"},
        "notes": {"bsonType": ["string", "null"]},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "created_by": {"bsonType": "string"},
    },
}

LENS_STOCK_LINE_SCHEMA = {
    "bsonType": "object",
    "required": ["line_stock_id", "lens_line_id", "store_id", "sph", "cyl",
                 "on_hand", "reserved"],
    "properties": {
        "line_stock_id": {"bsonType": "string"},
        "lens_line_id": {"bsonType": "string"},  # FK to lens_catalog
        "store_id": {"bsonType": "string"},
        # Discrete power cell. cyl=0 for spherical-only powers; add is null
        # for SV (Q2). Floats are kept in 0.25 steps by the validator -- the
        # JSON schema only checks the type because Mongo's $jsonSchema can't
        # express "multiple of 0.25".
        "sph": {"bsonType": "double"},
        "cyl": {"bsonType": "double"},
        "add": {"bsonType": ["double", "null"]},
        # on_hand = physical units in the bin. reserved = units earmarked by
        # open POS orders that have not yet been dispatched. Effective
        # available = on_hand - reserved (computed by compute_available()).
        # Q4: atomic CAS in the reserve/commit/release endpoints keeps
        # (on_hand - reserved) >= 0 even with concurrent POS terminals.
        "on_hand": {"bsonType": "int", "minimum": 0},
        "reserved": {"bsonType": "int", "minimum": 0},
        "reorder_point": {"bsonType": "int", "minimum": 0},
        "safety_stock": {"bsonType": "int", "minimum": 0},
        "last_counted_at": {"bsonType": ["date", "null"]},
        "last_counted_by": {"bsonType": ["string", "null"]},
        "last_movement_at": {"bsonType": "date"},
    },
}

LENS_STOCK_AUDIT_SCHEMA = {
    "bsonType": "object",
    "required": ["audit_id", "line_stock_id", "lens_line_id", "store_id",
                 "action", "at"],
    "properties": {
        "audit_id": {"bsonType": "string"},
        "line_stock_id": {"bsonType": "string"},
        "lens_line_id": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        # action: how the stock row was touched.
        #   create        - first time this (line, store, cell) was seen.
        #   set_on_hand   - absolute on_hand update (PATCH).
        #   reserve       - POS Step 6 reserve (increments .reserved).
        #   commit        - Workshop dispatch (decrements both fields).
        #   release       - Order cancel before dispatch (decrements .reserved).
        #   bulk_import   - CSV/JSON paste-matrix upsert.
        "action": {"enum": ["create", "set_on_hand", "reserve", "commit",
                            "release", "bulk_import"]},
        # Signed deltas. For 'set_on_hand' delta_on_hand carries the diff
        # (new - prior). For absolute writes the prior+after fields still hold.
        "delta_on_hand": {"bsonType": "int"},
        "delta_reserved": {"bsonType": "int"},
        "prior": {
            "bsonType": "object",
            "properties": {
                "on_hand": {"bsonType": "int"},
                "reserved": {"bsonType": "int"},
            },
        },
        "after": {
            "bsonType": "object",
            "properties": {
                "on_hand": {"bsonType": "int"},
                "reserved": {"bsonType": "int"},
            },
        },
        # Who/what triggered the movement. POS / WORKSHOP / ORDER_CANCEL /
        # MANUAL / IMPORT. source_id is the order_id / workshop_job_id when
        # known, so the auditor can trace a unit back to its sale.
        "source_type": {"bsonType": ["string", "null"]},
        "source_id": {"bsonType": ["string", "null"]},
        "by_user_id": {"bsonType": "string"},
        "by_user_name": {"bsonType": "string"},
        "notes": {"bsonType": ["string", "null"]},
        "at": {"bsonType": "date"},
    },
}

LENS_ENUM_CONFIG_SCHEMA = {
    "bsonType": "object",
    "required": ["enum_id", "items"],
    "properties": {
        # Primary key: the enum_type name (coatings / brands / series /
        # indexes / materials / lens_types). The router refuses any other.
        "enum_id": {"bsonType": "string"},
        # The editable list. Stored as a heterogeneous array so:
        #   coatings/brands/materials/lens_types  -- strings
        #   indexes                                -- doubles
        #   series                                 -- dicts {brand: [series]}
        # The validation service narrows this at write time.
        "items": {"bsonType": "array"},
        "updated_at": {"bsonType": "date"},
        "updated_by": {"bsonType": "string"},
    },
}


COLLECTIONS.update({
    "lens_catalog": {
        "schema": LENS_CATALOG_SCHEMA,
        "indexes": [
            # One row per (brand, series, index, material, lens_type,
            # coating) combo -- UNIQUE so a duplicate insert raises E11000.
            # Slug (lens_line_id) is also unique; that's enforced by a
            # second unique index below.
            {
                "keys": [
                    ("brand", 1),
                    ("series", 1),
                    ("index", 1),
                    ("material", 1),
                    ("lens_type", 1),
                    ("coating", 1),
                ],
                "unique": True,
            },
            {"keys": [("lens_line_id", 1)], "unique": True},
            {"keys": [("brand", 1), ("is_active", 1)]},
            {"keys": [("is_active", 1)]},
        ],
    },
    "lens_stock_lines": {
        "schema": LENS_STOCK_LINE_SCHEMA,
        "indexes": [
            # One row per (line, store, cell). add is sparse-null on SV so
            # the unique index uses partialFilterExpression workaround in
            # plain (multikey) form; pymongo creates the index identically
            # whether or not add is null in any given doc.
            {
                "keys": [
                    ("lens_line_id", 1),
                    ("store_id", 1),
                    ("sph", 1),
                    ("cyl", 1),
                    ("add", 1),
                ],
                "unique": True,
            },
            # Store-level low-stock queries (gap planner) sort by available.
            {"keys": [("store_id", 1), ("on_hand", 1)]},
            {"keys": [("line_stock_id", 1)], "unique": True},
        ],
    },
    "lens_stock_audit": {
        "schema": LENS_STOCK_AUDIT_SCHEMA,
        "indexes": [
            # Per-cell history -- ordered desc by `at` is the hot path.
            {"keys": [("line_stock_id", 1), ("at", -1)]},
            # Trace by source (e.g. all movements triggered by order X).
            {"keys": [("source_id", 1)]},
            {"keys": [("audit_id", 1)], "unique": True},
        ],
    },
    "lens_enum_config": {
        "schema": LENS_ENUM_CONFIG_SCHEMA,
        "indexes": [
            {"keys": [("enum_id", 1)], "unique": True},
        ],
    },
})


# ============================================================================
# E-COMMERCE (BVI -> IMS) FOUNDATION SCHEMAS  (Phase 1)
# ============================================================================
# The "Online Store" module brings BVI's Shopify PIM into IMS as ONE app.
# Full target architecture: docs/reference/BVI_MERGE_PLAN.md section A.
#
# THE BRIDGE INVARIANT (locked by the council, section A.2):
#   - `stock_units` remains the master physical on-hand (serialized, one row per
#     physical unit). There is NO stored quantity mirror in `catalog_variants`.
#   - on_hand        = COUNT(stock_units WHERE sku, store, status=AVAILABLE)
#   - online_qty     = stock_allocation.recommend_allocation(on_hand - buffer)
#   - online listed-qty must NEVER exceed real on-hand, not even momentarily.
#
# `catalog_variants` is the variant IDENTITY + Shopify-mapping tier ONLY. It
# resolves the qty-model crux: BVI `VariantLocation.quantity` (an aggregate
# count) is NOT ported; IMS keeps serialized `stock_units` as on-hand truth.
#
# Idempotent join keys (never key on Mongo `_id`):
#   sku (primary) | store_barcode -> stock_units.barcode (the immutable physical
#   join key) | shopify_variant_id / shopify_inventory_item_id (Shopify side).
# Two-barcode model (BVI): `gtin`/`barcode` (the GTIN pushed to Shopify
# inventoryItem.barcode) vs `store_barcode` (NEVER pushed; joins to the physical
# serialized unit via stock_units.barcode).

CATALOG_VARIANT_SCHEMA = {
    "bsonType": "object",
    # `sku` is the primary identity + the physical-stock join handle. A variant
    # row must always carry it. Everything else is optional/additive so a partial
    # import (e.g. a variant not yet mapped to Shopify) never fails validation.
    "required": ["sku"],
    "properties": {
        "variant_id": {"bsonType": "string"},  # internal uuid (repo _id)
        "sku": {"bsonType": "string"},          # UNIQUE -- primary identity
        # Lineage to the parent PIM product (catalog_products). parent_sku is the
        # human handle; parent_product_id is the catalog_products id.
        "parent_product_id": {"bsonType": "string"},
        "parent_sku": {"bsonType": "string"},
        # Storefront PDP grouping (BVI ProductVariant option axes).
        "option_color": {"bsonType": "string"},
        "option_size": {"bsonType": "string"},
        # Shopify-side identity (set on first push; isolated here so the rest of
        # the catalog never carries Shopify GIDs). All optional until mapped.
        "shopify_variant_id": {"bsonType": "string"},
        "shopify_inventory_item_id": {"bsonType": "string"},
        "shopify_location_id": {"bsonType": "string"},
        # Two-barcode model: gtin/barcode = GTIN pushed to Shopify
        # inventoryItem.barcode; store_barcode = physical join key to
        # stock_units.barcode (NEVER pushed to Shopify).
        "gtin": {"bsonType": "string"},
        "barcode": {"bsonType": "string"},
        "store_barcode": {"bsonType": "string"},
        # NOTE: deliberately NO `quantity` field. On-hand is DERIVED from
        # stock_units; storing a count here would re-introduce the BVI split.
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "created_by": {"bsonType": "string"},
    },
}

# OPTIONAL embedded `ecom` sub-document on `catalog_products`. This is a
# DOCUMENTATION/contract schema only -- it is NOT registered as its own
# collection and NOT added to the catalog_products validator (catalog_products
# is currently schemaless via the catalog router). Every field is optional and
# additive: a product with no `ecom` key behaves exactly as before (fail-soft).
# Mirrors BVI Product's Shopify/SEO/theme/rxable/design-status fields.
# See BVI_MERGE_PLAN.md A.2.
ECOM_SUBDOC_SCHEMA = {
    "bsonType": "object",
    "properties": {
        "shopify_product_id": {"bsonType": "string"},
        "status": {"enum": ["DRAFT", "PUBLISHED", "ARCHIVED"]},
        "handle": {"bsonType": "string"},          # storefront URL slug
        "theme_suffix": {"bsonType": "string"},    # Shopify template suffix
        "rxable": {"bsonType": "bool"},            # prescription-configurable PDP
        "seo": {
            "bsonType": "object",
            "properties": {
                "title": {"bsonType": "string"},
                "description": {"bsonType": "string"},
                "page_url": {"bsonType": "string"},
                "tags": {"bsonType": "array", "items": {"bsonType": "string"}},
                "html": {"bsonType": "string"},
            },
        },
        # BVI Product.categorySpecific JSON (free-form per-category attributes).
        "category_specific": {"bsonType": "object"},
        "image_design_status": {"enum": ["PENDING_DESIGN", "READY"]},
        "last_pushed_at": {"bsonType": "date"},
        # Dirty flag -> push queue. True = changed locally since last Shopify push.
        "locally_modified": {"bsonType": "bool"},
    },
}

# `catalog_products` (the PIM superset; BVI/Shopify lineage) is DELIBERATELY
# schemaless -- the catalog router and the PM mirror write heterogeneous doc
# shapes (legacy catalog rows carry id+sku; PM mirror rows carry id+parent_sku;
# the Shopify superset hangs off `ecom`/`attributes` verbatim). This permissive
# schema exists ONLY so the collection is registered in COLLECTIONS for
# documentation/tooling parity (migrations.py reads config["schema"]); it
# validates nothing beyond "is an object". The indexes below ARE real: built at
# startup by connection.py ensure_indexes (unification step 1 -- the collection
# previously had ZERO DB indexes, so duplicate PIM docs were physically
# possible under the check-then-write upserts). UNIQUE sparse so legacy rows
# missing a field are exempt and pre-existing prod dupes only WARN at build.
CATALOG_PRODUCT_SCHEMA = {
    "bsonType": "object",
}

COLLECTIONS.update({
    "catalog_products": {
        "schema": CATALOG_PRODUCT_SCHEMA,
        "indexes": [
            # UNIQUE sparse on `id` -- the primary PIM identity (catalog router
            # + PM mirror both key their upserts on it).
            {"keys": [("id", 1)], "unique": True, "sparse": True},
            # UNIQUE sparse on `sku` -- carried by catalog-router docs; PM
            # mirror docs carry parent_sku instead (sparse exempts them).
            {"keys": [("sku", 1)], "unique": True, "sparse": True},
        ],
    },
})

COLLECTIONS.update({
    "catalog_variants": {
        "schema": CATALOG_VARIANT_SCHEMA,
        "indexes": [
            # UNIQUE sparse on sku -- the primary identity + stock join handle.
            # Sparse so a (malformed) row missing sku can't collide on null.
            {"keys": [("sku", 1)], "unique": True, "sparse": True},
            # Lineage roll-up: list all variants of a parent product.
            {"keys": [("parent_product_id", 1)], "sparse": True},
            # Shopify-side reverse lookup (webhook -> IMS variant). UNIQUE sparse:
            # one Shopify variant maps to exactly one IMS variant; unmapped rows
            # (field absent) are exempt.
            {"keys": [("shopify_variant_id", 1)], "unique": True, "sparse": True},
            # Physical join key. UNIQUE sparse so a store_barcode resolves to one
            # variant (mirrors stock_units.barcode being the immutable join key).
            {"keys": [("store_barcode", 1)], "unique": True, "sparse": True},
        ],
    },
})


# ============================================================================
# E-COMMERCE COLLECTIONS  (BVI -> IMS)  Phase 2 -- FLAGSHIP #1
# ============================================================================
# `ecom_collections` is the IMS home for BVI's Shopify Custom/Smart Collections
# (BVI_MERGE_PLAN.md section A.1 "Collection" -> NEW ecom_collections, and
# section B Phase 2). It is PUSH-DARK: collections are stored + edited inside IMS
# Mongo; nothing is written to Shopify in Phase 2 (the GraphQL push is Phase 5).
#
# A collection is one of two types:
#   CUSTOM (manual) -- membership is an explicit, ORDERED list of product SKUs,
#       stored as the embedded `products` array [{sku, position}] (Mongo-natural;
#       BVI's separate CollectionProduct join table flattens into this array).
#   SMART -- membership is COMPUTED from `rules` (a JSON array of field rules)
#       evaluated against the catalog at read time. `disjunctive` = true means
#       ANY rule matches (OR); false means ALL must match (AND). Mirrors Shopify
#       smart-collection semantics. The embedded `products` array is unused for
#       SMART collections (resolution is dynamic).
#
# Auto-collection LINEAGE (BVI Collection.autoSource / categoryAnchor): when a
# collection was generated by the brand-add / category-add pipelines, `auto_source`
# records what produced it ("brand:Ray-Ban", "category:SUNGLASSES") so a re-run
# updates in place instead of duplicating; `category_anchor` is the top-level
# category it rolls up under (menu-bucket placement). Phase 2 STORES these fields
# (accepts them on create + filters by them); the auto-generation CRON that writes
# them is Phase 2b/5 and is deliberately NOT built here.
#
# `locally_modified` is the dirty flag -> push queue (true = changed in IMS since
# the last Shopify sync). `handle` is the unique storefront slug (the idempotent
# join key alongside `shopify_collection_id`); never key on Mongo `_id`.

ECOM_COLLECTION_SCHEMA = {
    "bsonType": "object",
    # `handle` is the unique storefront slug + the natural idempotent key for a
    # re-import. A collection must always carry one. `title` is required for any
    # human-facing list. Everything else is optional/additive so a partial import
    # (a collection not yet mapped to Shopify) never fails validation.
    "required": ["handle", "title"],
    "properties": {
        "collection_id": {"bsonType": "string"},   # internal uuid (repo _id)
        # Shopify-side identity (set on first push / seed-import). Optional until
        # mapped; isolated here so the rest of the catalog never carries GIDs.
        "shopify_collection_id": {"bsonType": "string"},
        "title": {"bsonType": "string"},
        "handle": {"bsonType": "string"},           # UNIQUE storefront slug
        "description": {"bsonType": "string"},
        "description_html": {"bsonType": "string"},
        # CUSTOM = manual SKU list; SMART = rules-driven (computed membership).
        "collection_type": {"enum": ["CUSTOM", "SMART"]},
        # Storefront sort order (Shopify CollectionSortOrder, e.g. BEST_SELLING,
        # ALPHA_ASC, PRICE_DESC, MANUAL). Free string -- mirrors BVI.
        "sort_order": {"bsonType": "string"},
        "template_suffix": {"bsonType": "string"},  # Shopify theme template
        "image_url": {"bsonType": "string"},
        "image_alt": {"bsonType": "string"},
        "seo_title": {"bsonType": "string"},
        "seo_description": {"bsonType": "string"},
        "published": {"bsonType": "bool"},
        # Cached membership size (manual: len(products); smart: last resolved
        # count). Advisory only -- the resolver is the source of truth for SMART.
        "products_count": {"bsonType": "int"},
        # Smart-collection rules. Stored as a JSON-style array of rule objects
        # {field, relation, value} (relation: EQUALS|CONTAINS). `disjunctive`
        # true = OR across rules, false = AND. Empty/absent for CUSTOM.
        "rules": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "properties": {
                    "field": {"bsonType": "string"},
                    "relation": {"bsonType": "string"},
                    "value": {"bsonType": "string"},
                },
            },
        },
        "disjunctive": {"bsonType": "bool"},
        # Round-2 collection metafields (BVI Collection) -- the three high-traffic
        # ones as columns; arbitrary extras hang off `metafields`.
        "banner_image": {"bsonType": "string"},
        "short_description": {"bsonType": "string"},
        "sort_priority": {"bsonType": "int"},        # lower = earlier in nav
        "metafields": {"bsonType": "object"},
        # Auto-creation lineage (BVI autoSource / categoryAnchor). "<source>:<value>"
        # e.g. "brand:Ray-Ban", "category:SUNGLASSES". Null for hand-curated.
        "auto_source": {"bsonType": "string"},
        "category_anchor": {"bsonType": "string"},
        # Dirty flag -> Phase-5 push queue. True = edited in IMS since last sync.
        "locally_modified": {"bsonType": "bool"},
        "last_synced_at": {"bsonType": "date"},
        # Manual membership: ORDERED embedded array (BVI CollectionProduct flattened).
        # Each item {sku, position}. Unused for SMART collections.
        "products": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "required": ["sku"],
                "properties": {
                    "sku": {"bsonType": "string"},
                    "position": {"bsonType": "int"},
                },
            },
        },
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "created_by": {"bsonType": "string"},
    },
}

COLLECTIONS.update({
    "ecom_collections": {
        "schema": ECOM_COLLECTION_SCHEMA,
        "indexes": [
            # UNIQUE sparse on handle -- the storefront slug + idempotent re-import
            # key. Sparse so a (malformed) row missing handle can't collide on null.
            {"keys": [("handle", 1)], "unique": True, "sparse": True},
            # Shopify-side reverse lookup (seed-import / future webhook). UNIQUE
            # sparse: one Shopify collection maps to exactly one IMS collection;
            # unmapped (PUSH-DARK) rows are exempt.
            {"keys": [("shopify_collection_id", 1)], "unique": True, "sparse": True},
            # Auto-lineage filters (idempotent re-run lookup + menu placement).
            {"keys": [("auto_source", 1)], "sparse": True},
            {"keys": [("category_anchor", 1)], "sparse": True},
        ],
    },
})


# ============================================================================
# E-COMMERCE MENUS / MEGA-MENU  (BVI -> IMS)  Phase 3 -- FLAGSHIP #2
# ============================================================================
# `ecom_menus` is the IMS home for BVI's Shopify navigation Menus + the mega-menu
# (BVI_MERGE_PLAN.md section A.1 "Menu / MenuItem" -> NEW ecom_menus, and section
# B Phase 3). It is PUSH-DARK: menus are stored + edited inside IMS Mongo; nothing
# is written to Shopify in Phase 3 (the GraphQL `menuUpdate` push is Phase 5).
#
# Mongo's nested-document model fits Shopify's recursive MenuItem tree natively,
# so the whole item hierarchy is stored as an EMBEDDED `items` tree on the menu
# doc (no separate join collection, unlike BVI's relational MenuItem table with
# its parentId self-relation). Each node carries its own `children` array of the
# same node shape, so an N-level mega-menu is one document.
#
# A MenuItem node (the shape of every element of `items` and of any node's
# `children`):
#   id           internal uuid for the node (stable across reorders; the handle
#                used by the add/move/remove item helpers -- never Mongo `_id`).
#   parent_id    id of the parent node, or null for a top-level node. Redundant
#                with tree position but kept so a flat view / move op is cheap.
#   position     0-based order among its siblings (renumbered on every mutation).
#   title        display label.
#   item_type    Shopify MenuItemType: COLLECTION | COLLECTIONS | PRODUCT | PAGE |
#                BLOG | ARTICLE | FRONTPAGE | CATALOG | SEARCH | HTTP |
#                SHOP_POLICY | METAOBJECT.
#   url          for HTTP / external links; otherwise derived from resource_id.
#   resource_id  Shopify GID of the linked resource (Collection/Product/Page...).
#   tags_filter  CSV -- "filter this collection by these tags".
#   shopify_item_id  Shopify MenuItem GID (set on first push; PUSH-DARK -> absent).
#   -- mega-menu presentation (BVI round-2 M6/M11) --
#   icon_url     small thumbnail shown next to the item.
#   banner_url   wider banner for top-level mega-menu cards.
#   badge_text   NEW / SALE / META / EXCLUSIVE.
#   badge_color  hex; null = theme default.
#   pinned_to_top  bool -- top categories pinned above niche ones.
#   children     recursive array of MenuItem nodes (sub-menu).
#
# `handle` is the unique menu slug (main-menu, footer, ...) + the idempotent
# join key alongside `shopify_menu_id`; never key on Mongo `_id`. `is_default`
# marks the storefront's primary menu; `active=false` hides a menu from the
# editor + skips its (future) Shopify push without deleting it. `locally_modified`
# is the dirty flag -> Phase-5 push queue.

# A single MenuItem node. NOTE: `children` is typed as a generic array-of-object
# rather than a self-$ref because MongoDB's $jsonSchema validator cannot express
# unbounded recursion; the node CONTRACT (every child is this same shape) is
# enforced in the repository's _renumber/_normalize_node walk, not the validator.
ECOM_MENU_ITEM_SCHEMA = {
    "bsonType": "object",
    "required": ["id", "title"],
    "properties": {
        "id": {"bsonType": "string"},          # internal node uuid
        "parent_id": {"bsonType": ["string", "null"]},
        "position": {"bsonType": "int"},
        "title": {"bsonType": "string"},
        "item_type": {
            "enum": [
                "COLLECTION", "COLLECTIONS", "PRODUCT", "PAGE", "BLOG",
                "ARTICLE", "FRONTPAGE", "CATALOG", "SEARCH", "HTTP",
                "SHOP_POLICY", "METAOBJECT",
            ]
        },
        "url": {"bsonType": ["string", "null"]},
        "resource_id": {"bsonType": ["string", "null"]},
        "tags_filter": {"bsonType": ["string", "null"]},
        "shopify_item_id": {"bsonType": ["string", "null"]},
        # Mega-menu presentation.
        "icon_url": {"bsonType": ["string", "null"]},
        "banner_url": {"bsonType": ["string", "null"]},
        "badge_text": {"bsonType": ["string", "null"]},
        "badge_color": {"bsonType": ["string", "null"]},
        "pinned_to_top": {"bsonType": "bool"},
        # Recursive sub-tree (validator can't self-ref -- see note above).
        "children": {"bsonType": "array"},
    },
}

ECOM_MENU_SCHEMA = {
    "bsonType": "object",
    # `handle` is the unique menu slug + idempotent re-import key; `title` is
    # required for the editor list. Everything else is optional/additive so a
    # partial import (a menu not yet mapped to Shopify) never fails validation.
    "required": ["handle", "title"],
    "properties": {
        "menu_id": {"bsonType": "string"},      # internal uuid (repo _id)
        # Shopify-side identity (set on first push / seed-import). Optional until
        # mapped; isolated here so the rest of the catalog never carries GIDs.
        "shopify_menu_id": {"bsonType": "string"},
        "handle": {"bsonType": "string"},        # UNIQUE slug: main-menu, footer...
        "title": {"bsonType": "string"},
        # The storefront's primary menu (BVI Menu.isDefault).
        "is_default": {"bsonType": "bool"},
        # active=false hides the menu from the editor + skips Shopify push without
        # deleting it (so it can be re-enabled later). BVI Menu.active.
        "active": {"bsonType": "bool"},
        # Dirty flag -> Phase-5 push queue. True = edited in IMS since last sync.
        "locally_modified": {"bsonType": "bool"},
        "last_synced_at": {"bsonType": "date"},
        # EMBEDDED recursive item tree (top-level nodes; each has `children`).
        "items": {"bsonType": "array", "items": ECOM_MENU_ITEM_SCHEMA},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "created_by": {"bsonType": "string"},
    },
}

COLLECTIONS.update({
    "ecom_menus": {
        "schema": ECOM_MENU_SCHEMA,
        "indexes": [
            # UNIQUE sparse on handle -- the menu slug + idempotent re-import key.
            # Sparse so a (malformed) row missing handle can't collide on null.
            {"keys": [("handle", 1)], "unique": True, "sparse": True},
            # Shopify-side reverse lookup (seed-import / future webhook). UNIQUE
            # sparse: one Shopify menu maps to exactly one IMS menu; unmapped
            # (PUSH-DARK) rows are exempt.
            {"keys": [("shopify_menu_id", 1)], "unique": True, "sparse": True},
        ],
    },
})


# ============================================================================
# PRODUCT IMAGES / IMAGE DESIGN QUEUE  (BVI -> IMS)  Phase 4 -- FLAGSHIP #3
# ============================================================================
# `product_images` is the IMS home for BVI's ProductImage + VariantImage tables
# AND the design-team work queue that wraps them (BVI_MERGE_PLAN.md section A.1
# "ProductImage" / "VariantImage" -> NEW product_images, and section B Phase 4).
# It is PUSH-DARK: image records + their RAW->EDITED->APPROVED design lifecycle
# are tracked inside IMS Mongo only; nothing is written to Shopify in Phase 4
# (the GraphQL image push that fills `shopify_image_id` is Phase 5).
#
# One doc = one image of a product (or a specific variant). BVI's two tables
# (ProductImage on the parent, VariantImage on the variant) are merged into this
# single collection, discriminated by an optional `variant_id`: a row with
# variant_id=null is a parent/product-level image (BVI ProductImage); a row with
# a variant_id is a variant-level image (BVI VariantImage).
#
# THE DESIGN LIFECYCLE (the daily-ops value):
#   kind    = RAW (uploaded by a cataloger, the un-edited source) | EDITED (the
#             designer's retouched output) | FINAL (the approved, push-ready asset).
#   status  = QUEUED -> IN_PROGRESS -> REVIEW -> APPROVED | REJECTED, with
#             REJECTED -> IN_PROGRESS to re-work. The valid-transition graph is
#             enforced in the repository (set_status) + router (409 on illegal).
#   assigned_to  = the DESIGN_MANAGER who owns the edit.
#   edited_url   = where the designer's EDITED asset lives (set by attach_edited,
#                  which also flips status to REVIEW).
#   approved_at + reviewed_by  = stamped when a reviewer APPROVES (which also
#                  writes a chained audit_logs row, since approval gates go-live).
#
# Idempotent join keys (never key on Mongo `_id`):
#   image_id (internal uuid, primary) | product_id (-> catalog_products) |
#   variant_id (-> catalog_variants, optional). `shopify_image_id` stays null in
#   Phase 4; it is the Phase-5 push reverse-lookup. `url`/`edited_url` are the
#   asset locations (the P4 re-host audit rewrites BVI /uploads/ paths into these).
#
# Every field beyond image_id/product_id/url is optional/additive so a partial
# import (e.g. a Shopify-pulled image with no design state) never fails
# validation. Fail-soft throughout (mirrors the other ecom collections).

PRODUCT_IMAGE_SCHEMA = {
    "bsonType": "object",
    # image_id (internal id) + product_id (the parent it belongs to) + url (the
    # asset location) are the minimum for a meaningful image record.
    "required": ["image_id", "product_id", "url"],
    "properties": {
        "image_id": {"bsonType": "string"},          # internal uuid (repo _id)
        # Lineage. product_id -> catalog_products; variant_id -> catalog_variants
        # (null/absent = a parent/product-level image, BVI ProductImage; present =
        # a variant-level image, BVI VariantImage).
        "product_id": {"bsonType": "string"},
        "variant_id": {"bsonType": ["string", "null"]},
        # The (raw / source) asset URL. originalUrl in BVI; here `url` is the
        # canonical location and `edited_url` (below) is the designer's output.
        "url": {"bsonType": "string"},
        "edited_url": {"bsonType": ["string", "null"]},
        # Lifecycle discriminators.
        "kind": {"enum": ["RAW", "EDITED", "FINAL"]},
        "status": {
            "enum": [
                "QUEUED", "IN_PROGRESS", "REVIEW", "APPROVED", "REJECTED",
            ]
        },
        # Where the image came from. Drives whether it enters the design queue
        # (UPLOAD/SCRAPE/AI typically QUEUED) vs is a passive Shopify pull.
        "source": {"enum": ["UPLOAD", "SHOPIFY", "SCRAPE", "AI"]},
        # Display order among a product's images (0-based; BVI position).
        "position": {"bsonType": "int"},
        "alt_text": {"bsonType": ["string", "null"]},
        # Free-form designer brief / reviewer feedback.
        "design_notes": {"bsonType": ["string", "null"]},
        # Workflow actors (user ids).
        "assigned_to": {"bsonType": ["string", "null"]},   # a DESIGN_MANAGER
        "submitted_by": {"bsonType": ["string", "null"]},  # who queued it
        "reviewed_by": {"bsonType": ["string", "null"]},   # who approved/rejected
        "approved_at": {"bsonType": ["date", "null"]},
        # Shopify-side identity (set on the Phase-5 push; null while PUSH-DARK).
        "shopify_image_id": {"bsonType": ["string", "null"]},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
    },
}

COLLECTIONS.update({
    "product_images": {
        "schema": PRODUCT_IMAGE_SCHEMA,
        "indexes": [
            # All images of a product (the PDP gallery + the per-product queue
            # view). The hot read path.
            {"keys": [("product_id", 1)]},
            # The design QUEUE filter -- "show me everything QUEUED / IN_PROGRESS
            # / awaiting REVIEW".
            {"keys": [("status", 1)]},
            # "My queue" -- everything assigned to a given DESIGN_MANAGER. Sparse
            # so unassigned rows (assigned_to absent) aren't indexed on null.
            {"keys": [("assigned_to", 1)], "sparse": True},
        ],
    },
})


# ============================================================================
# PURCHASE INVOICE (AP + ITC source of truth)  --  Phase 1
# ============================================================================
# A first-class purchase invoice: the vendor's tax invoice recorded with LINE
# ITEMS (HSN + per-rate GST split) on top of the header-only AP record that the
# Cash-Flow screen used to create. It is stored in the SAME `vendor_bills`
# collection that AP aging + the ITC register / GSTR-2B reconcile already read,
# so the enrichment is purely additive:
#
#   * legacy header-only bills (no `lines`, no `doc_type`) stay valid and keep
#     aging / reconciling exactly as before;
#   * a line-item invoice carries `doc_type:"PURCHASE_INVOICE"`, the per-rate
#     CGST/SGST/IGST split, and -- crucially -- a WRITTEN `place_of_supply` so
#     the ITC code classifies inter-state purchases as IGST (the bug it fixes:
#     place_of_supply was READ by itc_reconcile but written nowhere, so every
#     inter-state purchase was mis-booked CGST+SGST).
#
# The fields the AP engine + ITC register consume are kept identical to a
# header-only bill (bill_id / vendor_id / bill_number / bill_date / due_date /
# taxable_amount / tax_amount / total_amount / outstanding / status /
# place_of_supply) so NO read path needs to change. `lines` + the split totals
# are a strict superset for the new Purchase Invoice screen + GST reports.
#
# This schema is registered in COLLECTIONS for documentation + tooling parity.
# It is intentionally NOT applied as a strict Mongo $jsonSchema validator at
# runtime (connection.ensure_indexes hand-builds indexes and applies no
# validators), so pre-existing header-only bills in production are never
# rejected. `required` is therefore the minimal header every bill already has.
PURCHASE_INVOICE_LINE_SCHEMA = {
    "bsonType": "object",
    "properties": {
        "product_id": {"bsonType": ["string", "null"]},
        "description": {"bsonType": ["string", "null"]},
        "hsn": {"bsonType": ["string", "null"]},
        "qty": {"bsonType": ["double", "int"]},
        "unit_price": {"bsonType": ["double", "int"]},
        "taxable": {"bsonType": ["double", "int"]},
        "gst_rate": {"bsonType": ["double", "int"]},
        "cgst": {"bsonType": ["double", "int"]},
        "sgst": {"bsonType": ["double", "int"]},
        "igst": {"bsonType": ["double", "int"]},
        "line_total": {"bsonType": ["double", "int"]},
    },
}

PURCHASE_INVOICE_SCHEMA = {
    "bsonType": "object",
    # Minimal header -- exactly what a legacy header-only vendor bill already
    # carries, so registering this schema cannot invalidate existing rows.
    "required": ["bill_id", "vendor_id", "bill_number", "bill_date", "total_amount"],
    "properties": {
        # Canonical AP identity (shared with header-only bills).
        "bill_id": {"bsonType": "string"},
        # Alias of bill_id for the Purchase Invoice surface (same value).
        "invoice_id": {"bsonType": "string"},
        # Discriminator: present + "PURCHASE_INVOICE" => has lines + GST split.
        "doc_type": {"bsonType": "string"},
        "vendor_id": {"bsonType": "string"},
        "vendor_name": {"bsonType": "string"},
        "vendor_gstin": {"bsonType": ["string", "null"]},
        # Recipient (buyer) -- which of our legal entities / GSTINs is claiming
        # the ITC. Drives the inter-state (IGST) vs intra-state decision.
        "recipient_entity_id": {"bsonType": ["string", "null"]},
        "recipient_gstin": {"bsonType": ["string", "null"]},
        # 2-digit GST state code of the place of supply (the buyer's state for
        # a goods purchase). WRITTEN here so itc_reconcile classifies correctly.
        "place_of_supply": {"bsonType": ["string", "null"]},
        # The vendor's own invoice number (== bill_number, the AP dup key).
        "bill_number": {"bsonType": "string"},
        "invoice_number": {"bsonType": ["string", "null"]},
        "bill_date": {"bsonType": "string"},
        "invoice_date": {"bsonType": ["string", "null"]},
        "due_date": {"bsonType": ["string", "null"]},
        "credit_days": {"bsonType": ["int", "double"]},
        "po_id": {"bsonType": ["string", "null"]},
        "grn_id": {"bsonType": ["string", "null"]},
        "lines": {"bsonType": "array", "items": PURCHASE_INVOICE_LINE_SCHEMA},
        # Header money (shared with AP + ITC reads). taxable_amount/tax_amount
        # mirror taxable_total / (cgst+sgst+igst) so the existing register works.
        "taxable_amount": {"bsonType": ["double", "int"]},
        "tax_amount": {"bsonType": ["double", "int"]},
        "taxable_total": {"bsonType": ["double", "int"]},
        "cgst_total": {"bsonType": ["double", "int"]},
        "sgst_total": {"bsonType": ["double", "int"]},
        "igst_total": {"bsonType": ["double", "int"]},
        "total_amount": {"bsonType": ["double", "int"]},
        "total": {"bsonType": ["double", "int"]},
        "tds": {"bsonType": ["double", "int"]},
        "itc_eligible": {"bsonType": "bool"},
        "outstanding": {"bsonType": ["double", "int"]},
        "status": {"enum": ["DRAFT", "OUTSTANDING", "PARTIAL", "PAID"]},
        "notes": {"bsonType": ["string", "null"]},
        "created_by": {"bsonType": ["string", "null"]},
        "created_at": {"bsonType": "string"},
    },
}

COLLECTIONS.update({
    # Same physical collection as the header-only AP bills (see the long note
    # above). Indexes mirror the per-vendor duplicate guard + the new PO/GRN
    # back-links. Non-unique on (vendor_id, bill_number): the duplicate-invoice
    # block stays in application code (create paths) because legacy prod data
    # may already contain duplicates a unique index would reject at build time.
    "vendor_bills": {
        "schema": PURCHASE_INVOICE_SCHEMA,
        "indexes": [
            {"keys": [("bill_id", 1)], "unique": True, "sparse": True},
            {"keys": [("vendor_id", 1), ("bill_number", 1)]},
            {"keys": [("po_id", 1)], "sparse": True},
            {"keys": [("grn_id", 1)], "sparse": True},
            {"keys": [("status", 1)]},
            {"keys": [("bill_date", -1)]},
        ],
    },
})
