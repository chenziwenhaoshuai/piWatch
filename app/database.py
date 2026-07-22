from __future__ import annotations
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .config import DB_PATH

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

class Database:
    _connection_lock = threading.RLock()

    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
    @contextmanager
    def connection(self):
        # The app has recorder, detector, email and HTTP threads all touching
        # one database. WAL plus a short serialized transaction avoids lock
        # errors from terminating a worker during normal operation.
        with self._connection_lock:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
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
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(recordings)").fetchall()}
            if "important" not in columns:
                conn.execute("ALTER TABLE recordings ADD COLUMN important INTEGER NOT NULL DEFAULT 0")
            if "important_reasons" not in columns:
                conn.execute("ALTER TABLE recordings ADD COLUMN important_reasons TEXT NOT NULL DEFAULT '[]'")
            if "storage_zone" not in columns:
                conn.execute("ALTER TABLE recordings ADD COLUMN storage_zone TEXT NOT NULL DEFAULT 'regular'")
            if "detection_marks" not in columns:
                conn.execute("ALTER TABLE recordings ADD COLUMN detection_marks TEXT NOT NULL DEFAULT '[]'")
            conn.execute("UPDATE recordings SET status='interrupted',ended_at=? WHERE status='recording'", (utc_now(),))
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
    def create_recording(self, camera_type: str, file_path: str, audio_enabled: bool, storage_zone: str = "regular") -> int:
        with self.connection() as conn:
            cur = conn.execute("INSERT INTO recordings(camera_type,file_path,started_at,audio_enabled,storage_zone,created_at) VALUES(?,?,?,?,?,?)",
                (camera_type, file_path, utc_now(), int(audio_enabled), storage_zone, utc_now()))
            return int(cur.lastrowid)
    def finish_recording(self, recording_id: int, status: str, duration: float, size: int) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE recordings SET ended_at=?,status=?,duration_seconds=?,size_bytes=? WHERE id=?",
                (utc_now(), status, duration, size, recording_id))
    def get_recording(self, recording_id: int) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
        return self._recording_dict(row) if row else None
    def mark_recording_important(self, recording_id: int, reason: str) -> None:
        with self.connection() as conn:
            row = conn.execute("SELECT important_reasons FROM recordings WHERE id=?", (recording_id,)).fetchone()
            if not row:
                return
            try: reasons = json.loads(row["important_reasons"] or "[]")
            except json.JSONDecodeError: reasons = []
            if reason not in reasons: reasons.append(reason)
            conn.execute("UPDATE recordings SET important=1,protected=1,important_reasons=? WHERE id=?",
                (json.dumps(reasons, ensure_ascii=False), recording_id))
    def add_recording_detection_mark(
        self,
        recording_id: int,
        offset_seconds: float,
        detections: list[dict[str, Any]],
        frame_size: list[int] | tuple[int, int] | None = None,
    ) -> bool:
        second = max(0, round(float(offset_seconds), 2))
        compact = [
            {
                "label": str(item.get("label", "")),
                "confidence": round(float(item.get("confidence", 0)), 3),
                "box": [round(float(value), 1) for value in (item.get("box") or [])[:4]],
            }
            for item in detections
            if item.get("label")
        ]
        if not compact:
            return False
        mark_frame_size = [int(frame_size[0]), int(frame_size[1])] if frame_size and len(frame_size) == 2 else None
        for item in detections:
            size = item.get("frame_size")
            if size and len(size) == 2:
                mark_frame_size = [int(size[0]), int(size[1])]
                break
        with self.connection() as conn:
            row = conn.execute("SELECT detection_marks FROM recordings WHERE id=?", (recording_id,)).fetchone()
            if not row:
                return False
            try: marks = json.loads(row["detection_marks"] or "[]")
            except json.JSONDecodeError: marks = []
            if len(marks) >= 60:
                return False
            mark = {"second": second, "detections": compact}
            if mark_frame_size:
                mark["frame_size"] = mark_frame_size
            marks.append(mark)
            conn.execute(
                "UPDATE recordings SET detection_marks=? WHERE id=?",
                (json.dumps(marks, ensure_ascii=False), recording_id),
            )
            return True
    def delete_recording(self, recording_id: int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM events WHERE recording_id=?", (recording_id,))
            conn.execute("DELETE FROM recordings WHERE id=?", (recording_id,))
    def delete_recordings_by_ids(self, recording_ids: list[int]) -> None:
        if not recording_ids:
            return
        placeholders = ",".join("?" for _ in recording_ids)
        with self.connection() as conn:
            conn.execute(f"DELETE FROM events WHERE recording_id IN ({placeholders})", recording_ids)
            conn.execute(f"DELETE FROM recordings WHERE id IN ({placeholders})", recording_ids)
    def clear_recordings(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM recordings").fetchone()
            count = int(row["count"] if row else 0)
            conn.execute("DELETE FROM events WHERE recording_id IS NOT NULL")
            conn.execute("DELETE FROM recordings")
            return count
    def create_event(self, event_type: str, recording_id: int | None, details: dict[str, Any] | None = None) -> int:
        with self.connection() as conn:
            cur = conn.execute("INSERT INTO events(type,started_at,recording_id,details,created_at) VALUES(?,?,?,?,?)",
                (event_type, utc_now(), recording_id, json.dumps(details or {}, ensure_ascii=False), utc_now()))
            return int(cur.lastrowid)
    def _recording_filters(self, important_only: bool = False, zone: str = "", recording_date: str = "") -> tuple[str, list[Any]]:
        filters = []
        values: list[Any] = []
        if important_only:
            filters.append("important=1")
        if zone in {"regular", "alert"}:
            filters.append("storage_zone=?")
            values.append(zone)
        if recording_date:
            filters.append("file_path LIKE ?")
            values.append(f"%/{recording_date.replace('-', '/')}/%")
        filters.append("status='completed'")
        filters.append("size_bytes>0")
        return (" WHERE " + " AND ".join(filters)) if filters else "", values
    def count_recordings(self, important_only: bool = False, zone: str = "", recording_date: str = "") -> int:
        where, values = self._recording_filters(important_only, zone, recording_date)
        with self.connection() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM recordings{where}", values).fetchone()
        return int(row["count"] if row else 0)
    def list_recordings(self, limit: int = 50, important_only: bool = False, offset: int = 0, zone: str = "", recording_date: str = "") -> list[dict[str, Any]]:
        query = "SELECT * FROM recordings"
        where, values = self._recording_filters(important_only, zone, recording_date)
        query += where
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        values.extend((max(1, int(limit)), max(0, int(offset))))
        with self.connection() as conn: rows = conn.execute(query, values).fetchall()
        return [self._recording_dict(row) for row in rows]
    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as conn: rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
    def set_event_protected(self, event_id: int, protected: bool) -> None:
        with self.connection() as conn: conn.execute("UPDATE events SET protected=? WHERE id=?", (int(protected), event_id))
    @staticmethod
    def _recording_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        try: result["important_reasons"] = json.loads(result.get("important_reasons") or "[]")
        except json.JSONDecodeError: result["important_reasons"] = []
        try: result["detection_marks"] = json.loads(result.get("detection_marks") or "[]")
        except json.JSONDecodeError: result["detection_marks"] = []
        marks = result["detection_marks"]
        if len(marks) > 60:
            step = max(1, (len(marks) + 59) // 60)
            result["detection_marks"] = marks[::step]
        result["important"] = bool(result.get("important"))
        result["protected"] = bool(result.get("protected"))
        return result
