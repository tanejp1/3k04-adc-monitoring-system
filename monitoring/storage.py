"""Shared SQLite storage for module records and audit entries.

The four monitoring modules call these methods instead of writing SQL.
Eliana can extend this file for operational history and reports.
"""

from contextlib import closing
import json
import sqlite3


class SQLiteStore:
    """Keeps database queries in this file.

    Use a database filename, not :memory:, because each call opens a connection.
    save() and audit() commit before returning. If SQLite fails, pass the error
    back to the caller. Reading a record gives the caller a separate copy.
    """

    def __init__(self, path):
        if str(path) == ":memory:":
            raise ValueError("SQLiteStore requires a database file")
        self._path = str(path)
        # Create missing tables without clearing records from earlier runs.
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS module_records "
                       "(kind TEXT, record_id TEXT, payload TEXT NOT NULL, "
                       "PRIMARY KEY (kind, record_id))")
            db.execute("CREATE TABLE IF NOT EXISTS audit_entries "
                       "(id INTEGER PRIMARY KEY, actor TEXT NOT NULL, "
                       "operation TEXT NOT NULL, target TEXT, decision TEXT NOT NULL, "
                       "at REAL NOT NULL, reason TEXT NOT NULL)")

    def get(self, kind, key):
        """Find one record. Return a new dictionary, or None if it is missing."""
        with closing(sqlite3.connect(self._path)) as db:
            row = db.execute("SELECT payload FROM module_records "
                             "WHERE kind = ? AND record_id = ?", (kind, key)).fetchone()
        return None if row is None else json.loads(row[0])

    def save(self, kind, key, record):
        """Save one record, replacing the previous version with the same key."""
        # JSON holds the record fields; SQLite stores it under a kind and key.
        payload = json.dumps(record, allow_nan=False)
        with closing(sqlite3.connect(self._path)) as db, db:
            # Parameters stay separate from SQL; do not build queries from input.
            db.execute("INSERT INTO module_records VALUES (?, ?, ?) "
                       "ON CONFLICT(kind, record_id) DO UPDATE SET payload=excluded.payload",
                       (kind, key, payload))

    def list(self, kind):
        """Read all records of one kind, ordered by key, as new dictionaries."""
        with closing(sqlite3.connect(self._path)) as db:
            rows = db.execute("SELECT payload FROM module_records "
                              "WHERE kind = ? ORDER BY record_id", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def audit(self, actor, operation, target, decision, at, reason=""):
        """Save who attempted an action, its target, decision, time, and reason.

        Keep patient/medication contents out of reason. If there is no authenticated
        user, label the attempt Unauthenticated instead of making up a user ID.
        """
        actor = actor if isinstance(actor, str) else "Unauthenticated"
        operation = operation if isinstance(operation, str) else "Invalid operation"
        target = target if isinstance(target, str) else None
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("INSERT INTO audit_entries "
                       "(actor, operation, target, decision, at, reason) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       (actor, operation, target, decision, at, reason))
