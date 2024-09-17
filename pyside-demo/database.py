# TODO: refactor the code
import os
import requests
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SQLAlchemyEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import psycopg2
import uuid
from enum import Enum as PyEnum  # Add this import


Base = declarative_base()


SQL_CREATE_TABLE: str = """
CREATE TABLE IF NOT EXISTS items (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255),
    description TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    version INTEGER,
    sync_status VARCHAR(20)
)
"""

SQL_UPDATE_OR_INSERT_ITEM: str = """
INSERT INTO items (id, name, description, created_at, updated_at, version, sync_status)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = EXCLUDED.updated_at,
    version = EXCLUDED.version,
    sync_status = EXCLUDED.sync_status
"""

SQL_FETCH_ITEMS: str = """
SELECT id,
       name,
       description,
       created_at,
       updated_at,
       version
FROM items
"""

SQL_CHECK_FOR_CONFLICTS: str = """
SELECT version FROM items WHERE id = %s
"""


class SyncStatus(PyEnum):
    SYNCED = "synced"
    MODIFIED = "modified"
    DELETED = "deleted"
    CONFLICT = "conflict"

class Item(Base):
    __tablename__ = "items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    version = Column(Integer, default=1)
    sync_status = Column(SQLAlchemyEnum(SyncStatus), default=SyncStatus.MODIFIED)

class Database:
    def __init__(self):
        self.local_engine = create_engine('sqlite:///local.db')
        Base.metadata.create_all(self.local_engine)
        self.Session = sessionmaker(bind=self.local_engine)

    def add_item(self, name, description):
        session = self.Session()
        new_item = Item(name=name, description=description)
        session.add(new_item)
        session.commit()
        session.close()

    def update_item(self, item_id, name, description):
        session = self.Session()
        item = session.query(Item).filter_by(id=item_id).first()
        if item:
            item.name = name
            item.description = description
            item.version += 1
            item.sync_status = SyncStatus.MODIFIED
            session.commit()
        session.close()

    def delete_item(self, item_id):
        session = self.Session()
        item = session.query(Item).filter_by(id=item_id).first()
        if item:
            item.sync_status = SyncStatus.DELETED
            session.commit()
        session.close()

    def get_items(self):
        session = self.Session()
        items = session.query(Item).filter(Item.sync_status != SyncStatus.DELETED).all()
        session.close()
        return items

    def is_online(self):
        try:
            requests.get('https://www.google.com', timeout=5)
            return True
        except requests.ConnectionError:
            return False

    def sync_with_postgresql(self, host, database, user, password):
        if not self.is_online():
            print("Not online, can't sync with PostgreSQL")
            return

        try:
            conn = psycopg2.connect(
                host=host,
                database=database,
                user=user,
                password=password
            )
            cur = conn.cursor()

            # Create table if not exists
            cur.execute(SQL_CREATE_TABLE)

            # Get local items
            local_items = self.get_items()

            # Synchronize items
            for item in local_items:
                if item.sync_status == SyncStatus.MODIFIED:
                    # Check for conflicts
                    cur.execute('''
                        SELECT version FROM items WHERE id = %s
                    ''', (item.id,))
                    result = cur.fetchone()
                    
                    if result and result[0] > item.version:
                        # Conflict detected
                        item.sync_status = SyncStatus.CONFLICT
                    else:
                        # Update or insert item
                        cur.execute(
                            SQL_UPDATE_OR_INSERT_ITEM,
                            (
                                item.id,
                                item.name,
                                item.description,
                                item.created_at,
                                item.updated_at,
                                item.version,
                                "synced"
                            )
                        )
                        item.sync_status = SyncStatus.SYNCED

                elif item.sync_status == SyncStatus.DELETED:
                    # Delete item from PostgreSQL
                    cur.execute('''
                        DELETE FROM items WHERE id = %s
                    ''', (item.id,))

            # Fetch items from PostgreSQL that are not in local database
            cur.execute(SQL_FETCH_ITEMS)
            pg_items = cur.fetchall()

            session = self.Session()
            for pg_item in pg_items:
                local_item = session.query(Item).filter_by(id=pg_item[0]).first()
                if not local_item:
                    new_item = Item(
                        id=pg_item[0],
                        name=pg_item[1],
                        description=pg_item[2],
                        created_at=pg_item[3],
                        updated_at=pg_item[4],
                        version=pg_item[5],
                        sync_status=SyncStatus.SYNCED
                    )
                    session.add(new_item)

            session.commit()
            session.close()

            conn.commit()
            print("Sync with PostgreSQL completed successfully")

        except Exception as e:
            print(f"Error syncing with PostgreSQL: {e}")

        finally:
            if conn:
                cur.close()
                conn.close()

    def resolve_conflict(self, item_id, resolution_choice):
        session = self.Session()
        item = session.query(Item).filter_by(id=item_id).first()
        if item and item.sync_status == SyncStatus.CONFLICT:
            if resolution_choice == 'local':
                item.sync_status = SyncStatus.MODIFIED
            elif resolution_choice == 'remote':
                # Fetch the latest version from PostgreSQL and update local
                # This part would require a connection to PostgreSQL
                pass
            session.commit()
        session.close()

# Usage example:
# db = Database()
# db.add_item("Test Item", "This is a test item")
# items = db.get_items()
# for item in items:
#     print(f"Item: {item.name}, Description: {item.description}, Status: {item.sync_status}")
# db.sync_with_postgresql("localhost", "your_db", "your_user", "your_password")
# db.resolve_conflict(item_id, 'local')  # or 'remote'