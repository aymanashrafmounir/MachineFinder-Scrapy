import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional


class MachinefinderDB:
    def __init__(self, db_path: str = "machinefinder.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS machines (
                id TEXT PRIMARY KEY,
                search_title TEXT NOT NULL,
                title TEXT NOT NULL,
                price TEXT,
                location TEXT,
                hours TEXT,
                image_url TEXT,
                link TEXT NOT NULL,
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
    
    def add_machine(self, machine: Dict) -> bool:
        """Add a new machine to the database. Returns True if new, False if exists"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if machine already exists
        cursor.execute('SELECT id FROM machines WHERE id = ?', (machine['id'],))
        exists = cursor.fetchone() is not None
        
        if not exists:
            cursor.execute('''
                INSERT INTO machines (id, search_title, title, price, location, hours, image_url, link)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                machine['id'],
                machine['search_title'],
                machine['title'],
                machine.get('price', ''),
                machine.get('location', ''),
                machine.get('hours', ''),
                machine.get('image_url', ''),
                machine['link']
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
    
    def get_machine_by_id(self, machine_id: str) -> Optional[Dict]:
        """Get machine details by ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, search_title, title, price, location, hours, image_url, link 
            FROM machines WHERE id = ?
        ''', (machine_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'id': row[0],
                'search_title': row[1],
                'title': row[2],
                'price': row[3],
                'location': row[4],
                'hours': row[5],
                'image_url': row[6],
                'link': row[7]
            }
        return None
    
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

