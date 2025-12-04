import sqlite3
from datetime import datetime
import psycopg2
import psycopg2.extras
import threading


class DatabaseLogger:
    def __init__(self, local_db_path, neon_connection_string):
        self.local_db_path = local_db_path
        self.neon_conn_str = neon_connection_string

        self._init_local_db()

    # --------------------------------------------------
    # LOCAL SQLITE SETUP
    # --------------------------------------------------
    def _init_local_db(self):
        conn = sqlite3.connect(self.local_db_path)
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS environmental_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_iso TEXT NOT NULL,
            temp_c REAL,
            humidity_pct REAL,
            motion INTEGER DEFAULT 0,
            synced INTEGER DEFAULT 0
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS motion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_iso TEXT NOT NULL,
            temp_c REAL,
            humidity_pct REAL,
            system_mode TEXT,
            image_path TEXT,
            synced INTEGER DEFAULT 0
        )
        """)

        conn.commit()
        conn.close()

    # --------------------------------------------------
    # LOG ENVIRONMENTAL DATA (TEMP + HUMIDITY + MOTION)
    # --------------------------------------------------
    def log_environmental(self, temp_c, humidity_pct, motion=0, system_mode=None, image_path=None):
        ts_iso = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect(self.local_db_path)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO environmental_data (ts_iso, temp_c, humidity_pct, motion, synced)
            VALUES (?, ?, ?, ?, 0)
        """, (ts_iso, temp_c, humidity_pct, 1 if motion else 0))

        conn.commit()
        conn.close()

    # --------------------------------------------------
    # LOG MOTION EVENT (SEPARATE TABLE)
    # --------------------------------------------------
    def log_motion_event(self, temp_c=None, humidity_pct=None, system_mode=None, image_path=None):
        ts_iso = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        try:
            conn = sqlite3.connect(self.local_db_path)
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO motion_events (ts_iso, temp_c, humidity_pct, system_mode, image_path, synced)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (ts_iso, temp_c, humidity_pct, system_mode, image_path))

            conn.commit()
            cur.close()
            conn.close()
            print("[DB] Motion event logged.")

        except Exception as e:
            print(f"[DB ERROR] log_motion_event: {e}")

    # LOG INTRUSION
    def log_intrusion(self, temp_c, humidity_pct, system_mode, image_path):
       """Logs a motion/intrusion event into motion_events table."""
       ts_iso = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

       try:
           conn = sqlite3.connect(self.local_db_path)
           cur = conn.cursor()

           cur.execute("""
               INSERT INTO motion_events(ts_iso, temp_c, humidity_pct, system_mode, image_path)
               VALUES(?, ?, ?, ?, ?)
           """, (ts_iso, temp_c, humidity_pct, system_mode, image_path))

           conn.commit()
           cur.close()
           conn.close()

           print(f"[DB] Intrusion logged at {ts_iso}")

       except Exception as e:
           print(f"[DB ERROR] Could not log intrusion: {e}")

    # --------------------------------------------------
    # COUNT UNSYNCED ROWS
    # --------------------------------------------------
    def get_unsynced_count(self):
        conn = sqlite3.connect(self.local_db_path)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM environmental_data WHERE synced = 0")
        env = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM motion_events WHERE synced = 0")
        mot = cur.fetchone()[0]

        conn.close()
        return env + mot

    # --------------------------------------------------
    # SYNC TO NEON DB
    # --------------------------------------------------
    def sync_to_cloud(self):
        conn_sqlite = sqlite3.connect(self.local_db_path)
        cur_sqlite = conn_sqlite.cursor()

        # Fetch unsynced environmental data
        cur_sqlite.execute("""
            SELECT id, ts_iso, temp_c, humidity_pct, motion
            FROM environmental_data
            WHERE synced = 0
        """)
        env_rows = cur_sqlite.fetchall()

        # Fetch unsynced motion events
        cur_sqlite.execute("""
            SELECT id, ts_iso, temp_c, humidity_pct, system_mode, image_path
            FROM motion_events
            WHERE synced = 0
        """)
        motion_rows = cur_sqlite.fetchall()

        if not env_rows and not motion_rows:
            conn_sqlite.close()
            return

        try:
            conn_pg = psycopg2.connect(self.neon_conn_str)
            cur_pg = conn_pg.cursor()

            # INSERT environmental data
            for rid, ts, t, h, m in env_rows:
                cur_pg.execute("""
                    INSERT INTO environmental_data (ts_iso, temp_c, humidity_pct, motion)
                    VALUES (%s, %s, %s, %s)
                """, (ts, t, h, bool(m)))

                cur_sqlite.execute("UPDATE environmental_data SET synced = 1 WHERE id = ?", (rid,))

            # INSERT motion events
            for rid, ts, t, h, mode, img in motion_rows:
                cur_pg.execute("""
                    INSERT INTO motion_events (ts_iso, temp_c, humidity_pct, system_mode, image_path)
                    VALUES (%s, %s, %s, %s, %s)
                """, (ts, t, h, mode, img))

                cur_sqlite.execute("UPDATE motion_events SET synced = 1 WHERE id = ?", (rid,))

            conn_pg.commit()
            conn_sqlite.commit()

            conn_pg.close()
            conn_sqlite.close()

        except Exception as e:
            print(f"[DB ERROR] NEON sync failed: {e}")
            conn_sqlite.close()
