"""
IMS 2.0 - Database Layer
=========================
MongoDB connection, schemas, migrations, and repositories
"""
from .connection import (
    DatabaseConfig,
    DatabaseConnection,
    db,
    get_db,
    init_db,
    close_db,
    get_mock_db,
    MockDatabase,
    MockCollection
)

from .schemas import (
    COLLECTIONS,
    INDEXES,
    get_all_schemas,
    get_all_indexes
)

from .migrations import (
    DatabaseMigration,
    MigrationResult,
    run_migrations,
    get_migration_status
)

__all__ = [
    # Connection
    'DatabaseConfig',
    'DatabaseConnection',
    'db',
    'get_db',
    'init_db',
    'close_db',
    'get_mock_db',
    'MockDatabase',
    'MockCollection',
    
    # Schemas
    'COLLECTIONS',
    'INDEXES',
    'get_all_schemas',
    'get_all_indexes',
    
    # Migrations
    'DatabaseMigration',
    'MigrationResult',
    'run_migrations',
    'get_migration_status'
]
