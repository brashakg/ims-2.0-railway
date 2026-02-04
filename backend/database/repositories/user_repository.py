"""
IMS 2.0 - User Repository
==========================
User data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime
from .base_repository import BaseRepository


class UserRepository(BaseRepository):
    """Repository for User operations"""
    
    @property
    def entity_name(self) -> str:
        return "User"
    
    @property
    def id_field(self) -> str:
        return "user_id"
    
    # =========================================================================
    # User-specific queries
    # =========================================================================
    
    def find_by_username(self, username: str) -> Optional[Dict]:
        """Find user by username"""
        return self.find_one({"username": username})
    
    def find_by_email(self, email: str) -> Optional[Dict]:
        """Find user by email"""
        return self.find_one({"email": email})
    
    def find_by_store(self, store_id: str, active_only: bool = True) -> List[Dict]:
        """Find users in a store"""
        filter = {"store_ids": store_id}
        if active_only:
            filter["is_active"] = True
        return self.find_many(filter)
    
    def find_by_role(self, role: str, store_id: str = None) -> List[Dict]:
        """Find users by role"""
        filter = {"roles": role, "is_active": True}
        if store_id:
            filter["store_ids"] = store_id
        return self.find_many(filter)
    
    def find_optometrists(self, store_id: str = None) -> List[Dict]:
        """Find all optometrists"""
        return self.find_by_role("OPTOMETRIST", store_id)
    
    def find_managers(self, store_id: str = None) -> List[Dict]:
        """Find all managers"""
        return self.find_by_role("STORE_MANAGER", store_id)
    
    def find_sales_staff(self, store_id: str) -> List[Dict]:
        """Find sales staff in store"""
        return self.find_many({
            "store_ids": store_id,
            "roles": {"$in": ["SALES_STAFF", "CASHIER"]},
            "is_active": True
        })
    
    # =========================================================================
    # Authentication
    # =========================================================================
    
    def authenticate(self, username: str, password_hash: str) -> Optional[Dict]:
        """
        Authenticate user (password should already be hashed)
        """
        return self.find_one({
            "username": username,
            "password_hash": password_hash,
            "is_active": True
        })
    
    def update_last_login(self, user_id: str) -> bool:
        """Update last login timestamp"""
        return self.update(user_id, {"last_login": datetime.now()})
    
    def update_password(self, user_id: str, password_hash: str) -> bool:
        """Update user password"""
        return self.update(user_id, {
            "password_hash": password_hash,
            "password_changed_at": datetime.now()
        })
    
    # =========================================================================
    # Role Management
    # =========================================================================
    
    def add_role(self, user_id: str, role: str) -> bool:
        """Add role to user"""
        try:
            self.collection.update_one(
                {"user_id": user_id},
                {"$addToSet": {"roles": role}}
            )
            return True
        except:
            return False
    
    def remove_role(self, user_id: str, role: str) -> bool:
        """Remove role from user"""
        try:
            self.collection.update_one(
                {"user_id": user_id},
                {"$pull": {"roles": role}}
            )
            return True
        except:
            return False
    
    def add_store(self, user_id: str, store_id: str) -> bool:
        """Add store access to user"""
        try:
            self.collection.update_one(
                {"user_id": user_id},
                {"$addToSet": {"store_ids": store_id}}
            )
            return True
        except:
            return False
    
    def remove_store(self, user_id: str, store_id: str) -> bool:
        """Remove store access from user"""
        try:
            self.collection.update_one(
                {"user_id": user_id},
                {"$pull": {"store_ids": store_id}}
            )
            return True
        except:
            return False
    
    # =========================================================================
    # Queries
    # =========================================================================
    
    def search_users(self, query: str, store_id: str = None) -> List[Dict]:
        """Search users by name, email, or username"""
        return self.search(query, ["full_name", "username", "email"], 
                          {"store_ids": store_id} if store_id else None)
    
    def get_user_summary(self, store_id: str = None) -> Dict:
        """Get user summary statistics"""
        filter = {"store_ids": store_id} if store_id else {}
        
        pipeline = [
            {"$match": filter},
            {"$unwind": "$roles"},
            {"$group": {
                "_id": "$roles",
                "count": {"$sum": 1},
                "active": {"$sum": {"$cond": ["$is_active", 1, 0]}}
            }}
        ]
        
        results = self.aggregate(pipeline)
        return {r["_id"]: {"total": r["count"], "active": r["active"]} for r in results}
