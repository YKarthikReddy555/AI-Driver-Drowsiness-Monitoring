class DBWrapper:
    """Wrapper that intercepts SQLite queries and converts them for PostgreSQL if needed."""
    def __init__(self, conn, is_postgres: bool, manager=None):
        self.conn = conn
        self.is_postgres = is_postgres
        self.manager = manager
        self.cursor = None

    def execute(self, query: str, params=None):
        if self.is_postgres:
            # Handle schema translations precisely
            # SQLite: INTEGER PRIMARY KEY AUTOINCREMENT
            # Postgres: SERIAL PRIMARY KEY
            query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            
            # Handle float types
            if "REAL" in query:
                query = query.replace("REAL", "DOUBLE PRECISION")
            
            # Handle parameter markers
            if "?" in query:
                query = query.replace("?", "%s")
                
        self.cursor = self.conn.cursor()
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
        except Exception as e:
            err_str = str(e).lower()
            # Suppress known, harmless migration errors (column/index already exists)
            _is_migration_noise = any(phrase in err_str for phrase in [
                "already exists", "duplicate column"
            ])
            if not _is_migration_noise:
                print(f"\n[DBWrapper ERROR]\nQuery: {query}\nParams: {params}\nError: {e}\n")
            raise e
            
        return self

    def fetchall(self):
        return self.cursor.fetchall()

    def fetchone(self):
        return self.cursor.fetchone()

    def rollback(self):
        self.conn.rollback()

    def commit(self):
        self.conn.commit()
        
    @property
    def rowcount(self):
        if self.cursor is None: return 0
        return self.cursor.rowcount
        
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cursor:
            self.cursor.close()
        
        if exc_type is not None:
            try:
                self.conn.rollback()
            except: pass
        else:
            try:
                self.conn.commit()
            except: pass
            
        # For V4 Thread-Local, we NEVER close or release the connection here.
        # It stays alive for the life of the thread (optimized for SaaS).
        if not self.is_postgres:
            self.conn.close()
