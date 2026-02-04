"""
IMS 2.0 - Base Repository
==========================
Abstract base class for all repositories
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, TypeVar, Generic
from datetime import datetime
import uuid

T = TypeVar('T')


class BaseRepository(ABC, Generic[T]):
    """
    Abstract base repository with common CRUD operations
    """
    
    def __init__(self, collection):
        """
        Initialize repository with a MongoDB collection
        
        Args:
            collection: MongoDB collection or MockCollection instance
        """
        self.collection = collection
    
    @property
    @abstractmethod
    def entity_name(self) -> str:
        """Name of the entity (for logging)"""
        pass
    
    @property
    @abstractmethod
    def id_field(self) -> str:
        """Primary ID field name"""
        pass
    
    def _generate_id(self) -> str:
        """Generate unique ID"""
        return str(uuid.uuid4())
    
    def _add_timestamps(self, data: Dict, is_update: bool = False) -> Dict:
        """Add created_at/updated_at timestamps"""
        now = datetime.now()
        if not is_update:
            data["created_at"] = now
        data["updated_at"] = now
        return data
    
    # =========================================================================
    # CRUD Operations
    # =========================================================================
    
    def create(self, data: Dict) -> Optional[Dict]:
        """
        Create new document
        
        Args:
            data: Document data
            
        Returns:
            Created document with ID
        """
        try:
            # Generate ID if not provided
            if self.id_field not in data:
                data[self.id_field] = self._generate_id()
            
            # Add timestamps
            data = self._add_timestamps(data)
            
            # Set _id to match our ID field
            data["_id"] = data[self.id_field]
            
            result = self.collection.insert_one(data)
            return data
        except Exception as e:
            print(f"Error creating {self.entity_name}: {e}")
            return None
    
    def find_by_id(self, id: str) -> Optional[Dict]:
        """
        Find document by ID
        
        Args:
            id: Document ID
            
        Returns:
            Document or None
        """
        try:
            return self.collection.find_one({self.id_field: id})
        except Exception as e:
            print(f"Error finding {self.entity_name}: {e}")
            return None
    
    def find_one(self, filter: Dict) -> Optional[Dict]:
        """
        Find single document by filter
        
        Args:
            filter: MongoDB filter
            
        Returns:
            Document or None
        """
        try:
            return self.collection.find_one(filter)
        except Exception as e:
            print(f"Error finding {self.entity_name}: {e}")
            return None
    
    def find_many(
        self, 
        filter: Dict = None, 
        sort: List[tuple] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[Dict]:
        """
        Find multiple documents
        
        Args:
            filter: MongoDB filter
            sort: Sort specification [(field, direction)]
            skip: Number to skip
            limit: Maximum to return
            
        Returns:
            List of documents
        """
        try:
            cursor = self.collection.find(filter or {})
            
            if sort:
                cursor = cursor.sort(sort)
            if skip:
                cursor = cursor.skip(skip)
            if limit:
                cursor = cursor.limit(limit)
            
            return list(cursor)
        except Exception as e:
            print(f"Error finding {self.entity_name}s: {e}")
            return []
    
    def update(self, id: str, data: Dict) -> bool:
        """
        Update document by ID
        
        Args:
            id: Document ID
            data: Fields to update
            
        Returns:
            Success boolean
        """
        try:
            # Add updated timestamp
            data["updated_at"] = datetime.now()
            
            result = self.collection.update_one(
                {self.id_field: id},
                {"$set": data}
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"Error updating {self.entity_name}: {e}")
            return False
    
    def delete(self, id: str) -> bool:
        """
        Delete document by ID (hard delete)
        
        Args:
            id: Document ID
            
        Returns:
            Success boolean
        """
        try:
            result = self.collection.delete_one({self.id_field: id})
            return result.deleted_count > 0
        except Exception as e:
            print(f"Error deleting {self.entity_name}: {e}")
            return False
    
    def soft_delete(self, id: str) -> bool:
        """
        Soft delete by setting is_active=False
        
        Args:
            id: Document ID
            
        Returns:
            Success boolean
        """
        return self.update(id, {"is_active": False, "deleted_at": datetime.now()})
    
    def count(self, filter: Dict = None) -> int:
        """
        Count documents
        
        Args:
            filter: MongoDB filter
            
        Returns:
            Count
        """
        try:
            return self.collection.count_documents(filter or {})
        except Exception as e:
            print(f"Error counting {self.entity_name}s: {e}")
            return 0
    
    def exists(self, filter: Dict) -> bool:
        """
        Check if document exists
        
        Args:
            filter: MongoDB filter
            
        Returns:
            Exists boolean
        """
        return self.find_one(filter) is not None
    
    # =========================================================================
    # Bulk Operations
    # =========================================================================
    
    def create_many(self, documents: List[Dict]) -> List[Dict]:
        """
        Create multiple documents
        
        Args:
            documents: List of documents
            
        Returns:
            List of created documents
        """
        try:
            for doc in documents:
                if self.id_field not in doc:
                    doc[self.id_field] = self._generate_id()
                doc = self._add_timestamps(doc)
                doc["_id"] = doc[self.id_field]
            
            self.collection.insert_many(documents)
            return documents
        except Exception as e:
            print(f"Error bulk creating {self.entity_name}s: {e}")
            return []
    
    def update_many(self, filter: Dict, data: Dict) -> int:
        """
        Update multiple documents
        
        Args:
            filter: MongoDB filter
            data: Fields to update
            
        Returns:
            Number updated
        """
        try:
            data["updated_at"] = datetime.now()
            result = self.collection.update_many(filter, {"$set": data})
            return result.modified_count
        except Exception as e:
            print(f"Error bulk updating {self.entity_name}s: {e}")
            return 0
    
    # =========================================================================
    # Query Helpers
    # =========================================================================
    
    def find_active(self, filter: Dict = None) -> List[Dict]:
        """Find only active documents"""
        query = {"is_active": True}
        if filter:
            query.update(filter)
        return self.find_many(query)
    
    def search(self, text: str, fields: List[str], filter: Dict = None) -> List[Dict]:
        """
        Simple text search across fields
        
        Args:
            text: Search text
            fields: Fields to search
            filter: Additional filter
            
        Returns:
            Matching documents
        """
        try:
            # Build regex query
            regex = {"$regex": text, "$options": "i"}
            or_conditions = [{field: regex} for field in fields]
            
            query = {"$or": or_conditions}
            if filter:
                query = {"$and": [query, filter]}
            
            return self.find_many(query)
        except Exception as e:
            print(f"Error searching {self.entity_name}s: {e}")
            return []
    
    def aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        """
        Run aggregation pipeline
        
        Args:
            pipeline: Aggregation stages
            
        Returns:
            Aggregation results
        """
        try:
            return list(self.collection.aggregate(pipeline))
        except Exception as e:
            print(f"Error aggregating {self.entity_name}s: {e}")
            return []
