from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .camera import CameraManager
from .config import RECORDINGS_DIR, STATIC_DIR, TEMPLATES_DIR
from .database import Database
from .notifications import EmailNotifier, notification_allows
from .storage import RecordingManager, StorageManager
from .system_monitor import SystemMonitor
from .yolo_detector import Detection, DetectionWorker

DEFAULTS: dict[str, Any] = {
    "camera": {"source_type": "csi", "device": "csi:0", "width": 1280, "height": 720, "fps": 15, "bitrate": 2500000},
    "audio": {"enabled": False},
    "storage": {"retention_days": 7, "max_used_percent": 70},
    "recording": {"enabled": True, "segment_seconds": 60, "max_storage_gb": 64, "important_only": False, "alert_schedule_enabled": False, "alert_start": "22:00", "alert_end": "06:00"},
    "motion": {"enabled": True, "analysis_width": 320, "pixel_threshold": 25, "trigger_percent": 8, "cooldown_seconds": 5, "event_types": ["motion", "camera_disconnected", "storage_low", "temperature_high"]},
    "yolo": {"enabled": True, "model_path": "/var/lib/piwatch/models/yolo26n_ncnn_model", "confidence": 0.4, "imgsz": 416, "sample_fps": 2, "alert_cooldown_seconds": 300, "target_classes": ["person", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"], "roi": {"x": 0, "y": 0, "width": 1, "height": 1}},
    "notifications": {"enabled": False, "send_motion": True, "send_yolo": True, "alert_window_only": False, "smtp_host": "", "smtp_port": 465, "security": "ssl", "sender": "", "username": "", "password": "", "recipient": "", "subject_prefix": "[PiWatch]"},
}

MODELS_DIR = Path(os.getenv("PIWATCH_MODELS_DIR", "/var/lib/piwatch/models"))
YOLO_MODEL_PATHS = {
    256: str(MODELS_DIR / "yolo26n_256_ncnn_model"),
    320: str(MODELS_DIR / "yolo26n_320_ncnn_model"),
    416: str(MODELS_DIR / "yolo26n_ncnn_model"),
    512: str(MODELS_DIR / "yolo26n_512_ncnn_model"),
    640: str(MODELS_DIR / "yolo26n_640_ncnn_model"),
}


def merge_defaults(db: Database) -> None:
    for key, value in DEFAULTS.items():
        current = db.get_setting(key)
        if current is None:
            db.set_setting(key, value)
        elif isinstance(current, dict):
            merged = {**value, **current}
            if merged != current:
                db.set_setting(key, merged)


class AppState:
    def __init__(self):
        self.db = Database()
        merge_defaults(self.db)
        self._preload_native_runtime()
        self._preload_yolo_runtime()
        self.camera = CameraManager()
        self.storage = StorageManager(self.db)
        self.system = SystemMonitor()
        camera = self.db.get_setting("camera")
        self.camera.configure(camera)
        self.recorder = RecordingManager(
            self.storage,
            self.settings,
            self.camera.latest_frame,
            self.camera.status,
            self._on_motion,
        )
        self.detector = DetectionWorker(self._detection_settings, self._on_detection, self.camera.latest_frame)
        if self.db.get_setting("recording", {}).get("enabled"):
            self.recorder.start()
        if self.db.get_setting("yolo", {}).get("enabled"):
            self.detector.start()

    @staticmethod
    def _preload_native_runtime() -> None:
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            pass

    def _preload_yolo_runtime(self) -> None:
        if not self.db.get_setting("yolo", {}).get("enabled"):
            return
        try:
            import ultralytics  # noqa: F401
        except ImportError:
            pass

    def settings(self) -> dict[str, Any]:
        return self.db.get_settings()

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        for key, value in payload.items():
            if key not in DEFAULTS:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"setting_{key}_must_be_object")
            value = dict(value)
            if key == "yolo" and "imgsz" in value:
                image_size = int(value["imgsz"])
                if image_size not in YOLO_MODEL_PATHS:
                    raise ValueError("yolo_imgsz_must_be_256_320_416_512_or_640")
                value["imgsz"] = image_size
            if key == "yolo" and "sample_fps" in value:
                sample_fps = int(value["sample_fps"])
                if sample_fps < 0:
                    raise ValueError("yolo_sample_fps_must_be_zero_or_positive")
                value["sample_fps"] = sample_fps
            if key == "recording":
                if "segment_seconds" in value:
                    value["segment_seconds"] = max(10, int(value["segment_seconds"]))
                if "max_storage_gb" in value:
                    value["max_storage_gb"] = max(0.1, float(value["max_storage_gb"]))
                for field in ("alert_start", "alert_end"):
                    if field in value:
                        value[field] = validate_time(value[field])
            if key == "motion":
                if "pixel_threshold" in value:
                    value["pixel_threshold"] = max(1, int(value["pixel_threshold"]))
                if "trigger_percent" in value:
                    value["trigger_percent"] = max(0.1, min(100, float(value["trigger_percent"])))
                if "cooldown_seconds" in value:
                    value["cooldown_seconds"] = max(0, float(value["cooldown_seconds"]))
            if key == "notifications":
                if "smtp_port" in value:
                    value["smtp_port"] = max(1, min(65535, int(value["smtp_port"])))
                if "security" in value and value["security"] not in ("ssl", "starttls", "none"):
                    raise ValueError("smtp_security_must_be_ssl_starttls_or_none")
            current = self.db.get_setting(key, {})
            current.update(value)
            self.db.set_setting(key, current)
        camera = self.db.get_setting("camera")
        self.camera.configure(camera)
        if self.db.get_setting("recording", {}).get("enabled"):
            self.recorder.start()
        else:
            self.recorder.stop()
        if self.db.get_setting("yolo", {}).get("enabled"):
            self.detector.start()
        else:
            self.detector.stop()
        return self.settings()

    def status(self) -> dict[str, Any]:
        return {
            "camera": self.camera.status(),
            "audio": self.db.get_setting("audio"),
            "storage": self.storage.disk_status(),
            "system": self.system.snapshot(),
            "recording": self.recorder.status(),
            "yolo": self.detector.status(),
        }

    def _detection_settings(self) -> dict[str, Any]:
        yolo = self.db.get_setting("yolo", {})
        camera = self.db.get_setting("camera", {})
        image_size = int(yolo.get("imgsz", 416))
        return {
            **yolo,
            "model_path": YOLO_MODEL_PATHS.get(image_size, YOLO_MODEL_PATHS[416]),
            "device": camera.get("device", "/dev/video0"),
            "source_type": camera.get("source_type", "usb"),
            "width": camera.get("width", 1280),
            "height": camera.get("height", 720),
            "fps": camera.get("fps", 15),
        }

    def _on_detection(self, detections: list[Detection]) -> None:
        details = {"detections": [d.__dict__ for d in detections], "roi": self.db.get_setting("yolo", {}).get("roi")}
        recording_id = self.recorder.mark_important("yolo")
        event_id = self.db.create_event("yolo_target_detected", recording_id, details)
        labels = ", ".join(f"{d.label} ({d.confidence:.2f})" for d in detections)
        self._notify_important_event(
            "yolo",
            "YOLO 目标报警",
            f"检测到目标：{labels}\n事件 ID：{event_id}\n录像 ID：{recording_id or '无'}\nROI：{details['roi']}",
            event_id,
        )

    def _on_motion(self, score: float) -> None:
        recording_id = self.recorder.mark_important("motion")
        event_id = self.db.create_event("motion", recording_id, {"score_percent": score})
        self._notify_important_event(
            "motion",
            "移动检测报警",
            f"画面变化面积：{score:.2f}%\n事件 ID：{event_id}\n录像 ID：{recording_id or '无'}",
            event_id,
        )

    def _notify_important_event(self, event_type: str, title: str, body: str, event_id: int) -> None:
        notification = self.db.get_setting("notifications", {})
        recording = self.db.get_setting("recording", {})
        if not notification_allows(event_type, notification, recording, datetime.now().astimezone()):
            return
        frame = self.camera.latest_frame()
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        subject = f"{notification.get('subject_prefix', '[PiWatch]')} {title}"
        message = f"{body}\n时间：{timestamp}\n主机：security-camera"

        def send() -> None:
            try:
                EmailNotifier(notification).send(subject, message, frame, f"event-{event_id}.jpg")
            except Exception:
                # Recording and event handling must continue if SMTP is unavailable.
                pass

        threading.Thread(target=send, name=f"email-event-{event_id}", daemon=True).start()


STATE = AppState()


class Handler(BaseHTTPRequestHandler):
    server_version = "PiWatch/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self.file(TEMPLATES_DIR / "index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            asset = (STATIC_DIR / path[8:]).resolve()
            if STATIC_DIR.resolve() not in asset.parents:
                return self.json({"error": {"code": "NOT_FOUND", "message": "资源不存在"}}, HTTPStatus.NOT_FOUND)
            content_type = "text/css; charset=utf-8" if path.endswith(".css") else "application/javascript; charset=utf-8"
            return self.file(asset, content_type)
        if path == "/api/v1/stream.mjpg":
            return self.stream()
        if path == "/api/v1/snapshot.jpg":
            try:
                return self.raw(STATE.camera.snapshot(), "image/jpeg")
            except RuntimeError as exc:
                return self.json({"error": {"code": "CAMERA_UNAVAILABLE", "message": str(exc)}}, HTTPStatus.SERVICE_UNAVAILABLE)
        if path.startswith("/api/v1/recordings/") and path.endswith("/video"):
            try:
                recording_id = int(path.split("/")[-2])
                return self.video(recording_id)
            except (ValueError, OSError) as exc:
                return self.json({"error": {"code": "VIDEO_UNAVAILABLE", "message": str(exc)}}, HTTPStatus.NOT_FOUND)
        query = parse_qs(parsed.query)
        routes = {
            "/api/v1/health": lambda: {"ok": True},
            "/api/v1/status": STATE.status,
            "/api/v1/detections": STATE.detector.detection_status,
            "/api/v1/settings": STATE.settings,
            "/api/v1/cameras": lambda: {"items": STATE.camera.list_devices()},
            "/api/v1/recordings": lambda: {"items": filter_recordings(
                STATE.db.list_recordings(200, query.get("important", ["0"])[0] == "1"),
                query.get("zone", [""])[0],
            )},
            "/api/v1/events": lambda: {"items": STATE.db.list_events()},
        }
        if path in routes:
            return self.json(routes[path]())
        return self.json({"error": {"code": "NOT_FOUND", "message": "资源不存在"}}, HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        if self.path != "/api/v1/settings":
            return self.json({"error": {"code": "NOT_FOUND", "message": "资源不存在"}}, HTTPStatus.NOT_FOUND)
        try:
            return self.json(STATE.update_settings(self.body()))
        except Exception as exc:
            return self.json({"error": {"code": "INVALID_SETTINGS", "message": str(exc)}}, HTTPStatus.BAD_REQUEST)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/v1/notifications/test-email":
                notification = STATE.settings()["notifications"]
                EmailNotifier(notification).send(
                    f"{notification.get('subject_prefix', '[PiWatch]')} 测试邮件",
                    "PiWatch SMTP 配置测试成功，附件为发送测试时的摄像头画面。",
                    STATE.camera.latest_frame(),
                    "piwatch-test.jpg",
                )
                return self.json({"ok": True})
            if path == "/api/v1/events":
                payload = self.body()
                event_id = STATE.db.create_event(payload.get("type", "manual"), payload.get("recording_id"), payload.get("details"))
                return self.json({"id": event_id}, HTTPStatus.CREATED)
        except RuntimeError as exc:
            return self.json({"error": {"code": str(exc), "message": "操作失败"}}, HTTPStatus.CONFLICT)
        except Exception as exc:
            return self.json({"error": {"code": "OPERATION_FAILED", "message": str(exc)}}, HTTPStatus.BAD_REQUEST)
        return self.json({"error": {"code": "NOT_FOUND", "message": "资源不存在"}}, HTTPStatus.NOT_FOUND)

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/api/v1/events/"):
            try:
                event_id = int(path.rsplit("/", 1)[1])
                payload = self.body()
                if "protected" in payload:
                    STATE.db.set_event_protected(event_id, bool(payload["protected"]))
                return self.json({"ok": True})
            except Exception as exc:
                return self.json({"error": {"code": "INVALID_EVENT", "message": str(exc)}}, HTTPStatus.BAD_REQUEST)
        return self.json({"error": {"code": "NOT_FOUND", "message": "资源不存在"}}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/v1/recordings/"):
            try:
                recording_id = int(path.rsplit("/", 1)[1])
                row = STATE.db.get_recording(recording_id)
                if not row:
                    raise ValueError("recording_not_found")
                if row["status"] == "recording" or recording_id == STATE.recorder.current_recording_id:
                    raise ValueError("recording_is_active")
                file_path = Path(row["file_path"]).resolve()
                root = RECORDINGS_DIR.resolve()
                if file_path != root and root not in file_path.parents:
                    raise ValueError("recording_path_outside_storage")
                if file_path.exists():
                    file_path.unlink()
                STATE.db.delete_recording(recording_id)
                return self.json({"ok": True})
            except Exception as exc:
                return self.json({"error": {"code": "DELETE_FAILED", "message": str(exc)}}, HTTPStatus.BAD_REQUEST)
        return self.json({"error": {"code": "NOT_FOUND", "message": "资源不存在"}}, HTTPStatus.NOT_FOUND)

    def body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def file(self, path: Path, content_type: str):
        if not path.exists():
            return self.json({"error": {"code": "NOT_FOUND", "message": "资源不存在"}}, HTTPStatus.NOT_FOUND)
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def raw(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def video(self, recording_id: int):
        row = STATE.db.get_recording(recording_id)
        if not row:
            raise ValueError("recording_not_found")
        path = Path(row["file_path"])
        if not path.exists() or path.suffix.lower() != ".mp4":
            raise ValueError("recording_file_not_found")
        size = path.stat().st_size
        start, end = 0, max(0, size - 1)
        range_header = self.headers.get("Range")
        status = HTTPStatus.OK
        if range_header and range_header.startswith("bytes="):
            values = range_header[6:].split("-", 1)
            if values[0]: start = max(0, int(values[0]))
            if len(values) > 1 and values[1]: end = min(end, int(values[1]))
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=60")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk: break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def stream(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        try:
            for frame in STATE.camera.frames():
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            return

    def log_message(self, *args):
        return


def validate_time(value: Any) -> str:
    text = str(value)
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError("time_must_be_hh_mm")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time_must_be_hh_mm")
    return f"{hour:02d}:{minute:02d}"




def filter_recordings(items: list[dict[str, Any]], zone: str) -> list[dict[str, Any]]:
    if zone not in {"regular", "alert"}:
        return items
    return [item for item in items if item.get("storage_zone", "regular") == zone]


def run(host: str = "0.0.0.0", port: int | None = None):
    port = port or int(os.getenv("PIWATCH_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"PiWatch listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
