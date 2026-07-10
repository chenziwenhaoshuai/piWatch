from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .config import DB_PATH

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recordings (
              id INTEGER PRIMARY KEY AUTOINCREMENT, camera_type TEXT NOT NULL, file_path TEXT NOT NULL,
              started_at TEXT NOT NULL, ended_at TEXT, duration_seconds REAL, size_bytes INTEGER DEFAULT 0,
              audio_enabled INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'recording',
              protected INTEGER NOT NULL DEFAULT 0, thumbnail_path TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, started_at TEXT NOT NULL,
              ended_at TEXT, recording_id INTEGER, viewed INTEGER NOT NULL DEFAULT 0,
              protected INTEGER NOT NULL DEFAULT 0, email_sent INTEGER NOT NULL DEFAULT 0,
              details TEXT, created_at TEXT NOT NULL, FOREIGN KEY(recording_id) REFERENCES recordings(id));
            """)
    def set_setting(self, key: str, value: Any) -> None:
        with self.connection() as conn:
            conn.execute("""INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)
              ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
              (key, json.dumps(value, ensure_ascii=False), utc_now()))
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row: return default
        try: return json.loads(row["value"])
        except json.JSONDecodeError: return row["value"]
    def get_settings(self) -> dict[str, Any]:
        with self.connection() as conn: rows = conn.execute("SELECT key,value FROM settings").fetchall()
        result = {}
        for row in rows:
            try: result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError: result[row["key"]] = row["value"]
        return result
    def create_recording(self, camera_type: str, file_path: str, audio_enabled: bool) -> int:
        with self.connection() as conn:
            cur = conn.execute("INSERT INTO recordings(camera_type,file_path,started_at,audio_enabled,created_at) VALUES(?,?,?,?,?)",
                (camera_type, file_path, utc_now(), int(audio_enabled), utc_now()))
            return int(cur.lastrowid)
    def finish_recording(self, recording_id: int, status: str, duration: float, size: int) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE recordings SET ended_at=?,status=?,duration_seconds=?,size_bytes=? WHERE id=?",
                (utc_now(), status, duration, size, recording_id))
    def create_event(self, event_type: str, recording_id: int | None, details: dict[str, Any] | None = None) -> int:
        with self.connection() as conn:
            cur = conn.execute("INSERT INTO events(type,started_at,recording_id,details,created_at) VALUES(?,?,?,?,?)",
                (event_type, utc_now(), recording_id, json.dumps(details or {}, ensure_ascii=False), utc_now()))
            return int(cur.lastrowid)
    def list_recordings(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as conn: rows = conn.execute("SELECT * FROM recordings ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as conn: rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
    def set_event_protected(self, event_id: int, protected: bool) -> None:
        with self.connection() as conn: conn.execute("UPDATE events SET protected=? WHERE id=?", (int(protected), event_id))
