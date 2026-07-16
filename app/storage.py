from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

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
    def __init__(self, db: Database):
        self.db = db

    def disk_status(self) -> dict[str, Any]:
        usage = shutil.disk_usage(RECORDINGS_DIR)
        recording_bytes = self.recording_bytes()
        return {
            "path": str(RECORDINGS_DIR),
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "used_bytes": usage.used,
            "used_percent": round(usage.used / usage.total * 100, 2) if usage.total else 0,
            "recording_bytes": recording_bytes,
        }

    def recording_bytes(self) -> int:
        return sum(path.stat().st_size for path in RECORDINGS_DIR.rglob("*.mp4") if path.is_file())

    def start_recording(self, camera_type: str, audio_enabled: bool, storage_zone: str = "regular") -> RecordingSession:
        now = datetime.now()
        folder = RECORDINGS_DIR / storage_zone / now.strftime("%Y/%m/%d")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{now.strftime('%Y%m%d_%H%M%S')}_{camera_type}.mp4"
        recording_id = self.db.create_recording(camera_type, str(path), audio_enabled, storage_zone)
        return RecordingSession(recording_id, path, time.monotonic(), camera_type, audio_enabled)

    def finish_recording(self, session: RecordingSession, status: str = "completed") -> int:
        duration = max(0, time.monotonic() - session.started_monotonic)
        size = session.file_path.stat().st_size if session.file_path.exists() else 0
        if size == 0 and status != "recording":
            try:
                if session.file_path.exists():
                    session.file_path.unlink()
            finally:
                self.db.delete_recording(session.recording_id)
            return 0
        self.db.finish_recording(session.recording_id, status, duration, size)
        return size

    def enforce_limit(self, max_storage_gb: float) -> int:
        limit_bytes = max(0.1, float(max_storage_gb)) * 1024**3
        deleted = 0
        rows = list(reversed(self.db.list_recordings(100000)))
        while self.recording_bytes() > limit_bytes:
            candidates = [row for row in rows if row["status"] != "recording" and not row["important"]]
            if not candidates:
                candidates = [row for row in rows if row["status"] != "recording"]
            if not candidates:
                break
            row = candidates[0]
            rows.remove(row)
            path = Path(row["file_path"])
            try:
                if path.exists():
                    path.unlink()
                self.db.delete_recording(int(row["id"]))
                deleted += 1
            except OSError:
                break
        return deleted

    def clear_recordings(self) -> int:
        deleted_files = 0
        for path in sorted(RECORDINGS_DIR.rglob("*"), reverse=True):
            try:
                if path.is_file():
                    path.unlink()
                    deleted_files += 1
                elif path.is_dir() and path != RECORDINGS_DIR:
                    path.rmdir()
            except OSError:
                continue
        self.db.clear_recordings()
        return deleted_files


class RecordingManager:
    def __init__(
        self,
        storage: StorageManager,
        settings_getter: Callable[[], dict[str, Any]],
        frame_getter: Callable[[], bytes | None],
        frame_iterator: Callable[[], Iterator[bytes]] | None,
        camera_status_getter: Callable[[], dict[str, Any]],
        motion_callback: Callable[[float], None],
        detection_getter: Callable[[], dict[str, Any]] | None = None,
    ):
        self.storage = storage
        self.settings_getter = settings_getter
        self.frame_getter = frame_getter
        self.frame_iterator = frame_iterator
        self.camera_status_getter = camera_status_getter
        self.motion_callback = motion_callback
        self.detection_getter = detection_getter
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._session: RecordingSession | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self.last_error: str | None = None
        self.motion_score = 0.0
        self.last_motion_at: float | None = None
        self._last_detection_mark_second: int | None = None
        self._failure_count = 0
        self._encoder: str | None = None

    @property
    def active(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def current_recording_id(self) -> int | None:
        with self._lock:
            return self._session.recording_id if self._session else None

    def start(self) -> None:
        if self.active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="piwatch-recorder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close_segment("interrupted")
        if self._thread:
            self._thread.join(timeout=8)
        self._thread = None

    def mark_important(self, reason: str) -> int | None:
        recording_id = self.current_recording_id
        if recording_id is not None:
            self.storage.db.mark_recording_important(recording_id, reason)
        return recording_id

    def status(self) -> dict[str, Any]:
        settings = self.settings_getter().get("recording", {})
        return {
            "enabled": bool(settings.get("enabled")),
            "active": self.active,
            "recording_id": self.current_recording_id,
            "segment_seconds": int(settings.get("segment_seconds", 60)),
            "max_storage_gb": float(settings.get("max_storage_gb", 64)),
            "motion_score": self.motion_score,
            "last_motion_at": self.last_motion_at,
            "error": self.last_error,
        }

    def _run(self) -> None:
        previous_gray = None
        next_motion_at = 0.0
        next_frame_at = 0.0
        frames = self.frame_iterator() if self.frame_iterator else None
        try:
            while not self._stop.is_set():
                settings = self.settings_getter()
                recording = settings.get("recording", {})
                if not recording.get("enabled"):
                    self._close_segment("completed")
                    time.sleep(0.5)
                    continue
                camera = self.camera_status_getter()
                fps = max(1, int(camera.get("fps", 15)))
                segment_seconds = max(10, int(recording.get("segment_seconds", 60)))
                if self._session is None:
                    try:
                        self._open_segment(camera, fps)
                    except Exception as exc:
                        self.last_error = str(exc)
                        time.sleep(2)
                        continue
                    next_frame_at = time.monotonic()
                try:
                    payload = next(frames) if frames else self.frame_getter()
                except Exception as exc:
                    self.last_error = f"recording_frame_unavailable:{exc}"
                    time.sleep(0.2)
                    frames = self.frame_iterator() if self.frame_iterator else None
                    continue
                if not payload or self._process is None or self._process.stdin is None:
                    time.sleep(0.05)
                    continue
                now = time.monotonic()
                if not frames and now < next_frame_at:
                    time.sleep(min(0.01, next_frame_at - now))
                    continue
                next_frame_at = now + 1 / fps
                try:
                    self._record_detection_overlay(now)
                    self._process.stdin.write(payload)
                except (BrokenPipeError, OSError) as exc:
                    self.last_error = f"recording_encoder_failed:{exc}"
                    self._close_segment("failed")
                    self._failure_count += 1
                    time.sleep(min(60, 2 ** min(self._failure_count, 6)))
                    continue
                if now >= next_motion_at:
                    next_motion_at = now + 0.5
                    previous_gray = self._check_motion(payload, previous_gray, settings.get("motion", {}))
                if self._session and now - self._session.started_monotonic >= segment_seconds:
                    completed = self._close_segment("completed")
                    self._failure_count = 0
                    self._discard_unimportant(recording, completed)
                    self.storage.enforce_limit(float(recording.get("max_storage_gb", 64)))
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self._close_segment("completed")

    def _open_segment(self, camera: dict[str, Any], fps: int) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg_not_installed")
        recording = self.settings_getter().get("recording", {})
        alert_active = in_daily_window(
            datetime.now(),
            str(recording.get("alert_start", "22:00")),
            str(recording.get("alert_end", "06:00")),
        ) if recording.get("alert_schedule_enabled") else False
        storage_zone = "alert" if alert_active else "regular"
        session = self.storage.start_recording(str(camera.get("source_type", "csi")), False, storage_zone)
        if alert_active:
            self.storage.db.mark_recording_important(session.recording_id, "alert_schedule")
        if self._encoder is None:
            self._encoder = "h264_v4l2m2m" if self._ffmpeg_encoder_available(ffmpeg, "h264_v4l2m2m") else "libx264"
        encoder = self._encoder
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-use_wallclock_as_timestamps", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(fps), "-i", "-",
            "-an", "-c:v", encoder,
        ]
        if encoder == "libx264":
            command.extend(["-preset", "ultrafast", "-tune", "zerolatency"])
        else:
            command.extend(["-b:v", "4000k"])
        command.extend(["-fps_mode", "vfr", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(session.file_path)])
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        with self._lock:
            self._session = session
            self._process = process
            self._last_detection_mark_second = None
        self.last_error = None

    def _ffmpeg_encoder_available(self, ffmpeg: str, encoder: str) -> bool:
        try:
            result = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], text=True, capture_output=True, timeout=5)
            return result.returncode == 0 and encoder in result.stdout
        except Exception:
            return False

    def _close_segment(self, status: str) -> RecordingSession | None:
        with self._lock:
            session, process = self._session, self._process
            self._session = None
            self._process = None
        if process:
            if process.stdin:
                try: process.stdin.close()
                except OSError: pass
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if session:
            self.storage.finish_recording(session, status)
        return session

    def _discard_unimportant(self, recording: dict[str, Any], session: RecordingSession | None) -> None:
        if not recording.get("important_only") or session is None:
            return
        completed = self.storage.db.get_recording(session.recording_id)
        if not completed or completed["important"]:
            return
        path = Path(completed["file_path"])
        try:
            if path.exists():
                path.unlink()
            self.storage.db.delete_recording(int(completed["id"]))
        except OSError as exc:
            self.last_error = f"recording_delete_failed:{exc}"

    def _check_motion(self, payload: bytes, previous_gray: Any, settings: dict[str, Any]):
        if not settings.get("enabled"):
            self.motion_score = 0.0
            return None
        try:
            import cv2
            import numpy as np
            frame = cv2.imdecode(np.frombuffer(payload, dtype="uint8"), cv2.IMREAD_GRAYSCALE)
            if frame is None:
                return previous_gray
            width = max(80, int(settings.get("analysis_width", 320)))
            height = max(45, round(frame.shape[0] * width / frame.shape[1]))
            gray = cv2.GaussianBlur(cv2.resize(frame, (width, height)), (5, 5), 0)
            if previous_gray is None:
                return gray
            pixel_threshold = max(1, int(settings.get("pixel_threshold", 25)))
            self.motion_score = changed_percent(previous_gray, gray, pixel_threshold, cv2)
            trigger_percent = max(0.1, float(settings.get("trigger_percent", 8)))
            cooldown = max(0, float(settings.get("cooldown_seconds", 5)))
            now = time.time()
            if self.motion_score >= trigger_percent and (self.last_motion_at is None or now - self.last_motion_at >= cooldown):
                self.last_motion_at = now
                self.motion_callback(self.motion_score)
            return gray
        except Exception as exc:
            self.last_error = f"motion_detection_failed:{exc}"
            return previous_gray

    def _record_detection_overlay(self, now: float) -> None:
        detection = self.detection_getter() if self.detection_getter else None
        if not detection:
            return
        frame_size = detection.get("frame_size") or None
        updated_at = detection.get("updated_at")
        if not frame_size or updated_at is None or time.time() - float(updated_at) > 2.0:
            return
        detections = detection.get("last_detections") or []
        self._record_detection_mark(now, detections, frame_size)

    def _record_detection_mark(self, now: float, detections: list[dict[str, Any]], frame_size: list[int] | tuple[int, int] | None = None) -> None:
        with self._lock:
            session = self._session
        if session is None:
            return
        second = max(0.0, round(now - session.started_monotonic, 2))
        if self._last_detection_mark_second is not None and second - self._last_detection_mark_second < 0.5:
            return
        self.storage.db.add_recording_detection_mark(session.recording_id, second, [dict(item) for item in detections], frame_size, allow_empty=True)
        self._last_detection_mark_second = second


def changed_percent(previous: Any, current: Any, pixel_threshold: int, cv2: Any) -> float:
    difference = cv2.absdiff(previous, current)
    changed = cv2.threshold(difference, max(1, int(pixel_threshold)), 255, cv2.THRESH_BINARY)[1]
    return round(float(cv2.countNonZero(changed)) / changed.size * 100, 2)


def in_daily_window(now: datetime, start: str, end: str) -> bool:
    def minutes(value: str) -> int:
        hour, minute = value.split(":", 1)
        return max(0, min(1439, int(hour) * 60 + int(minute)))
    current = now.hour * 60 + now.minute
    start_minutes, end_minutes = minutes(start), minutes(end)
    if start_minutes == end_minutes:
        return True
    if start_minutes < end_minutes:
        return start_minutes <= current < end_minutes
    return current >= start_minutes or current < end_minutes
