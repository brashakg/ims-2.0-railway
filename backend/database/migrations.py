"""
IMS 2.0 - Database Migrations
==============================
Collection creation, schema validation, and index management
"""
from typing import Dict, List, Optional
from datetime import datetime
import json

from .schemas import COLLECTIONS, get_all_schemas, get_all_indexes

try:
    from pymongo.database import Database
    from pymongo.errors import CollectionInvalid, OperationFailure
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False
    Database = None


class MigrationResult:
    """Result of a migration operation"""
    
    def __init__(self, success: bool, message: str, details: Dict = None):
        self.success = success
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now()
    
    def __str__(self):
        status = "✅" if self.success else "❌"
        return f"{status} {self.message}"


class DatabaseMigration:
    """
    Database Migration Manager
    """
    
    def __init__(self, db: Database = None):
        self.db = db
        self.results: List[MigrationResult] = []
    
    def run_all(self) -> List[MigrationResult]:
        """Run all migrations"""
        print("=" * 60)
        print("IMS 2.0 DATABASE MIGRATION")
        print("=" * 60)
        
        if not MONGO_AVAILABLE or not self.db:
            print("⚠️ MongoDB not available. Running in mock mode.")
            return self._run_mock_migrations()
        
        results = []
        
        # 1. Create collections with schema validation
        print("\n📦 Creating Collections...")
        for name, config in COLLECTIONS.items():
            result = self._create_collection(name, config["schema"])
            results.append(result)
            print(f"  {result}")
        
        # 2. Create indexes
        print("\n📊 Creating Indexes...")
        for name, indexes in get_all_indexes().items():
            for index_config in indexes:
                result = self._create_index(name, index_config)
                results.append(result)
                print(f"  {result}")

        # 2b. SSO jti single-use TTL index (Council Branch C). Auto-purges
        # expired sso_jti rows so the collection stays bounded.
        result = self._create_sso_jti_ttl_index()
        results.append(result)
        print(f"  {result}")

        # 3. Create default data
        print("\n📝 Creating Default Data...")
        result = self._create_default_data()
        results.append(result)
        print(f"  {result}")

        # 3b. Seed lens_enum_config (Branch B' sub-PR 1, Q6 defaults).
        # Idempotent: never overwrites owner edits. Only technical
        # dimensions are seeded (coatings, indexes, materials, lens_types);
        # brands + series start empty so the owner enters their own.
        result = self._seed_lens_enum_config()
        results.append(result)
        print(f"  {result}")
        
        # Summary
        success_count = len([r for r in results if r.success])
        print(f"\n✅ Completed: {success_count}/{len(results)} operations successful")
        
        self.results = results
        return results
    
    def _run_mock_migrations(self) -> List[MigrationResult]:
        """Run mock migrations for testing"""
        results = []
        
        for name in COLLECTIONS.keys():
            results.append(MigrationResult(True, f"Collection '{name}' (mock)"))
        
        print(f"✅ Mock migration complete: {len(results)} collections")
        return results
    
    def _create_collection(self, name: str, schema: Dict) -> MigrationResult:
        """Create collection with schema validation"""
        try:
            # Check if collection exists
            if name in self.db.list_collection_names():
                # Update validator
                self.db.command({
                    "collMod": name,
                    "validator": {"$jsonSchema": schema},
                    "validationLevel": "moderate"
                })
                return MigrationResult(True, f"Collection '{name}' validator updated")
            
            # Create new collection
            self.db.create_collection(
                name,
                validator={"$jsonSchema": schema},
                validationLevel="moderate"
            )
            return MigrationResult(True, f"Collection '{name}' created")
        
        except CollectionInvalid as e:
            return MigrationResult(False, f"Collection '{name}' invalid: {e}")
        except OperationFailure as e:
            return MigrationResult(False, f"Collection '{name}' failed: {e}")
        except Exception as e:
            return MigrationResult(False, f"Collection '{name}' error: {e}")
    
    def _create_index(self, collection_name: str, index_config: Dict) -> MigrationResult:
        """Create index on collection"""
        try:
            collection = self.db[collection_name]
            
            keys = index_config["keys"]
            unique = index_config.get("unique", False)
            sparse = index_config.get("sparse", False)
            
            index_name = collection.create_index(
                keys,
                unique=unique,
                sparse=sparse,
                background=True
            )
            
            key_names = [k[0] for k in keys]
            return MigrationResult(
                True, 
                f"Index on {collection_name}.{'+'.join(key_names)}",
                {"index_name": index_name}
            )
        
        except Exception as e:
            return MigrationResult(False, f"Index on {collection_name} failed: {e}")

    def _create_sso_jti_ttl_index(self) -> MigrationResult:
        """Create the TTL index on sso_jti.exp (Council Branch C).

        Mongo deletes a doc when its `exp` Date passes (expireAfterSeconds=0).
        Idempotent: pymongo create_index is a no-op if an identical index
        already exists with the same name.
        """
        try:
            collection = self.db["sso_jti"]
            index_name = collection.create_index(
                "exp",
                expireAfterSeconds=0,
                name="sso_jti_exp_ttl",
                background=True,
            )
            return MigrationResult(
                True,
                "TTL index on sso_jti.exp",
                {"index_name": index_name},
            )
        except Exception as e:
            return MigrationResult(False, f"TTL index on sso_jti failed: {e}")

    def _seed_lens_enum_config(self) -> MigrationResult:
        """Seed lens_enum_config defaults (Branch B' sub-PR 1, Q6).

        Idempotent: for each enum_type, INSERT only when the row doesn't
        already exist. Owner edits via PATCH /lens-enums/{enum_type} are
        preserved across restarts (the upsert from migrations never runs
        for a row that already exists).

        Only technical-dimension lists are seeded; brands + series start
        empty so the owner enters them via Settings.
        """
        try:
            # Import here so an early import error in the service file
            # cannot break the whole migration run.
            from api.services.lens_catalog_validation import DEFAULT_ENUM_ITEMS

            coll = self.db["lens_enum_config"]
            seeded = []
            for enum_type, items in DEFAULT_ENUM_ITEMS.items():
                if coll.count_documents({"enum_id": enum_type}) > 0:
                    # Already exists -- preserve owner edits.
                    continue
                coll.insert_one(
                    {
                        "enum_id": enum_type,
                        "items": list(items),
                        "updated_at": datetime.now(),
                        "updated_by": "system-migration",
                    }
                )
                seeded.append(enum_type)
            if seeded:
                return MigrationResult(
                    True,
                    "Seeded lens_enum_config: {seeded}".format(
                        seeded=", ".join(seeded)
                    ),
                )
            return MigrationResult(
                True, "lens_enum_config already populated (no seed needed)"
            )
        except Exception as e:
            return MigrationResult(
                False, "lens_enum_config seed failed: {e}".format(e=e)
            )

    def _create_default_data(self) -> MigrationResult:
        """Create default/seed data"""
        try:
            created = []
            
            # Default Superadmin
            if self.db.users.count_documents({"username": "superadmin"}) == 0:
                self.db.users.insert_one({
                    "user_id": "user-superadmin",
                    "username": "superadmin",
                    "email": "ceo@bettervision.in",
                    "password_hash": "CHANGE_ME",  # Should be hashed
                    "full_name": "Super Admin",
                    "roles": ["SUPERADMIN"],
                    "store_ids": [],
                    "is_active": True,
                    "created_at": datetime.now()
                })
                created.append("superadmin user")
            
            # Default HQ Store
            if self.db.stores.count_documents({"store_code": "HQ"}) == 0:
                self.db.stores.insert_one({
                    "store_id": "store-hq",
                    "store_code": "HQ",
                    "store_name": "Headquarters",
                    "brand": "BETTER_VISION",
                    "city": "Pune",
                    "state": "Maharashtra",
                    "is_hq": True,
                    "is_active": True,
                    "enabled_categories": ["FRAME", "SUNGLASS", "READING_GLASSES", "OPTICAL_LENS",
                                           "CONTACT_LENS", "COLORED_CONTACT_LENS", "WATCH", "SMARTWATCH",
                                           "SMARTGLASSES", "WALL_CLOCK", "ACCESSORIES", "SERVICES"],
                    "created_at": datetime.now()
                })
                created.append("HQ store")
            
            if created:
                return MigrationResult(True, f"Created: {', '.join(created)}")
            return MigrationResult(True, "Default data already exists")
        
        except Exception as e:
            return MigrationResult(False, f"Default data error: {e}")
    
    def drop_all(self) -> MigrationResult:
        """Drop all collections (DANGER!)"""
        if self.db is None:
            return MigrationResult(False, "No database connection")
        
        try:
            for name in COLLECTIONS.keys():
                self.db.drop_collection(name)
            return MigrationResult(True, f"Dropped {len(COLLECTIONS)} collections")
        except Exception as e:
            return MigrationResult(False, f"Drop failed: {e}")
    
    def get_status(self) -> Dict:
        """Get current database status"""
        if self.db is None:
            return {"status": "disconnected"}
        
        try:
            collections = self.db.list_collection_names()
            status = {}
            
            for name in COLLECTIONS.keys():
                if name in collections:
                    count = self.db[name].count_documents({})
                    indexes = list(self.db[name].list_indexes())
                    status[name] = {
                        "exists": True,
                        "documents": count,
                        "indexes": len(indexes)
                    }
                else:
                    status[name] = {"exists": False}
            
            return {
                "status": "connected",
                "database": self.db.name,
                "collections": status
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


def run_migrations(db: Database = None):
    """Run database migrations"""
    migration = DatabaseMigration(db)
    return migration.run_all()


def get_migration_status(db: Database = None) -> Dict:
    """Get migration status"""
    migration = DatabaseMigration(db)
    return migration.get_status()


if __name__ == "__main__":
    print("=" * 60)
    print("IMS 2.0 MIGRATION STATUS")
    print("=" * 60)
    
    # Run in mock mode
    migration = DatabaseMigration(None)
    migration.run_all()
