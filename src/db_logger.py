import os

class DatabaseLogger:
    """
    Handles logging to both local SQLite and cloud PostgreSQL (NEON).
    Implements offline sync capability.
    """

    def __init__(self, local_db_path='data/local_sensors.db', neon_connection_string=None):
        self.local_db_path = local_db_path
        self.neon_conn_string = neon_connection_string
        self.last_synced_id = 0

        # Ensure data directory exists
        os.makedirs(os.path.dirname(local_db_path), exist_ok=True)

        # Initialize local SQLite database
        self._init_local_db()

        # Load last synced ID
        self._load_sync_state()

        print("[DB] Database logger initialized")
        if self.neon_conn_string:
            print("[DB] NEON connection configured")
        else:
            print("[DB] WARNING: No NEON connection - local only mode")

    def _init_local_db(self):
        """Create local SQLite table if it doesn't exist"""
        conn = sqlite3.connect(self.local_db_path)
        cursor = conn.cursor()

        # Create sensor_data table matching NEON schema
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sensor_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_iso TEXT NOT NULL,
                temp_c REAL,
                humidity_pct REAL,
                motion INTEGER,
                fan_on INTEGER,
                buzzer_on INTEGER,
                image_path TEXT,
                synced INTEGER DEFAULT 0
            )
        ''')

        # Create a separate table to store sync state
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value INTEGER
            )
        ''')

        conn.commit()
        conn.close()

    def _load_sync_state(self):
        """Load the ID of the last successfully synced record."""
        try:
            conn = sqlite3.connect(self.local_db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM sync_state WHERE key = 'last_synced_id'")
            result = cursor.fetchone()
            if result:
                self.last_synced_id = result[0]
            conn.close()
        except Exception as e:
            print(f"[DB ERROR] Failed to load sync state: {e}")

    def _save_sync_state(self):
        """Save the ID of the last successfully synced record."""
        try:
            conn = sqlite3.connect(self.local_db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO sync_state (key, value)
                VALUES (?, ?)
            ''', ('last_synced_id', self.last_synced_id))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DB ERROR] Failed to save sync state: {e}")

    def log_data(self, data):
        """
        Logs sensor data (temp_c, humidity_pct, motion, image_path)
        and actuator states (fan_on, buzzer_on) to local SQLite.
        """
        try:
            conn = sqlite3.connect(self.local_db_path, timeout=5)
            cursor = conn.cursor()

            # Extract values, defaulting to None if not present
            ts_iso = datetime.utcnow().isoformat()

            temp_c = data.get('temperature')
            humidity_pct = data.get('humidity')
            # Extract 'motion' and convert to 1 (True) or 0 (False/None)
            motion = 1 if data.get('motion') == '1' else 0
            fan_on = data.get('fan_on')
            buzzer_on = data.get('buzzer_on')
            image_path = data.get('image_path')

            cursor.execute('''
                INSERT INTO sensor_data (
                    ts_iso, temp_c, humidity_pct, motion,
                    fan_on, buzzer_on, image_path, synced
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ''', (
                ts_iso, temp_c, humidity_pct, motion,
                fan_on, buzzer_on, image_path
            ))

            conn.commit()
            conn.close()
            print(f"[DB] Logged new data locally at {ts_iso}")
        except Exception as e:
            print(f"[DB ERROR] Failed to log data locally: {e}")

    def sync_to_neon(self):
        """
        Syncs unsynced data from local SQLite to NEON PostgreSQL.
        Returns the number of records synced.
        """
        if not self.neon_conn_string:
            return 0

        local_conn = None
        neon_conn = None
        synced_count = 0

        try:
            # 1. Fetch unsynced data from SQLite
            local_conn = sqlite3.connect(self.local_db_path, timeout=5)
            local_cursor = local_conn.cursor()

            local_cursor.execute(f'''
                SELECT
                    id, ts_iso, temp_c, humidity_pct, motion,
                    fan_on, buzzer_on, image_path
                FROM sensor_data
                WHERE synced = 0 AND id > {self.last_synced_id}
                ORDER BY id ASC
                LIMIT 100
            ''')

            records_to_sync = local_cursor.fetchall()

            if not records_to_sync:
                print("[DB] No new records to sync.")
                return 0

            # 2. Connect to NEON PostgreSQL
            neon_conn = psycopg2.connect(self.neon_conn_string, connect_timeout=10)
            neon_cursor = neon_conn.cursor()

            # 3. Prepare data for bulk insert
            insert_data = []
            max_synced_id = self.last_synced_id

            for record in records_to_sync:
                record_id = record[0]
                max_synced_id = max(max_synced_id, record_id)

                # PostgreSQL requires None for NULLs, not Python's None for numeric types in some cases,
                # but we use None here and let psycopg2 handle the mapping.
                insert_data.append(record[1:]) # Skip the SQLite 'id' column

            # 4. Execute bulk insert into NEON
            insert_query = """
                INSERT INTO sensor_data (
                    ts_iso, temp_c, humidity_pct, motion,
                    fan_on, buzzer_on, image_path
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """

            neon_cursor.executemany(insert_query, insert_data)
            neon_conn.commit()
            synced_count = len(records_to_sync)

            # 5. Update SQLite: Mark synced records and update sync state
            local_cursor.execute(f'''
                UPDATE sensor_data
                SET synced = 1
                WHERE id <= {max_synced_id} AND synced = 0
            ''')
            local_conn.commit()

            # 6. Update internal sync state
            self.last_synced_id = max_synced_id
            self._save_sync_state()

            print(f"[DB] Successfully synced {synced_count} records to NEON. Max ID synced: {self.last_synced_id}")

        except psycopg2.Error as e:
            print(f"[DB ERROR] NEON sync failed: {e}")
            neon_conn.rollback() # Rollback NEON transaction
        except sqlite3.Error as e:
            print(f"[DB ERROR] SQLite sync update failed: {e}")
            local_conn.rollback() # Rollback SQLite transaction
        except Exception as e:
            print(f"[DB ERROR] An unexpected error occurred during sync: {e}")
        finally:
            if local_conn:
                local_conn.close()
            if neon_conn:
                neon_conn.close()

        return synced_count

    def get_unsynced_count(self):
        """Get count of records waiting to be synced"""
        try:
            conn = sqlite3.connect(self.local_db_path, timeout=5)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sensor_data WHERE synced = 0")
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception as e:
            print(f"[DB ERROR] Failed to count unsynced: {e}")
            return 0

    def cleanup_old_synced_records(self, days=7):
        """Delete synced records older than X days to save space"""
        try:
            conn = sqlite3.connect(self.local_db_path, timeout=10)
            cursor = conn.cursor()

            cutoff_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            cutoff_date = cutoff_date.replace(day=cutoff_date.day - days)
            cutoff_iso = cutoff_date.isoformat()

            cursor.execute('''
                DELETE FROM sensor_data
                WHERE synced = 1 AND ts_iso < ?
            ''', (cutoff_iso,))

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            if deleted > 0:
                print(f"[DB] Cleaned up {deleted} old synced records")

            return deleted

        except Exception as e:
            print(f"[DB ERROR] Cleanup failed: {e}")
            return 0