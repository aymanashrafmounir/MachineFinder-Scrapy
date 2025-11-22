import sqlite3
import os
from typing import Set

# Path to the SQLite database file
DB_PATH = os.path.join(os.path.dirname(__file__), "items.db")


def _get_connection():
    """Create and return a new SQLite connection with Row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database with a minimal table storing only slug and search_name."""
    with _get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                slug TEXT PRIMARY KEY,
                search_name TEXT
            )
            """
        )
        # Create a config table to store settings like first_run_complete
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.commit()


def is_first_run() -> bool:
    """Check if this is the first run (no items in database)."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM config WHERE key = 'first_run_complete'"
        ).fetchone()
        # If the flag doesn't exist or is not 'true', it's the first run
        return row is None or row["value"] != "true"


def mark_first_run_complete():
    """Mark that the first run is complete."""
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO config (key, value)
            VALUES ('first_run_complete', 'true')
            ON CONFLICT(key) DO UPDATE SET value='true'
            """
        )
        conn.commit()


def upsert_item(slug: str, search_name: str):
    """Insert a new record or update the existing one based on slug.

    Only the slug and the search_name are stored to save space.
    """
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO items (slug, search_name)
            VALUES (?, ?)
            ON CONFLICT(slug) DO UPDATE SET search_name=excluded.search_name
            """,
            (slug, search_name),
        )
        conn.commit()


def get_all_slugs() -> Set[str]:
    """Return a set of all slugs currently stored in the database."""
    with _get_connection() as conn:
        rows = conn.execute("SELECT slug FROM items").fetchall()
        return {row["slug"] for row in rows}


def get_slugs_by_search(search_name: str) -> Set[str]:
    """Return slugs for items belonging to a specific search (category)."""
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT slug FROM items WHERE search_name = ?", (search_name,)
        ).fetchall()
        return {row["slug"] for row in rows}


def get_total_count() -> int:
    """Return total number of items stored in the DB."""
    with _get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM items").fetchone()
        return row["cnt"] if row else 0


def delete_missing(slugs: Set[str]):
    """Delete rows whose slug is not present in the supplied set."""
    if not slugs:
        return
    placeholders = ",".join(["?"] * len(slugs))
    with _get_connection() as conn:
        conn.execute(
            f"DELETE FROM items WHERE slug NOT IN ({placeholders})", tuple(slugs)
        )
        conn.commit()


def delete_missing_by_search(search_name: str, current_slugs: Set[str]) -> int:
    """Delete items for a specific search that are not in the current set.
    
    Returns the number of deleted items.
    """
    with _get_connection() as conn:
        # Get existing slugs for this search
        existing = conn.execute(
            "SELECT slug FROM items WHERE search_name = ?", (search_name,)
        ).fetchall()
        existing_slugs = {row["slug"] for row in existing}
        
        # Find slugs to delete (in DB but not in current fetch)
        to_delete = existing_slugs - current_slugs
        
        if not to_delete:
            return 0
        
        # Delete them
        placeholders = ",".join(["?"] * len(to_delete))
        conn.execute(
            f"DELETE FROM items WHERE slug IN ({placeholders})",
            tuple(to_delete)
        )
        conn.commit()
        return len(to_delete)
