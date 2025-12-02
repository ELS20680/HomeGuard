import sqlite3
import psycopg2
from datetime import datetime
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
                light_on INTEGER,
                mode TEXT,
                image_path TEXT,
                synced INTEGER DEFAULT 0
            )
        ''')
        
        # Create sync_state table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value INTEGER
            )
        ''')
        
        conn.commit()
        conn.close()
        print("[DB] Local SQLite database initialized")
    
    def _load_sync_state(self):
        """Load the last synced record ID"""
        conn = sqlite3.connect(self.local_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sync_state WHERE key = 'last_synced_id'")
        result = cursor.fetchone()
        if result:
            self.last_synced_id = result[0]
        conn.close()
    
    def _save_sync_state(self):
        """Save the last synced record ID"""
        conn = sqlite3.connect(self.local_db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO sync_state (key, value) 
            VALUES ('last_synced_id', ?)
        ''', (self.last_synced_id,))
        conn.commit()
        conn.close()
    
    def log_sensor_data(self, temp_c=None, humidity_pct=None, motion=0, 
                       fan_on=0, light_on=0, mode='DISARMED', image_path=None):
        """
        Log sensor data to local SQLite database.
        Returns the local record ID.
        """
        ts_iso = datetime.utcnow().isoformat()
        
        try:
            conn = sqlite3.connect(self.local_db_path, timeout=10)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO sensor_data 
                (ts_iso, temp_c, humidity_pct, motion, fan_on, light_on, mode, image_path, synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            ''', (ts_iso, temp_c, humidity_pct, motion, fan_on, light_on, mode, image_path))
            
            record_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            print(f"[DB] Logged locally: ID={record_id}, T={temp_c}C, H={humidity_pct}%, Motion={motion}")
            
            # Try to sync to cloud immediately
            self.sync_to_cloud()
            
            return record_id
            
        except Exception as e:
            print(f"[DB ERROR] Failed to log locally: {e}")
            return None
    
    def sync_to_cloud(self):
        """
        Sync unsynced local records to NEON PostgreSQL.
        Returns number of records synced.
        """
        if not self.neon_conn_string:
            return 0
        
        local_conn = None
        neon_conn = None
        
        try:
            # Get unsynced records with timeout
            local_conn = sqlite3.connect(self.local_db_path, timeout=10)
            local_cursor = local_conn.cursor()
            
            local_cursor.execute('''
                SELECT id, ts_iso, temp_c, humidity_pct, motion, fan_on, light_on, mode, image_path
                FROM sensor_data
                WHERE synced = 0
                ORDER BY id ASC
                LIMIT 100
            ''')
            
            unsynced = local_cursor.fetchall()
            
            if not unsynced:
                local_conn.close()
                return 0
            
            # Connect to NEON and insert records
            neon_conn = psycopg2.connect(self.neon_conn_string)
            neon_cursor = neon_conn.cursor()
            
            synced_count = 0
            synced_ids = []
            
            for record in unsynced:
                record_id, ts_iso, temp_c, humidity_pct, motion, fan_on, light_on, mode, image_path = record
                
                try:
                    motion = True if motion == 1 else False
                    fan_on = True if fan_on == 1 else False
                    light_on = True if light_on == 1 else False
                    neon_cursor.execute('''
                        INSERT INTO sensor_data 
                        (ts_iso, temp_c, humidity_pct, motion, fan_on, light_on, mode, image_path)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (ts_iso, temp_c, humidity_pct, motion, fan_on, light_on, mode, image_path))
                    
                    synced_ids.append(record_id)
                    synced_count += 1
                    
                except psycopg2.IntegrityError:
                    neon_conn.rollback()
                    synced_ids.append(record_id)
                    continue
            
            neon_conn.commit()
            neon_cursor.close()
            neon_conn.close()
            neon_conn = None
            
            # Mark records as synced in local DB
            if synced_ids:
                local_cursor.execute(f'''
                    UPDATE sensor_data 
                    SET synced = 1 
                    WHERE id IN ({','.join('?' * len(synced_ids))})
                ''', synced_ids)
                
                self.last_synced_id = max(synced_ids)
                self._save_sync_state()
            
            local_conn.commit()
            local_conn.close()
            local_conn = None
            
            if synced_count > 0:
                print(f"[DB] Synced {synced_count} records to NEON cloud database")
            
            return synced_count
            
        except sqlite3.OperationalError as e:
            if 'locked' in str(e):
                print(f"[DB] Database busy, will retry later")
            else:
                print(f"[DB ERROR] SQLite error: {e}")
            return 0
        except psycopg2.OperationalError as e:
            print(f"[DB] Cannot reach NEON database (offline?)")
            return 0
        except Exception as e:
            print(f"[DB ERROR] Sync failed: {e}")
            return 0
        finally:
            # Clean up connections
            if neon_conn:
                try:
                    neon_conn.close()
                except:
                    pass
            if local_conn:
                try:
                    local_conn.close()
                except:
                    pass
    
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
