"""
IMS 2.0 - Database Connection Layer
====================================
MongoDB connection management with connection pooling
"""
from typing import Optional, Dict, Any
from datetime import datetime
import os

# MongoDB driver
try:
    from pymongo import MongoClient
    from pymongo.database import Database
    from pymongo.collection import Collection
    from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False
    MongoClient = None
    Database = None
    Collection = None


class DatabaseConfig:
    """Database configuration"""
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 27017,
        database: str = "ims_2_0",
        username: str = None,
        password: str = None,
        auth_source: str = "admin",
        replica_set: str = None,
        ssl: bool = False,
        max_pool_size: int = 50,
        min_pool_size: int = 10,
        connect_timeout_ms: int = 5000,
        server_selection_timeout_ms: int = 5000
    ):
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password
        self.auth_source = auth_source
        self.replica_set = replica_set
        self.ssl = ssl
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size
        self.connect_timeout_ms = connect_timeout_ms
        self.server_selection_timeout_ms = server_selection_timeout_ms
    
    @classmethod
    def from_env(cls) -> 'DatabaseConfig':
        """Create config from environment variables"""
        return cls(
            host=os.getenv("MONGO_HOST", "localhost"),
            port=int(os.getenv("MONGO_PORT", "27017")),
            database=os.getenv("MONGO_DATABASE", "ims_2_0"),
            username=os.getenv("MONGO_USERNAME"),
            password=os.getenv("MONGO_PASSWORD"),
            auth_source=os.getenv("MONGO_AUTH_SOURCE", "admin"),
            replica_set=os.getenv("MONGO_REPLICA_SET"),
            ssl=os.getenv("MONGO_SSL", "false").lower() == "true",
            max_pool_size=int(os.getenv("MONGO_MAX_POOL_SIZE", "50")),
            min_pool_size=int(os.getenv("MONGO_MIN_POOL_SIZE", "10"))
        )
    
    @classmethod
    def from_uri(cls, uri: str, database: str = "ims_2_0") -> 'DatabaseConfig':
        """Create config from MongoDB URI"""
        config = cls()
        config._uri = uri
        config.database = database
        return config
    
    def get_uri(self) -> str:
        """Build MongoDB connection URI"""
        if hasattr(self, '_uri'):
            return self._uri
        
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"
        else:
            auth = ""
        
        uri = f"mongodb://{auth}{self.host}:{self.port}"
        
        params = []
        if self.auth_source and self.username:
            params.append(f"authSource={self.auth_source}")
        if self.replica_set:
            params.append(f"replicaSet={self.replica_set}")
        if self.ssl:
            params.append("ssl=true")
        params.append(f"maxPoolSize={self.max_pool_size}")
        params.append(f"minPoolSize={self.min_pool_size}")
        params.append(f"connectTimeoutMS={self.connect_timeout_ms}")
        params.append(f"serverSelectionTimeoutMS={self.server_selection_timeout_ms}")
        
        if params:
            uri += "?" + "&".join(params)
        
        return uri


class DatabaseConnection:
    """
    MongoDB Connection Manager
    Singleton pattern for connection pooling
    """
    
    _instance: Optional['DatabaseConnection'] = None
    _client: Optional[MongoClient] = None
    _db: Optional[Database] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self._config: Optional[DatabaseConfig] = None
        self._connected = False
    
    def configure(self, config: DatabaseConfig):
        """Configure database connection"""
        self._config = config
    
    def connect(self) -> bool:
        """Establish database connection"""
        if not MONGO_AVAILABLE:
            print("⚠️ PyMongo not installed. Running in mock mode.")
            self._connected = False
            return False
        
        if self._connected and self._client:
            return True
        
        if not self._config:
            self._config = DatabaseConfig.from_env()
        
        try:
            self._client = MongoClient(self._config.get_uri())
            # Test connection
            self._client.admin.command('ping')
            self._db = self._client[self._config.database]
            self._connected = True
            print(f"✅ Connected to MongoDB: {self._config.database}")
            return True
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            print(f"❌ MongoDB connection failed: {e}")
            self._connected = False
            return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            self._connected = False
            return False
    
    def disconnect(self):
        """Close database connection"""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
            self._connected = False
            print("🔌 Disconnected from MongoDB")
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property
    def db(self) -> Optional[Database]:
        """Get database instance"""
        if not self._connected:
            self.connect()
        return self._db
    
    def get_collection(self, name: str) -> Optional[Collection]:
        """Get a collection by name"""
        if self.db:
            return self.db[name]
        return None
    
    # Collection shortcuts
    @property
    def users(self) -> Optional[Collection]:
        return self.get_collection("users")
    
    @property
    def stores(self) -> Optional[Collection]:
        return self.get_collection("stores")
    
    @property
    def products(self) -> Optional[Collection]:
        return self.get_collection("products")
    
    @property
    def stock_units(self) -> Optional[Collection]:
        return self.get_collection("stock_units")
    
    @property
    def orders(self) -> Optional[Collection]:
        return self.get_collection("orders")
    
    @property
    def customers(self) -> Optional[Collection]:
        return self.get_collection("customers")
    
    @property
    def prescriptions(self) -> Optional[Collection]:
        return self.get_collection("prescriptions")
    
    @property
    def vendors(self) -> Optional[Collection]:
        return self.get_collection("vendors")
    
    @property
    def purchase_orders(self) -> Optional[Collection]:
        return self.get_collection("purchase_orders")
    
    @property
    def grns(self) -> Optional[Collection]:
        return self.get_collection("grns")
    
    @property
    def tasks(self) -> Optional[Collection]:
        return self.get_collection("tasks")
    
    @property
    def expenses(self) -> Optional[Collection]:
        return self.get_collection("expenses")
    
    @property
    def advances(self) -> Optional[Collection]:
        return self.get_collection("advances")
    
    @property
    def audit_logs(self) -> Optional[Collection]:
        return self.get_collection("audit_logs")
    
    @property
    def notifications(self) -> Optional[Collection]:
        return self.get_collection("notifications")


# Global instance
db = DatabaseConnection()


def get_db() -> DatabaseConnection:
    """Get database connection instance"""
    return db


def init_db(config: DatabaseConfig = None) -> bool:
    """Initialize database connection"""
    if config:
        db.configure(config)
    return db.connect()


def close_db():
    """Close database connection"""
    db.disconnect()


# For testing without MongoDB
class MockCursor:
    """Mock cursor for chaining operations like MongoDB"""

    def __init__(self, data: list):
        self._data = data
        self._sort_key = None
        self._skip = 0
        self._limit = None

    def sort(self, sort_spec):
        """Sort the results"""
        if sort_spec and len(sort_spec) > 0:
            field, direction = sort_spec[0] if isinstance(sort_spec[0], tuple) else (sort_spec[0], 1)
            reverse = direction == -1
            self._data = sorted(self._data, key=lambda x: x.get(field, ""), reverse=reverse)
        return self

    def skip(self, n: int):
        """Skip n documents"""
        self._skip = n
        return self

    def limit(self, n: int):
        """Limit to n documents"""
        self._limit = n
        return self

    def __iter__(self):
        data = self._data[self._skip:]
        if self._limit:
            data = data[:self._limit]
        return iter(data)

    def __list__(self):
        return list(self.__iter__())


class MockCollection:
    """Mock collection for testing without MongoDB"""

    def __init__(self, name: str):
        self.name = name
        self._data: Dict[str, Dict] = {}

    def insert_one(self, document: Dict) -> Any:
        doc_id = document.get("_id") or str(len(self._data) + 1)
        document["_id"] = doc_id
        # Make a copy to avoid mutation issues
        self._data[doc_id] = dict(document)
        return type('obj', (object,), {'inserted_id': doc_id})()

    def insert_many(self, documents: list) -> Any:
        inserted_ids = []
        for doc in documents:
            result = self.insert_one(doc)
            inserted_ids.append(result.inserted_id)
        return type('obj', (object,), {'inserted_ids': inserted_ids})()

    def _matches_filter(self, doc: Dict, filter: Dict) -> bool:
        """Check if document matches the filter"""
        if not filter:
            return True

        for key, value in filter.items():
            if key == "$or":
                # Handle $or operator
                if not any(self._matches_filter(doc, cond) for cond in value):
                    return False
            elif key == "$and":
                # Handle $and operator
                if not all(self._matches_filter(doc, cond) for cond in value):
                    return False
            elif isinstance(value, dict):
                # Handle operators like $regex, $gt, $lt, etc.
                doc_value = doc.get(key, "")
                for op, op_value in value.items():
                    if op == "$regex":
                        import re
                        flags = re.IGNORECASE if value.get("$options") == "i" else 0
                        if not re.search(op_value, str(doc_value), flags):
                            return False
                    elif op == "$gt":
                        if not (doc_value > op_value):
                            return False
                    elif op == "$lt":
                        if not (doc_value < op_value):
                            return False
                    elif op == "$gte":
                        if not (doc_value >= op_value):
                            return False
                    elif op == "$lte":
                        if not (doc_value <= op_value):
                            return False
                    elif op == "$in":
                        if doc_value not in op_value:
                            return False
                    elif op == "$ne":
                        if doc_value == op_value:
                            return False
            else:
                # Direct equality check
                if doc.get(key) != value:
                    return False
        return True

    def find_one(self, filter: Dict) -> Optional[Dict]:
        if not filter:
            return next(iter(self._data.values()), None)

        # Direct _id lookup
        if "_id" in filter and len(filter) == 1:
            return self._data.get(filter["_id"])

        for doc in self._data.values():
            if self._matches_filter(doc, filter):
                return doc
        return None

    def find(self, filter: Dict = None) -> MockCursor:
        if not filter:
            return MockCursor(list(self._data.values()))

        results = [doc for doc in self._data.values() if self._matches_filter(doc, filter)]
        return MockCursor(results)

    def update_one(self, filter: Dict, update: Dict) -> Any:
        doc = self.find_one(filter)
        if doc:
            if "$set" in update:
                doc.update(update["$set"])
            if "$inc" in update:
                for field, amount in update["$inc"].items():
                    doc[field] = doc.get(field, 0) + amount
            if "$push" in update:
                for field, value in update["$push"].items():
                    if field not in doc:
                        doc[field] = []
                    doc[field].append(value)
            return type('obj', (object,), {'modified_count': 1})()
        return type('obj', (object,), {'modified_count': 0})()

    def update_many(self, filter: Dict, update: Dict) -> Any:
        count = 0
        for doc in self._data.values():
            if self._matches_filter(doc, filter):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$inc" in update:
                    for field, amount in update["$inc"].items():
                        doc[field] = doc.get(field, 0) + amount
                count += 1
        return type('obj', (object,), {'modified_count': count})()

    def delete_one(self, filter: Dict) -> Any:
        doc = self.find_one(filter)
        if doc and doc.get("_id") in self._data:
            del self._data[doc["_id"]]
            return type('obj', (object,), {'deleted_count': 1})()
        return type('obj', (object,), {'deleted_count': 0})()

    def delete_many(self, filter: Dict = None) -> Any:
        if not filter:
            count = len(self._data)
            self._data.clear()
            return type('obj', (object,), {'deleted_count': count})()

        to_delete = [doc["_id"] for doc in self._data.values() if self._matches_filter(doc, filter)]
        for doc_id in to_delete:
            del self._data[doc_id]
        return type('obj', (object,), {'deleted_count': len(to_delete)})()

    def count_documents(self, filter: Dict = None) -> int:
        if not filter:
            return len(self._data)
        return len([doc for doc in self._data.values() if self._matches_filter(doc, filter)])

    def aggregate(self, pipeline: list) -> list:
        """Basic aggregation support - just returns all documents for now"""
        # This is a simplified implementation
        return list(self._data.values())


class MockDatabase:
    """Mock database for testing without MongoDB"""
    
    def __init__(self):
        self._collections: Dict[str, MockCollection] = {}
    
    def __getitem__(self, name: str) -> MockCollection:
        if name not in self._collections:
            self._collections[name] = MockCollection(name)
        return self._collections[name]
    
    def list_collection_names(self) -> list:
        return list(self._collections.keys())


def get_mock_db() -> MockDatabase:
    """Get mock database for testing"""
    return MockDatabase()


# Seeded Mock Database singleton
_seeded_mock_db = None


def get_seeded_mock_db() -> MockDatabase:
    """Get a mock database pre-seeded with sample data"""
    global _seeded_mock_db
    if _seeded_mock_db is None:
        _seeded_mock_db = MockDatabase()
        try:
            from .seed_data import get_all_seed_data
            seed_data = get_all_seed_data()
            for collection_name, data in seed_data.items():
                collection = _seeded_mock_db[collection_name]
                for doc in data:
                    collection.insert_one(doc)
            print(f"✅ Seeded mock database with sample data")
        except ImportError as e:
            print(f"⚠️ Could not load seed data: {e}")
    return _seeded_mock_db


class SeededDatabaseConnection:
    """
    Database connection that falls back to seeded mock data
    when MongoDB is not available
    """
    _instance = None
    _mock_db = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self._real_db = db  # Use the real database connection
        self._use_mock = False

    @property
    def is_connected(self) -> bool:
        if self._real_db.is_connected:
            return True
        # If not connected to real DB, use mock
        self._use_mock = True
        return True  # Mock is always "connected"

    @property
    def db(self):
        if self._real_db.is_connected:
            return self._real_db.db
        return get_seeded_mock_db()

    def get_collection(self, name: str):
        if self._real_db.is_connected:
            return self._real_db.get_collection(name)
        return get_seeded_mock_db()[name]

    # Collection shortcuts
    @property
    def users(self):
        return self.get_collection("users")

    @property
    def stores(self):
        return self.get_collection("stores")

    @property
    def products(self):
        return self.get_collection("products")

    @property
    def stock_units(self):
        return self.get_collection("stock_units")

    @property
    def orders(self):
        return self.get_collection("orders")

    @property
    def customers(self):
        return self.get_collection("customers")

    @property
    def prescriptions(self):
        return self.get_collection("prescriptions")

    @property
    def vendors(self):
        return self.get_collection("vendors")

    @property
    def purchase_orders(self):
        return self.get_collection("purchase_orders")

    @property
    def grns(self):
        return self.get_collection("grns")

    @property
    def tasks(self):
        return self.get_collection("tasks")

    @property
    def expenses(self):
        return self.get_collection("expenses")

    @property
    def advances(self):
        return self.get_collection("advances")

    @property
    def audit_logs(self):
        return self.get_collection("audit_logs")

    @property
    def notifications(self):
        return self.get_collection("notifications")


# Seeded database instance
seeded_db = SeededDatabaseConnection()


def get_seeded_db() -> SeededDatabaseConnection:
    """Get database connection with fallback to seeded mock data"""
    return seeded_db


if __name__ == "__main__":
    print("=" * 60)
    print("IMS 2.0 DATABASE CONNECTION TEST")
    print("=" * 60)
    
    # Test mock database
    print("\n📦 Testing Mock Database")
    mock_db = get_mock_db()
    
    # Insert
    users = mock_db["users"]
    result = users.insert_one({"name": "Rahul", "role": "SALES_STAFF"})
    print(f"  Inserted: {result.inserted_id}")
    
    # Find
    user = users.find_one({"name": "Rahul"})
    print(f"  Found: {user}")
    
    # Update
    users.update_one({"name": "Rahul"}, {"$set": {"role": "STORE_MANAGER"}})
    user = users.find_one({"name": "Rahul"})
    print(f"  Updated: {user}")
    
    # Count
    count = users.count_documents({})
    print(f"  Count: {count}")
    
    print("\n" + "=" * 60)
