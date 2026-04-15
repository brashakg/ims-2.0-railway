"""
IMS 2.0 - Vendor Returns Router
=================================
Vendor returns, debit notes, and credit note management
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional, Literal
from datetime import datetime
import uuid
from .auth import get_current_user
from ..dependencies import get_db

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class ReturnItemCreate(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    reason: str  # defective, wrong_item, expired, damaged_in_transit, quality_issue, not_as_ordered, other
    unit_price: float


class VendorReturnCreate(BaseModel):
    vendor_id: str
    vendor_name: str
    store_id: str
    items: List[ReturnItemCreate]
    return_type: Literal["credit_note", "replacement"]
    notes: Optional[str] = None


class VendorReturnStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _get_db():
    """Get raw MongoDB database for collections without a dedicated repository"""
    try:
        conn = get_db()
        if conn is not None and hasattr(conn, 'db'):
            return conn.db
        elif hasattr(conn, 'client'):
            return conn.client.ims_db
    except Exception:
        pass
    return None


def generate_return_id() -> str:
    """Generate unique vendor return ID"""
    timestamp = datetime.now().strftime("%Y%m%d")
    unique_part = str(uuid.uuid4())[:8].upper()
    return f"VR-{timestamp}-{unique_part}"


def generate_credit_note_number() -> str:
    """Generate unique credit note number"""
    timestamp = datetime.now().strftime("%y%m%d%H%M")
    return f"CN-{timestamp}-{str(uuid.uuid4())[:6].upper()}"


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("/")
async def list_vendor_returns(
    store_id: Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List vendor returns with optional filters"""
    db = _get_db()
    if db is None:
        return {"returns": [], "total": 0}

    try:
        collection = db.get_collection("vendor_returns")
        filter_dict = {}

        if store_id:
            filter_dict["store_id"] = store_id
        if vendor_id:
            filter_dict["vendor_id"] = vendor_id
        if status:
            filter_dict["status"] = status

        # Get total count
        total = collection.count_documents(filter_dict)

        # Get paginated results
        returns = list(
            collection.find(filter_dict)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )

        # Clean up MongoDB _id field for response
        for ret in returns:
            if "_id" in ret:
                del ret["_id"]

        return {"returns": returns, "total": total}

    except Exception as e:
        raise HTTPException(status_code=500, detail="A database error occurred. Please try again or contact support.")


@router.post("/", status_code=201)
async def create_vendor_return(
    return_data: VendorReturnCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new vendor return"""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        collection = db.get_collection("vendor_returns")
        return_id = generate_return_id()

        # Calculate total value
        total_value = sum(item.quantity * item.unit_price for item in return_data.items)

        # Create return document
        return_doc = {
            "return_id": return_id,
            "vendor_id": return_data.vendor_id,
            "vendor_name": return_data.vendor_name,
            "store_id": return_data.store_id,
            "items": [
                {
                    "product_id": item.product_id,
                    "product_name": item.product_name,
                    "quantity": item.quantity,
                    "reason": item.reason,
                    "unit_price": item.unit_price,
                }
                for item in return_data.items
            ],
            "return_type": return_data.return_type,
            "status": "created",
            "total_value": total_value,
            "credit_note_number": None,
            "credit_note_amount": None,
            "notes": return_data.notes or "",
            "created_at": datetime.now().isoformat(),
            "created_by": current_user.get("user_id"),
            "status_history": [
                {
                    "status": "created",
                    "timestamp": datetime.now().isoformat(),
                    "changed_by": current_user.get("user_id"),
                    "notes": "Return created",
                }
            ],
        }

        collection.insert_one(return_doc)

        return {
            "return_id": return_id,
            "message": "Vendor return created successfully",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create return. Please try again.")


@router.get("/{return_id}")
async def get_vendor_return(
    return_id: str, current_user: dict = Depends(get_current_user)
):
    """Get vendor return details"""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        collection = db.get_collection("vendor_returns")
        return_doc = collection.find_one({"return_id": return_id})

        if not return_doc:
            raise HTTPException(status_code=404, detail="Return not found")

        # Clean up MongoDB _id field
        if "_id" in return_doc:
            del return_doc["_id"]

        return return_doc

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="A database error occurred. Please try again or contact support.")


@router.patch("/{return_id}/status")
async def update_return_status(
    return_id: str,
    status_update: VendorReturnStatusUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update vendor return status"""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        collection = db.get_collection("vendor_returns")
        return_doc = collection.find_one({"return_id": return_id})

        if not return_doc:
            raise HTTPException(status_code=404, detail="Return not found")

        # Valid status transitions
        current_status = return_doc.get("status")
        valid_transitions = {
            "created": ["approved", "cancelled"],
            "approved": ["shipped", "cancelled"],
            "shipped": ["received_by_vendor"],
            "received_by_vendor": ["credit_issued", "replaced"],
            "credit_issued": [],
            "replaced": [],
            "cancelled": [],
        }

        if status_update.status not in valid_transitions.get(current_status, []):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from {current_status} to {status_update.status}",
            )

        # Generate credit note if transitioning to credit_issued
        credit_note_number = return_doc.get("credit_note_number")
        if status_update.status == "credit_issued" and not credit_note_number:
            credit_note_number = generate_credit_note_number()

        # Update document
        update_dict = {
            "status": status_update.status,
            "updated_at": datetime.now().isoformat(),
            "updated_by": current_user.get("user_id"),
        }

        # Add status to history
        status_history = return_doc.get("status_history", [])
        status_history.append(
            {
                "status": status_update.status,
                "timestamp": datetime.now().isoformat(),
                "changed_by": current_user.get("user_id"),
                "notes": status_update.notes or "",
            }
        )
        update_dict["status_history"] = status_history

        if credit_note_number:
            update_dict["credit_note_number"] = credit_note_number
            if status_update.status == "credit_issued":
                update_dict["credit_note_amount"] = return_doc.get("total_value")

        collection.update_one(
            {"return_id": return_id},
            {"$set": update_dict},
        )

        # Fetch and return updated document
        updated_doc = collection.find_one({"return_id": return_id})
        if "_id" in updated_doc:
            del updated_doc["_id"]

        return {
            "message": f"Return status updated to {status_update.status}",
            "return": updated_doc,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="A database error occurred. Please try again or contact support.")
