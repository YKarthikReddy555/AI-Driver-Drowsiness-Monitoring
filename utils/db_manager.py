import os
import threading
import time
from typing import Optional

class DatabaseManager:
    """Thread-scoped singleton for managing PostgreSQL connections.
    Handles Neon.tech's aggressive idle connection timeouts with
    automatic reconnection and TCP keepalive options.
    """
    _instance = None
    _lock = threading.Lock()
    _local = threading.local() # Each thread (Flask/AI) gets its own connection

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DatabaseManager, cls).__new__(cls)
            return cls._instance

    def init_pool(self, dsn: str, *args, **kwargs):
        """Stores the DSN for thread-scoped connections."""
        self.dsn = dsn
        print("[DatabaseManager] V4 Thread-Local Storage initialized.")

    def get_conn(self):
        """Fetches a thread-local connection, re-opening if the connection has dropped.
        Neon.tech drops idle connections after ~5 minutes. We handle this gracefully
        by detecting the dead connection and immediately opening a fresh one."""
        
        if not hasattr(self._local, "conn"):
            self._local.conn = None

        # Check if existing connection is still alive
        if self._local.conn:
            try:
                with self._local.conn.cursor() as cur:
                    cur.execute("SELECT 1")
                self._local.conn.autocommit = True
                return self._local.conn
            except Exception:
                # Connection is dead (Neon timeout / network drop)
                try:
                    self._local.conn.close()
                except Exception:
                    pass
                self._local.conn = None

        # Open a fresh connection (with retry for transient startup failures)
        return self._open_fresh_connection()

    def _open_fresh_connection(self, max_retries: int = 3):
        """Opens a brand new PostgreSQL connection with TCP keepalive enabled.
        Retries up to max_retries times to handle transient Neon startup delays."""
        import psycopg2
        
        # TCP keepalive prevents the OS-level connection from going silent
        keepalive_kwargs = {
            "keepalives": 1,
            "keepalives_idle": 60,       # Send probe after 60s idle
            "keepalives_interval": 10,   # Retry every 10s
            "keepalives_count": 5,       # Give up after 5 failed probes
        }

        for attempt in range(1, max_retries + 1):
            try:
                conn = psycopg2.connect(self.dsn, sslmode='require', **keepalive_kwargs)
                conn.autocommit = True
                self._local.conn = conn
                print(f"[DatabaseManager] New Thread-Scoped connection created (Thread: {threading.get_ident()})")
                return conn
            except Exception as e:
                if attempt < max_retries:
                    wait = attempt * 1.5  # 1.5s, 3s backoff
                    print(f"[DatabaseManager] Connection attempt {attempt} failed, retrying in {wait}s: {e}")
                    time.sleep(wait)
                else:
                    print(f"[DatabaseManager] CRITICAL: All {max_retries} connection attempts failed: {e}")
                    raise e

    def release_conn(self, conn):
        """Connections are persistent per-thread and NOT returned to a pool."""
        pass

    def close_all(self):
        """Manual cleanup if needed."""
        if hasattr(self._local, "conn") and self._local.conn:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None
