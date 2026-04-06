import sqlite3
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List
import uuid
from utils.db_wrapper import DBWrapper

@dataclass(frozen=True)
class EventRow:
    id: int
    ts: str
    event_type: str
    severity: str
    duration_s: float
    message: str

@dataclass(frozen=True)
class Admin:
    id: int
    username: str
    password_hash: str

@dataclass(frozen=True)
class Organisation:
    id: int
    name: str
    email: str
    password_hash: str
    status: str
    subscription_expiry: str
    org_code: str
    can_clear_data: int = 0

@dataclass(frozen=True)
class Driver:
    id: int
    name: str
    email: str
    password_hash: str
    organisation_id: Optional[int]
    status: str
    created_at: str

@dataclass(frozen=True)
class Message:
    id: int
    sender_type: str
    sender_id: int
    receiver_id: int
    message: str
    timestamp: str

from utils.db_manager import DatabaseManager

class EventLogger:
    def __init__(self, db_path: str = "database.db"):
        self.db_path = str(Path(db_path))
        self.pg_url = os.environ.get("DATABASE_URL")
        self.manager = DatabaseManager()
        
        if self.pg_url:
            self.manager.init_pool(self.pg_url)
            
        self._init_db()

    def _connect(self):
        if self.pg_url:
            # V4 Thread-Scoped Connection
            # Each thread (Flask request or AI thread) maintains its own connection.
            conn = self.manager.get_conn()
            if conn:
                import psycopg2.extras
                conn.cursor_factory = psycopg2.extras.RealDictCursor
                return DBWrapper(conn, True)
            
        # Fallback to SQLite (local)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return DBWrapper(conn, False)

    def _row_to_dict(self, row):
        if row is None: return None
        d = dict(row)
        for k, v in d.items():
            from decimal import Decimal
            if isinstance(v, Decimal):
                d[k] = int(v) if v == v.to_integral_value() else float(v)
            elif hasattr(v, 'isoformat'): # datetime objects
                d[k] = v.isoformat()
        return d

    def _init_db(self) -> None:
        # 1. Base Tables (grouped transaction)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    duration_s REAL NOT NULL,
                    message TEXT NOT NULL,
                    driver_id INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS organisations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    subscription_expiry TEXT,
                    org_code TEXT NOT NULL UNIQUE,
                    can_clear_data INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS drivers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    organisation_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT '2026-03-22'
                )
            """)

        # 2. Sequential Alterations (Isolated transactions for Postgres safety)
        def _try_sql(sql):
            try:
                with self._connect() as c:
                    c.execute(sql)
            except: pass

        _try_sql("ALTER TABLE events ADD COLUMN driver_id INTEGER")
        _try_sql("CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts)")
        _try_sql("ALTER TABLE organisations ADD COLUMN can_clear_data INTEGER NOT NULL DEFAULT 0")
        _try_sql("ALTER TABLE drivers ADD COLUMN organisation_id INTEGER")
        _try_sql("ALTER TABLE drivers ADD COLUMN created_at TEXT NOT NULL DEFAULT '2026-03-22'")
        _try_sql("CREATE INDEX IF NOT EXISTS idx_drivers_org ON drivers (organisation_id)")
        _try_sql("ALTER TABLE drivers ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")

        # 3. Message Tables and Apps (grouped transaction)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_type TEXT NOT NULL,
                    sender_id INTEGER NOT NULL,
                    receiver_type TEXT NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    message_type TEXT NOT NULL DEFAULT 'text',
                    timestamp TEXT NOT NULL,
                    is_read INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS driver_applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    driver_id INTEGER NOT NULL,
                    organisation_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    timestamp TEXT NOT NULL,
                    UNIQUE(driver_id, organisation_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    type TEXT DEFAULT 'info',
                    timestamp TEXT NOT NULL,
                    is_read INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    role TEXT NOT NULL
                )
            """)

        # 4. Final Message Alterations
        _try_sql("ALTER TABLE messages ADD COLUMN receiver_type TEXT NOT NULL DEFAULT 'org'")
        _try_sql("UPDATE messages SET receiver_type = 'driver' WHERE sender_type = 'org'")
        _try_sql("UPDATE messages SET receiver_type = 'org' WHERE sender_type = 'driver'")
        _try_sql("ALTER TABLE messages ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0")
        _try_sql("ALTER TABLE messages ADD COLUMN message_type TEXT NOT NULL DEFAULT 'text'")

    def log_event(
        self,
        *,
        ts: str,
        event_type: str,
        severity: str,
        duration_s: float,
        message: str,
        driver_id: Optional[int] = None
    ) -> None:
        with self._connect() as conn:
            print(f"[EventLogger] Logging {event_type} for driver_id={driver_id}")
            conn.execute(
                "INSERT INTO events (ts, event_type, severity, duration_s, message, driver_id) VALUES (?, ?, ?, ?, ?, ?)",
                (ts, event_type, severity, float(duration_s), message, driver_id),
            )

    def get_recent_events(self, limit: int = 200, driver_id: Optional[int] = None) -> list[EventRow]:
        with self._connect() as conn:
            if driver_id:
                rows = conn.execute(
                    "SELECT id, ts, event_type, severity, duration_s, message FROM events WHERE driver_id = ? ORDER BY id DESC LIMIT ?",
                    (int(driver_id), int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, ts, event_type, severity, duration_s, message FROM events ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        return [
            EventRow(
                id=int(r["id"]),
                ts=str(r["ts"]),
                event_type=str(r["event_type"]),
                severity=str(r["severity"]),
                duration_s=float(r["duration_s"]),
                message=str(r["message"]),
            )
            for r in rows
        ]

    def clear_driver_events(self, driver_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM events WHERE driver_id = ?", (int(driver_id),))

    def clear_chat_history(self, role1: str, id1: int, role2: str, id2: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM messages 
                WHERE (sender_type = ? AND sender_id = ? AND receiver_type = ? AND receiver_id = ?)
                   OR (sender_type = ? AND sender_id = ? AND receiver_type = ? AND receiver_id = ?)
                """,
                (role1, int(id1), role2, int(id2), role2, int(id2), role1, int(id1))
            )

    # --- Admin Helpers ---
    def create_admin(self, username: str, password_hash: str) -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
                    (username.strip(), password_hash),
                )
            return True
        except Exception:
            return False

    def get_admin_by_username(self, username: str) -> Optional[Admin]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash FROM admins WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        if not row:
            return None
        return Admin(int(row["id"]), str(row["username"]), str(row["password_hash"]))

    def get_all_organizations_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM organisations").fetchone()
            if not row: return 0
            val = row["count"] if isinstance(row, dict) else row[0]
            return int(val) if val is not None else 0
            
    def get_all_drivers_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM drivers").fetchone()
            if not row: return 0
            val = row["count"] if isinstance(row, dict) else row[0]
            return int(val) if val is not None else 0

    def get_all_drivers(self):
        with self._connect() as conn:
            # Join with organisations to get the name
            rows = conn.execute("""
                SELECT d.*, o.name as org_name 
                FROM drivers d
                LEFT JOIN organisations o ON d.organisation_id = o.id
                ORDER BY d.id DESC
            """).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_driver_status(self, driver_id: int, status: str):
        with self._connect() as conn:
            conn.execute("UPDATE drivers SET status = ? WHERE id = ?", (status, driver_id))

    def delete_driver(self, driver_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM drivers WHERE id = ?", (driver_id,))

    def get_all_organisations(self):
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT o.*, (SELECT COUNT(*) FROM drivers d WHERE d.organisation_id = o.id) as driver_count
                FROM organisations o
            """).fetchall()
        return [self._row_to_dict(r) for r in rows]
        
    def update_organisation_status(self, org_id: int, status: str):
        with self._connect() as conn:
            conn.execute("UPDATE organisations SET status = ? WHERE id = ?", (status, org_id))
            
    def delete_organisation(self, org_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM drivers WHERE organisation_id = ?", (org_id,))
            conn.execute("DELETE FROM organisations WHERE id = ?", (org_id,))

    # --- Organisation Helpers ---
    def create_organisation(self, name: str, email: str, password_hash: str) -> Optional[str]:
        org_code = uuid.uuid4().hex[:8].upper()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO organisations (name, email, password_hash, org_code) VALUES (?, ?, ?, ?)",
                    (name.strip(), email.lower().strip(), password_hash, org_code),
                )
            return org_code
        except Exception:
            return None

    def get_organisation_by_email(self, email: str) -> Optional[Organisation]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, email, password_hash, status, subscription_expiry, org_code, can_clear_data FROM organisations WHERE email = ?",
                (email.lower().strip(),),
            ).fetchone()
        if not row:
            return None
        return Organisation(
            int(row["id"]), str(row["name"]), str(row["email"]), str(row["password_hash"]),
            str(row["status"]), str(row["subscription_expiry"] if row["subscription_expiry"] else ""), 
            str(row["org_code"]), int(row["can_clear_data"])
        )

    def toggle_org_clear_permission(self, org_id: int) -> bool:
        with self._connect() as conn:
            conn.execute("UPDATE organisations SET can_clear_data = 1 - can_clear_data WHERE id = ?", (int(org_id),))
            row = conn.execute("SELECT can_clear_data FROM organisations WHERE id = ?", (int(org_id),)).fetchone()
            return bool(row["can_clear_data"])

    def get_organisation_by_id(self, org_id: int) -> Optional[Organisation]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM organisations WHERE id = ?", (org_id,)).fetchone()
        if not row:
            return None
        return Organisation(
            int(row["id"]), str(row["name"]), str(row["email"]), str(row["password_hash"]),
            str(row["status"]), str(row["subscription_expiry"] if row["subscription_expiry"] else ""),
            str(row["org_code"]), int(row["can_clear_data"])
        )

    def get_drivers_by_org(self, org_id: int) -> list[Driver]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM drivers WHERE organisation_id = ?", (org_id,)).fetchall()
        return [Driver(int(r["id"]), str(r["name"]), str(r["email"]), str(r["password_hash"]), int(r["organisation_id"]), str(r["status"]), str(r["created_at"])) for r in rows]

    def accept_driver_request(self, driver_id: int, org_id: int):
        with self._connect() as conn:
            conn.execute("UPDATE drivers SET status = 'active' WHERE id = ? AND organisation_id = ?", (driver_id, org_id))

    def remove_driver(self, driver_id: int, org_id: int):
        with self._connect() as conn:
            conn.execute("UPDATE drivers SET organisation_id = NULL, status = 'active' WHERE id = ? AND organisation_id = ?", (driver_id, org_id))

    def get_organisation_by_code(self, org_code: str):
        with self._connect() as conn:
            return conn.execute("SELECT id FROM organisations WHERE org_code = ?", (org_code.strip(),)).fetchone()

    # --- Driver Helpers ---
    def create_driver(self, name: str, email: str, password_hash: str, org_id: Optional[int], created_at: str, status: str = 'active') -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO drivers (name, email, password_hash, organisation_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (name.strip(), email.lower().strip(), password_hash, org_id, status, created_at),
                )
            return True
        except Exception:
            return False

    def get_driver_by_email(self, email: str) -> Optional[Driver]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM drivers WHERE email = ?", (email.lower().strip(),)).fetchone()
        if not row:
            return None
        return Driver(
            int(row["id"]), str(row["name"]), str(row["email"]), str(row["password_hash"]),
            int(row["organisation_id"]) if row["organisation_id"] else None, str(row["status"]), str(row["created_at"])
        )

    def get_driver_by_id(self, driver_id: int) -> Optional[Driver]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,)).fetchone()
        if not row:
            return None
        return Driver(
            int(row["id"]), str(row["name"]), str(row["email"]), str(row["password_hash"]),
            int(row["organisation_id"]) if row["organisation_id"] else None, str(row["status"]), str(row["created_at"])
        )

    def update_driver_password(self, email: str, new_password_hash: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE drivers SET password_hash = ? WHERE email = ?",
                (new_password_hash, email.lower().strip()),
            )
            return cursor.rowcount > 0

    # Fallback to keep compatibility during migration
    def _migrate_users_to_drivers(self):
        with self._connect() as conn:
            try:
                users = conn.execute("SELECT * FROM users").fetchall()
                for user in users:
                    try:
                        conn.execute(
                            "INSERT INTO drivers (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                            (user["email"].split('@')[0], user["email"], user["password_hash"], user["created_at"])
                        )
                    except Exception:
                        pass
                # Ignore users table after migrating
            except Exception:
                pass


    # --- Messages Helpers ---
    def save_message(self, sender_type: str, sender_id: int, receiver_type: str, receiver_id: int, message: str, timestamp: str, message_type: str = 'text'):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (sender_type, sender_id, receiver_type, receiver_id, message, message_type, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sender_type, sender_id, receiver_type, receiver_id, message, message_type, timestamp)
            )

    def get_messages(self, role1: str, id1: int, role2: str, id2: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages 
                WHERE (sender_type = ? AND sender_id = ? AND receiver_type = ? AND receiver_id = ?)
                   OR (sender_type = ? AND sender_id = ? AND receiver_type = ? AND receiver_id = ?)
                ORDER BY timestamp ASC
                """,
                (role1, int(id1), role2, int(id2), role2, int(id2), role1, int(id1))
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def clear_messages(self, role1: str, id1: int, role2: str, id2: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM messages 
                WHERE (sender_type = ? AND sender_id = ? AND receiver_type = ? AND receiver_id = ?)
                   OR (sender_type = ? AND sender_id = ? AND receiver_type = ? AND receiver_id = ?)
                """,
                (role1, int(id1), role2, int(id2), role2, int(id2), role1, int(id1))
            )
            conn.commit()

    def mark_messages_read(self, receiver_type: str, receiver_id: int, sender_type: str, sender_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET is_read = 1 WHERE receiver_type = ? AND receiver_id = ? AND sender_type = ? AND sender_id = ?",
                (receiver_type, receiver_id, sender_type, sender_id)
            )
            conn.commit()

    def create_reset_token(self, email: str, token: str, expires_at: str, role: str) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM password_resets WHERE email = ? AND role = ?", (email, role))
            conn.execute(
                "INSERT INTO password_resets (email, token, expires_at, role) VALUES (?, ?, ?, ?)",
                (email, token, expires_at, role)
            )
            return True

    def get_reset_token_info(self, token: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM password_resets WHERE token = ?", (token,)).fetchone()
            return self._row_to_dict(row) if row else None

    def delete_reset_token(self, token: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM password_resets WHERE token = ?", (token,))

    def update_organisation_password(self, email: str, new_password_hash: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE organisations SET password_hash = ? WHERE email = ?",
                (new_password_hash, email.lower().strip()),
            )
            return cursor.rowcount > 0

    def get_driver_applications(self, driver_id: int) -> List[dict]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT a.*, o.name as org_name 
                FROM driver_applications a
                JOIN organisations o ON a.organisation_id = o.id
                WHERE a.driver_id = ?
                ORDER BY a.timestamp DESC
                """,
                (driver_id,)
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]

    def create_driver_application(self, driver_id: int, organisation_id: int) -> bool:
        from datetime import datetime
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO driver_applications (driver_id, organisation_id, timestamp) VALUES (?, ?, ?)",
                    (driver_id, organisation_id, datetime.now().isoformat())
                )
                return True
        except Exception:
            return False

    def update_application_status(self, driver_id: int, organisation_id: int, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE driver_applications SET status = ? WHERE driver_id = ? AND organisation_id = ?",
                (status, driver_id, organisation_id)
            )

    def get_pending_applications_for_org(self, org_id: int) -> List[dict]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT a.id as application_id, d.id, d.name, d.email, a.timestamp
                FROM driver_applications a
                JOIN drivers d ON a.driver_id = d.id
                WHERE a.organisation_id = ? AND a.status = 'pending'
                """,
                (org_id,)
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]

    def create_notification(self, user_id: int, title: str, message: str, type: str = 'info') -> None:
        from datetime import datetime
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO notifications (user_id, title, message, type, timestamp) VALUES (?, ?, ?, ?, ?)",
                (user_id, title, message, type, datetime.now().isoformat())
            )
            conn.commit()

    def get_notifications(self, user_id: int, limit: int = 20) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT * FROM notifications WHERE user_id = ? ORDER BY "timestamp" DESC LIMIT ?',
                (user_id, limit)
            )
            return [self._row_to_dict(r) for r in rows.fetchall()]

    def clear_notifications(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,))
            conn.commit()

    def delete_notification(self, notif_id: int, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM notifications WHERE id = ? AND user_id = ?", (notif_id, user_id))
            conn.commit()