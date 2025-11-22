import sqlite3
from typing import Dict


class MachinefinderDB:
    def __init__(self, db_path: str = "machinefinder.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with minimal tracking table"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Simple table - only track ID and search_title
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS machines (
                id TEXT PRIMARY KEY,
                search_title TEXT NOT NULL
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
    
    def add_machine(self, machine: Dict) -> bool:
        """Add a new machine ID to tracking. Returns True if new, False if exists"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if machine already exists
        cursor.execute('SELECT id FROM machines WHERE id = ?', (machine['id'],))
        exists = cursor.fetchone() is not None
        
        if not exists:
            # Only save ID and search_title - no details, no timestamps
            cursor.execute('''
                INSERT INTO machines (id, search_title)
                VALUES (?, ?)
            ''', (machine['id'], machine['search_title']))
            
            conn.commit()
        
        conn.close()
        return not exists
    
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

