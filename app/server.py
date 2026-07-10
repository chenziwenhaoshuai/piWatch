from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .camera import CameraManager
from .config import STATIC_DIR, TEMPLATES_DIR
from .database import Database
from .notifications import EmailNotifier
from .storage import RecordingManager, StorageManager
from .system_monitor import SystemMonitor
from .yolo_detector import Detection, DetectionWorker

DEFAULTS: dict[str, Any] = {
    "camera": {"source_type": "csi", "device": "csi:0", "width": 1280, "height": 720, "fps": 15, "bitrate": 2500000},
    "audio": {"enabled": False},
    "storage": {"retention_days": 7, "max_used_percent": 70},
    "motion": {"enabled": True, "sensitivity": "medium", "cooldown_seconds": 300, "event_types": ["motion", "camera_disconnected", "storage_low", "temperature_high"]},
    "yolo": {"enabled": True, "model_path": "/var/lib/piwatch/models/yolo26n_ncnn_model", "confidence": 0.4, "imgsz": 416, "sample_fps": 2, "alert_cooldown_seconds": 300, "target_classes": ["person", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"], "roi": {"x": 0, "y": 0, "width": 1, "height": 1}},
    "notifications": {"enabled": False, "smtp_host": "", "smtp_port": 465, "security": "ssl", "sender": "", "username": "", "password": "", "recipient": "", "subject_prefix": "[PiWatch]"},
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
        if db.get_setting(key) is None:
            db.set_setting(key, value)


class AppState:
    def __init__(self):
        self.db = Database()
        merge_defaults(self.db)
        self.camera = CameraManager()
        self.storage = StorageManager(self.db)
        self.system = SystemMonitor()
        self.recorder = RecordingManager(self.storage)
        camera = self.db.get_setting("camera")
        self.camera.configure(camera)
        self.detector = DetectionWorker(self._detection_settings, self._on_detection, self.camera.latest_frame)
        if self.db.get_setting("yolo", {}).get("enabled"):
            self.detector.start()

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
            current = self.db.get_setting(key, {})
            current.update(value)
            self.db.set_setting(key, current)
        camera = self.db.get_setting("camera")
        self.camera.configure(camera)
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
            "recording_active": self.recorder.active,
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
        event_id = self.db.create_event("yolo_target_detected", None, details)
        motion = self.db.get_setting("motion", {})
        notification = self.db.get_setting("notifications", {})
        if "yolo_target_detected" not in motion.get("event_types", []) or not notification.get("enabled"):
            return
        labels = ", ".join(f"{d.label} ({d.confidence:.2f})" for d in detections)
        try:
            EmailNotifier(notification).send(
                f"{notification.get('subject_prefix', '[PiWatch]')} YOLO 目标报警",
                f"检测到目标：{labels}\n事件 ID：{event_id}\nROI：{details['roi']}",
            )
        except Exception:
            # Detection remains visible in the event log if SMTP is unavailable.
            pass


STATE = AppState()


class Handler(BaseHTTPRequestHandler):
    server_version = "PiWatch/0.1"

    def do_GET(self):
        path = urlparse(self.path).path
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
        routes = {
            "/api/v1/health": lambda: {"ok": True},
            "/api/v1/status": STATE.status,
            "/api/v1/detections": STATE.detector.detection_status,
            "/api/v1/settings": STATE.settings,
            "/api/v1/cameras": lambda: {"items": STATE.camera.list_devices()},
            "/api/v1/recordings": lambda: {"items": STATE.db.list_recordings()},
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
            if path == "/api/v1/recordings/start":
                settings = STATE.settings()
                return self.json(STATE.recorder.start(settings["camera"]["source_type"], settings["audio"].get("enabled", False)), HTTPStatus.CREATED)
            if path == "/api/v1/recordings/stop":
                return self.json(STATE.recorder.stop())
            if path == "/api/v1/notifications/test-email":
                EmailNotifier(STATE.settings()["notifications"]).send("PiWatch 测试邮件", "PiWatch SMTP 配置测试成功。")
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
