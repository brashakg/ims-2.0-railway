"""
IMS 2.0 - Authentication Router
================================
Login, logout, token management
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timedelta
import jwt
import hashlib
import os
import logging

logger = logging.getLogger(__name__)

router = APIRouter()
# HTTPBearer with auto_error=False allows us to handle missing credentials gracefully
security = HTTPBearer(auto_error=False)

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "ims-2.0-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours


# ============================================================================
# SCHEMAS
# ============================================================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    store_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict

class TokenData(BaseModel):
    user_id: str
    username: str
    roles: List[str]
    store_ids: List[str]
    active_store_id: Optional[str]
    exp: datetime

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)

class RefreshTokenRequest(BaseModel):
    token: str


# ============================================================================
# HELPERS
# ============================================================================

def hash_password(password: str) -> str:
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return hash_password(plain_password) == hashed_password

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    """Decode and validate JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    """Get current user from JWT token"""
    try:
        if not credentials:
            raise HTTPException(
                status_code=401,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"}
            )

        token = credentials.credentials
        if not token:
            raise HTTPException(
                status_code=401,
                detail="No token provided",
                headers={"WWW-Authenticate": "Bearer"}
            )

        payload = decode_token(token)
        return payload
    except HTTPException:
        # Re-raise HTTPException as-is
        raise
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user and return JWT token.
    Accepts either username or email in the username field.
    """
    # 36 Users across 10 roles for IMS 2.0
    # Stores: BV-DEL, BV-NOI, BV-GUR, BV-MUM, BV-BLR, WO-MUM
    mock_users = {
        # ============== SUPERADMIN (2) ==============
        "admin": {
            "user_id": "user-001",
            "username": "admin",
            "email": "admin@bettervision.in",
            "password_hash": hash_password("admin123"),
            "full_name": "System Administrator",
            "roles": ["SUPERADMIN"],
            "store_ids": ["BV-DEL", "BV-NOI", "BV-GUR", "BV-MUM", "BV-BLR", "WO-MUM"],
            "is_active": True
        },
        "avinash.ceo": {
            "user_id": "user-002",
            "username": "avinash.ceo",
            "email": "avinash@bettervision.in",
            "password_hash": hash_password("Ceo@2024"),
            "full_name": "Avinash Kumar (CEO)",
            "roles": ["ADMIN"],
            "store_ids": ["BV-DEL", "BV-NOI", "BV-GUR", "BV-MUM", "BV-BLR", "WO-MUM"],
            "is_active": True
        },
        # ============== ADMIN / DIRECTORS (2) ==============
        "director1": {
            "user_id": "user-003",
            "username": "director1",
            "email": "rajiv.sharma@bettervision.in",
            "password_hash": hash_password("Dir@2024"),
            "full_name": "Rajiv Sharma (Director - Operations)",
            "roles": ["ADMIN"],
            "store_ids": ["BV-DEL", "BV-NOI", "BV-GUR", "BV-MUM", "BV-BLR"],
            "is_active": True
        },
        "director2": {
            "user_id": "user-004",
            "username": "director2",
            "email": "priya.singh@bettervision.in",
            "password_hash": hash_password("Dir@2024"),
            "full_name": "Priya Singh (Director - Finance)",
            "roles": ["ADMIN"],
            "store_ids": ["BV-DEL", "BV-NOI", "BV-GUR", "BV-MUM", "BV-BLR"],
            "is_active": True
        },
        # ============== AREA MANAGERS (2) ==============
        "area.north": {
            "user_id": "user-005",
            "username": "area.north",
            "email": "amit.verma@bettervision.in",
            "password_hash": hash_password("Area@2024"),
            "full_name": "Amit Verma (Area Manager - North)",
            "roles": ["AREA_MANAGER"],
            "store_ids": ["BV-DEL", "BV-NOI", "BV-GUR"],
            "is_active": True
        },
        "area.west": {
            "user_id": "user-006",
            "username": "area.west",
            "email": "sneha.patel@bettervision.in",
            "password_hash": hash_password("Area@2024"),
            "full_name": "Sneha Patel (Area Manager - West)",
            "roles": ["AREA_MANAGER"],
            "store_ids": ["BV-MUM", "WO-MUM"],
            "is_active": True
        },
        # ============== STORE MANAGERS (6) ==============
        "rajesh.manager": {
            "user_id": "user-007",
            "username": "rajesh.manager",
            "email": "rajesh.kumar@bettervision.in",
            "password_hash": hash_password("Store@2024"),
            "full_name": "Rajesh Kumar",
            "roles": ["STORE_MANAGER"],
            "store_ids": ["BV-DEL"],
            "is_active": True
        },
        "neha.manager": {
            "user_id": "user-008",
            "username": "neha.manager",
            "email": "neha.gupta@bettervision.in",
            "password_hash": hash_password("Store@2024"),
            "full_name": "Neha Gupta",
            "roles": ["STORE_MANAGER"],
            "store_ids": ["BV-NOI"],
            "is_active": True
        },
        "vikram.manager": {
            "user_id": "user-009",
            "username": "vikram.manager",
            "email": "vikram.singh@bettervision.in",
            "password_hash": hash_password("Store@2024"),
            "full_name": "Vikram Singh",
            "roles": ["STORE_MANAGER", "OPTOMETRIST"],
            "store_ids": ["BV-GUR"],
            "is_active": True
        },
        "pooja.manager": {
            "user_id": "user-010",
            "username": "pooja.manager",
            "email": "pooja.shah@bettervision.in",
            "password_hash": hash_password("Store@2024"),
            "full_name": "Pooja Shah",
            "roles": ["STORE_MANAGER"],
            "store_ids": ["BV-MUM"],
            "is_active": True
        },
        "arjun.manager": {
            "user_id": "user-011",
            "username": "arjun.manager",
            "email": "arjun.reddy@bettervision.in",
            "password_hash": hash_password("Store@2024"),
            "full_name": "Arjun Reddy",
            "roles": ["STORE_MANAGER", "OPTOMETRIST"],
            "store_ids": ["BV-BLR"],
            "is_active": True
        },
        "deepak.workshop": {
            "user_id": "user-012",
            "username": "deepak.workshop",
            "email": "deepak.mishra@wizopt.in",
            "password_hash": hash_password("Store@2024"),
            "full_name": "Deepak Mishra (Workshop Manager)",
            "roles": ["STORE_MANAGER"],
            "store_ids": ["WO-MUM"],
            "is_active": True
        },
        # ============== ACCOUNTANTS (2) ==============
        "accountant.delhi": {
            "user_id": "user-013",
            "username": "accountant.delhi",
            "email": "suresh.agarwal@bettervision.in",
            "password_hash": hash_password("Acc@2024"),
            "full_name": "Suresh Agarwal",
            "roles": ["ACCOUNTANT"],
            "store_ids": ["BV-DEL"],
            "is_active": True
        },
        "accountant.mumbai": {
            "user_id": "user-014",
            "username": "accountant.mumbai",
            "email": "meera.joshi@bettervision.in",
            "password_hash": hash_password("Acc@2024"),
            "full_name": "Meera Joshi",
            "roles": ["ACCOUNTANT"],
            "store_ids": ["BV-MUM"],
            "is_active": True
        },
        # ============== CATALOG MANAGER (1) ==============
        "catalog.admin": {
            "user_id": "user-015",
            "username": "catalog.admin",
            "email": "rohit.malhotra@bettervision.in",
            "password_hash": hash_password("Cat@2024"),
            "full_name": "Rohit Malhotra",
            "roles": ["CATALOG_MANAGER"],
            "store_ids": ["BV-DEL", "BV-NOI", "BV-GUR", "BV-MUM", "BV-BLR", "WO-MUM"],
            "is_active": True
        },
        # ============== HEAD OPTOMETRIST (1) ==============
        "dr.sharma": {
            "user_id": "user-016",
            "username": "dr.sharma",
            "email": "dr.anita.sharma@bettervision.in",
            "password_hash": hash_password("Opt@2024"),
            "full_name": "Dr. Anita Sharma (Head Optometrist)",
            "roles": ["OPTOMETRIST"],
            "store_ids": ["BV-DEL", "BV-NOI", "BV-GUR", "BV-MUM", "BV-BLR"],
            "is_active": True
        },
        # ============== DELHI SALES STAFF (3) ==============
        "sales.delhi1": {
            "user_id": "user-017",
            "username": "sales.delhi1",
            "email": "ravi.sharma@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Ravi Sharma",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-DEL"],
            "is_active": True
        },
        "sales.delhi2": {
            "user_id": "user-018",
            "username": "sales.delhi2",
            "email": "anjali.verma@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Anjali Verma",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-DEL"],
            "is_active": True
        },
        "sales.delhi3": {
            "user_id": "user-019",
            "username": "sales.delhi3",
            "email": "karan.mehta@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Karan Mehta",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-DEL"],
            "is_active": True
        },
        # ============== NOIDA SALES STAFF (3) ==============
        "sales.noida1": {
            "user_id": "user-020",
            "username": "sales.noida1",
            "email": "simran.kaur@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Simran Kaur",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-NOI"],
            "is_active": True
        },
        "sales.noida2": {
            "user_id": "user-021",
            "username": "sales.noida2",
            "email": "rahul.jain@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Rahul Jain",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-NOI"],
            "is_active": True
        },
        "sales.noida3": {
            "user_id": "user-022",
            "username": "sales.noida3",
            "email": "priyanka.mishra@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Priyanka Mishra",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-NOI"],
            "is_active": True
        },
        # ============== GURGAON SALES STAFF (2) ==============
        "sales.gurgaon1": {
            "user_id": "user-023",
            "username": "sales.gurgaon1",
            "email": "aditya.singh@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Aditya Singh",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-GUR"],
            "is_active": True
        },
        "sales.gurgaon2": {
            "user_id": "user-024",
            "username": "sales.gurgaon2",
            "email": "kavita.rani@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Kavita Rani",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-GUR"],
            "is_active": True
        },
        # ============== WIZOPT SALES STAFF (2) ==============
        "sales.wizopt1": {
            "user_id": "user-025",
            "username": "sales.wizopt1",
            "email": "sanjay.kumar@wizopt.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Sanjay Kumar",
            "roles": ["SALES_STAFF"],
            "store_ids": ["WO-MUM"],
            "is_active": True
        },
        "sales.wizopt2": {
            "user_id": "user-026",
            "username": "sales.wizopt2",
            "email": "nisha.patel@wizopt.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Nisha Patel",
            "roles": ["SALES_STAFF"],
            "store_ids": ["WO-MUM"],
            "is_active": True
        },
        # ============== CASHIERS (6) ==============
        "cashier.delhi": {
            "user_id": "user-027",
            "username": "cashier.delhi",
            "email": "mohan.lal@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Mohan Lal",
            "roles": ["CASHIER"],
            "store_ids": ["BV-DEL"],
            "is_active": True
        },
        "cashier.noida": {
            "user_id": "user-028",
            "username": "cashier.noida",
            "email": "sunita.devi@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Sunita Devi",
            "roles": ["CASHIER"],
            "store_ids": ["BV-NOI"],
            "is_active": True
        },
        "cashier.gurgaon": {
            "user_id": "user-029",
            "username": "cashier.gurgaon",
            "email": "ramesh.kumar@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Ramesh Kumar",
            "roles": ["CASHIER"],
            "store_ids": ["BV-GUR"],
            "is_active": True
        },
        "cashier.mumbai": {
            "user_id": "user-030",
            "username": "cashier.mumbai",
            "email": "lakshmi.iyer@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Lakshmi Iyer",
            "roles": ["CASHIER"],
            "store_ids": ["BV-MUM"],
            "is_active": True
        },
        "cashier.bangalore": {
            "user_id": "user-031",
            "username": "cashier.bangalore",
            "email": "venkat.rao@bettervision.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Venkat Rao",
            "roles": ["CASHIER"],
            "store_ids": ["BV-BLR"],
            "is_active": True
        },
        "cashier.wizopt": {
            "user_id": "user-032",
            "username": "cashier.wizopt",
            "email": "prakash.sharma@wizopt.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Prakash Sharma",
            "roles": ["CASHIER"],
            "store_ids": ["WO-MUM"],
            "is_active": True
        },
        # ============== WORKSHOP STAFF (4) ==============
        "workshop.tech1": {
            "user_id": "user-033",
            "username": "workshop.tech1",
            "email": "vinod.yadav@wizopt.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Vinod Yadav",
            "roles": ["WORKSHOP_STAFF"],
            "store_ids": ["WO-MUM"],
            "is_active": True
        },
        "workshop.tech2": {
            "user_id": "user-034",
            "username": "workshop.tech2",
            "email": "rajiv.gupta@wizopt.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Rajiv Gupta",
            "roles": ["WORKSHOP_STAFF"],
            "store_ids": ["WO-MUM"],
            "is_active": True
        },
        "workshop.tech3": {
            "user_id": "user-035",
            "username": "workshop.tech3",
            "email": "manoj.singh@wizopt.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Manoj Singh",
            "roles": ["WORKSHOP_STAFF"],
            "store_ids": ["WO-MUM"],
            "is_active": True
        },
        "workshop.tech4": {
            "user_id": "user-036",
            "username": "workshop.tech4",
            "email": "sunil.mishra@wizopt.in",
            "password_hash": hash_password("Staff@2024"),
            "full_name": "Sunil Mishra",
            "roles": ["WORKSHOP_STAFF"],
            "store_ids": ["WO-MUM"],
            "is_active": True
        },
        # ============== TEST USERS ==============
        "store_manager": {
            "user_id": "user-037",
            "username": "store_manager",
            "email": "store.manager@bettervision.in",
            "password_hash": hash_password("admin123"),
            "full_name": "Store Manager (Test)",
            "roles": ["STORE_MANAGER"],
            "store_ids": ["BV-DEL"],
            "is_active": True
        },
        "optometrist": {
            "user_id": "user-038",
            "username": "optometrist",
            "email": "optometrist@bettervision.in",
            "password_hash": hash_password("admin123"),
            "full_name": "Optometrist (Test)",
            "roles": ["OPTOMETRIST"],
            "store_ids": ["BV-DEL"],
            "is_active": True
        }
    }
    
    # Find user by username first, then by email
    login_input = request.username.lower().strip()
    user = mock_users.get(login_input)

    # If not found by username, search by email
    if not user:
        for u in mock_users.values():
            if u.get("email", "").lower() == login_input:
                user = u
                break

    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="User account is disabled")
    
    # Validate store access if store_id provided
    active_store = request.store_id
    if active_store and active_store not in user["store_ids"]:
        # Allow ADMIN/SUPERADMIN to access any store
        if not any(r in ["ADMIN", "SUPERADMIN"] for r in user["roles"]):
            raise HTTPException(status_code=403, detail="No access to this store")
    
    # Create token
    token_data = {
        "user_id": user["user_id"],
        "username": user["username"],
        "roles": user["roles"],
        "store_ids": user["store_ids"],
        "active_store_id": active_store or user["store_ids"][0] if user["store_ids"] else None
    }
    
    access_token = create_access_token(token_data)
    
    return LoginResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "user_id": user["user_id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "roles": user["roles"],
            "store_ids": user["store_ids"],
            "active_store_id": token_data["active_store_id"]
        }
    )


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """
    Logout user (invalidate token)
    """
    # In production, add token to blacklist
    return {"message": "Successfully logged out"}


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Get current user info from token
    """
    return current_user


@router.post("/refresh")
async def refresh_token(request: RefreshTokenRequest):
    """
    Refresh access token
    """
    payload = decode_token(request.token)
    
    # Create new token
    token_data = {
        "user_id": payload["user_id"],
        "username": payload["username"],
        "roles": payload["roles"],
        "store_ids": payload["store_ids"],
        "active_store_id": payload.get("active_store_id")
    }
    
    new_token = create_access_token(token_data)
    
    return {
        "access_token": new_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest, 
    current_user: dict = Depends(get_current_user)
):
    """
    Change user password
    """
    # TODO: Verify current password and update in database
    return {"message": "Password changed successfully"}


@router.post("/switch-store/{store_id}")
async def switch_store(store_id: str, current_user: dict = Depends(get_current_user)):
    """
    Switch active store context
    """
    if store_id not in current_user["store_ids"]:
        if not any(r in ["ADMIN", "SUPERADMIN"] for r in current_user["roles"]):
            raise HTTPException(status_code=403, detail="No access to this store")
    
    # Create new token with updated store
    token_data = {
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "roles": current_user["roles"],
        "store_ids": current_user["store_ids"],
        "active_store_id": store_id
    }
    
    new_token = create_access_token(token_data)
    
    return {
        "access_token": new_token,
        "active_store_id": store_id
    }
