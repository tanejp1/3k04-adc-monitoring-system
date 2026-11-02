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
   
   
    ####### Eliana's additions: operational event history (FR-HIS-01 to 03) ######
    #######              This includes FR-HIS-01 to 03                      ######

    def _make_events_table(self):
        """Create the events table if it does not exist yet."""
        # Open a connection the same way Priyal's methods do
        with closing(sqlite3.connect(self._path)) as db, db:
            # event_id is the PRIMARY KEY so the same event can only be saved once
            db.execute("CREATE TABLE IF NOT EXISTS events "
                       "(event_id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL, "
                       "category TEXT NOT NULL, occurred_at REAL NOT NULL, "
                       "received_at REAL NOT NULL, payload TEXT NOT NULL)")

    def save_event(self, event_id, cabinet_id, category, occurred_at, received_at, payload):
        """Save one accepted event. Return True if new, False if event_id was already saved."""
        self._make_events_table()                       # make sure the table exists
        text = json.dumps(payload, allow_nan=False)     # turn the payload dictionary into text
        with closing(sqlite3.connect(self._path)) as db, db:
            # INSERT OR IGNORE skips the insert when event_id already exists
            cursor = db.execute("INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?)",
                                (event_id, cabinet_id, category, occurred_at, received_at, text))
            return cursor.rowcount == 1                 # 1 row added = new event

    def query_events(self, cabinet_id=None, category=None, start=None, end=None):
        """Return saved events as a list of dictionaries, oldest first.

        Any filter left as None is ignored. The interval is start <= time < end.
        """
        self._make_events_table()                       # make sure the table exists
        sql = ("SELECT event_id, cabinet_id, category, occurred_at, received_at, payload "
               "FROM events WHERE 1 = 1")               # 1 = 1 lets us add filters with AND
        args = []                                       # values for the ? placeholders
        if cabinet_id is not None:                      # filter by cabinet
            sql = sql + " AND cabinet_id = ?"
            args.append(cabinet_id)
        if category is not None:                        # filter by category
            sql = sql + " AND category = ?"
            args.append(category)
        if start is not None:                           # interval start (included)
            sql = sql + " AND occurred_at >= ?"
            args.append(start)
        if end is not None:                             # interval end (not included)
            sql = sql + " AND occurred_at < ?"
            args.append(end)
        sql = sql + " ORDER BY occurred_at"             # oldest first
        with closing(sqlite3.connect(self._path)) as db:
            rows = db.execute(sql, args).fetchall()     # run the query
        result = []                                     # build a list of dictionaries
        for row in rows:
            result.append({"event_id": row[0], "cabinet_id": row[1], "category": row[2],
                           "occurred_at": row[3], "received_at": row[4],
                           "payload": json.loads(row[5])})
        return result

    def recent_audit(self, limit=15):
        """Return the newest audit entries (used by the admin view, NFR-AUD-01)."""
        with closing(sqlite3.connect(self._path)) as db:
            rows = db.execute("SELECT actor, operation, target, decision, at, reason "
                              "FROM audit_entries ORDER BY id DESC LIMIT ?",
                              (limit,)).fetchall()
        result = []
        for row in rows:                                # turn each row into a dictionary
            result.append({"actor": row[0], "operation": row[1], "target": row[2],
                           "decision": row[3], "at": row[4], "reason": row[5]})
        return result