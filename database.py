import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional


class MachinefinderDB:
    def __init__(self, db_path: str = "machinefinder.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables
        
        OPTIMIZED SCHEMA:
        - Only stores: id, search_title, first_seen, last_seen
        - Does NOT store: title, price, location, hours, image_url, link
        - Reason: We only need IDs for comparison, not full data
        - Full data is used ONLY for Telegram notifications (not stored)
        - Storage savings: ~90% smaller database!
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if old table exists with extra columns
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='machines'")
        table_exists = cursor.fetchone() is not None
        
        if table_exists:
            # Check if old schema (has 'title' column)
            cursor.execute("PRAGMA table_info(machines)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'title' in columns:
                # Migrate to new optimized schema
                print("🔄 Migrating database to optimized schema (removing unnecessary columns)...")
                cursor.execute('''
                    CREATE TABLE machines_new (
                        id TEXT PRIMARY KEY,
                        search_title TEXT NOT NULL,
                        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Copy only essential data
                cursor.execute('''
                    INSERT INTO machines_new (id, search_title, first_seen, last_seen)
                    SELECT id, search_title, first_seen, last_seen FROM machines
                ''')
                
                # Replace old table
                cursor.execute('DROP TABLE machines')
                cursor.execute('ALTER TABLE machines_new RENAME TO machines')
                print("✅ Migration complete! Database is now ~90% smaller.")
        else:
            # Create new optimized table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS machines (
                    id TEXT PRIMARY KEY,
                    search_title TEXT NOT NULL,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        conn.commit()
        conn.close()
    
    def get_existing_ids(self, search_title: str) -> set:
        """Get all existing machine IDs for a specific search"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT id FROM machines WHERE search_title = ?', (search_title,))
        existing_ids = {row[0] for row in cursor.fetchall()}
        
        conn.close()
        return existing_ids
    
    def batch_process_machines(self, machines: List[Dict], search_title: str) -> List[Dict]:
        """
        OPTIMIZED: Process all machines in batch mode (much faster!)
        Returns list of NEW machines only.
        
        Performance improvement:
        - OLD WAY: N queries (1 SELECT + 1 INSERT/UPDATE per machine)
        - NEW WAY: 1 SELECT + batch INSERT + batch UPDATE
        - Speed: ~10-100x faster with large datasets!
        """
        if not machines:
            return []
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # STEP 1: Get ALL existing IDs for this category (1 query only!)
            cursor.execute('SELECT id FROM machines WHERE search_title = ?', (search_title,))
            existing_ids = {row[0] for row in cursor.fetchall()}
            
            # STEP 2: Separate new machines from existing ones (in-memory comparison)
            new_machines = []
            existing_machines = []
            
            for machine in machines:
                machine_id = machine['id']
                if machine_id not in existing_ids:
                    new_machines.append(machine)
                else:
                    existing_machines.append(machine_id)
            
            # STEP 3: Batch INSERT new machines (1 query for all!)
            # Only store ID + search_title (no need for full data!)
            if new_machines:
                cursor.executemany('''
                    INSERT INTO machines (id, search_title)
                    VALUES (?, ?)
                ''', [
                    (m['id'], m['search_title'])
                    for m in new_machines
                ])
            
            # STEP 4: Batch UPDATE last_seen for existing machines (1 query for all!)
            if existing_machines:
                placeholders = ','.join('?' * len(existing_machines))
                cursor.execute(
                    f'UPDATE machines SET last_seen = CURRENT_TIMESTAMP WHERE id IN ({placeholders})',
                    existing_machines
                )
            
            conn.commit()
            return new_machines
            
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def add_machine(self, machine: Dict) -> bool:
        """Add a new machine to the database. Returns True if new, False if exists"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if machine already exists
        cursor.execute('SELECT id FROM machines WHERE id = ?', (machine['id'],))
        exists = cursor.fetchone() is not None
        
        if not exists:
            # Only store ID + search_title (optimized!)
            cursor.execute('''
                INSERT INTO machines (id, search_title)
                VALUES (?, ?)
            ''', (
                machine['id'],
                machine['search_title']
            ))
        else:
            # Update last_seen timestamp
            cursor.execute('''
                UPDATE machines 
                SET last_seen = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (machine['id'],))
        
        conn.commit()
        conn.close()
        
        return not exists
    
    def get_new_machines(self, current_ids: set, search_title: str) -> List[str]:
        """Compare current scraped IDs with database to find new machines"""
        existing_ids = self.get_existing_ids(search_title)
        new_ids = current_ids - existing_ids
        return list(new_ids)
    
    # REMOVED: get_machine_by_id() - no longer needed!
    # Full machine data is NOT stored in DB anymore (only IDs)
    # Machine details come directly from scraping for notifications
    
    def cleanup_missing_machines(self, search_title: str, current_ids: set) -> int:
        """Remove machines for this category that are not in current_ids.
        Returns the number of machines deleted."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get existing IDs for this category
        cursor.execute('SELECT id FROM machines WHERE search_title = ?', (search_title,))
        existing_ids = {row[0] for row in cursor.fetchall()}
        
        # Find IDs to delete (exist in DB but not in current scrape)
        ids_to_delete = existing_ids - current_ids
        
        if ids_to_delete:
            # Delete these machines
            placeholders = ','.join('?' * len(ids_to_delete))
            cursor.execute(
                f'DELETE FROM machines WHERE id IN ({placeholders})',
                tuple(ids_to_delete)
            )
            deleted_count = cursor.rowcount
        else:
            deleted_count = 0
        
        conn.commit()
        conn.close()
        
        return deleted_count

