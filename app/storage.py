from __future__ import annotations
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import RECORDINGS_DIR
from .database import Database

@dataclass
class RecordingSession:
    recording_id: int
    file_path: Path
    started_monotonic: float
    camera_type: str
    audio_enabled: bool

class StorageManager:
    def __init__(self, db: Database): self.db = db
    def disk_status(self) -> dict:
        usage = shutil.disk_usage(RECORDINGS_DIR)
        return {"path": str(RECORDINGS_DIR), "total_bytes": usage.total, "free_bytes": usage.free,
                "used_bytes": usage.used, "used_percent": round(usage.used / usage.total * 100, 2) if usage.total else 0}
    def start_recording(self, camera_type: str, audio_enabled: bool) -> RecordingSession:
        now = datetime.now(); folder = RECORDINGS_DIR / now.strftime("%Y/%m/%d"); folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{now.strftime('%Y%m%d_%H%M%S')}_{camera_type}.mp4"
        rid = self.db.create_recording(camera_type, str(path), audio_enabled)
        return RecordingSession(rid, path, time.monotonic(), camera_type, audio_enabled)
    def finish_recording(self, session: RecordingSession, status: str = "completed") -> None:
        duration = max(0, time.monotonic() - session.started_monotonic)
        size = session.file_path.stat().st_size if session.file_path.exists() else 0
        self.db.finish_recording(session.recording_id, status, duration, size)
    def cleanup(self, retention_days: int, max_used_percent: float = 70) -> int:
        cutoff = time.time() - max(1, retention_days) * 86400; deleted = 0
        for row in self.db.list_recordings(10000):
            if row["protected"] or row["status"] == "recording": continue
            path = Path(row["file_path"])
            if not path.exists(): continue
            if path.stat().st_mtime < cutoff or self.disk_status()["used_percent"] >= max_used_percent:
                try: path.unlink(); deleted += 1
                except OSError: pass
        return deleted

class RecordingManager:
    def __init__(self, storage: StorageManager): self.storage, self._lock, self._session = storage, threading.Lock(), None
    @property
    def active(self): return self._session is not None
    def start(self, camera_type: str, audio_enabled: bool) -> dict:
        with self._lock:
            if self._session: raise RuntimeError("recording_already_active")
            self._session = self.storage.start_recording(camera_type, audio_enabled)
            return {"recording_id": self._session.recording_id, "file_path": str(self._session.file_path)}
    def stop(self) -> dict:
        with self._lock:
            if not self._session: raise RuntimeError("recording_not_active")
            session = self._session; self.storage.finish_recording(session); self._session = None
            return {"recording_id": session.recording_id, "file_path": str(session.file_path)}


