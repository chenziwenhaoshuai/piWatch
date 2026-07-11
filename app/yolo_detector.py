from __future__ import annotations

import threading
import time
import importlib.util
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class Detection:
    label: str
    confidence: float
    box: tuple[int, int, int, int]


class YoloDetector:
    """Lazy YOLO adapter. The heavy ultralytics import only happens when detection starts."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.model = None
        self.names: dict[int, str] = {}
        self.error: str | None = None

    @property
    def available(self) -> bool:
        return importlib.util.find_spec("ultralytics") is not None and importlib.util.find_spec("cv2") is not None

    def load(self) -> None:
        if self.model is not None:
            return
        try:
            from ultralytics import YOLO

            self.model = YOLO(self.config.get("model_path", "yolo26n.pt"), task="detect")
            names = getattr(self.model, "names", {})
            self.names = {int(key): str(value) for key, value in names.items()} if isinstance(names, dict) else {}
            self.error = None
        except Exception as exc:  # Model download and hardware errors are runtime configuration errors.
            self.error = str(exc)
            raise

    def detect(self, frame: Any, config: dict[str, Any] | None = None) -> list[Detection]:
        if config is not None:
            self.config = config
        self.load()
        height, width = frame.shape[:2]
        roi = normalize_roi(self.config.get("roi"), width, height)
        x1, y1, x2, y2 = roi
        crop = frame[y1:y2, x1:x2]
        targets = {str(item).strip().lower() for item in self.config.get("target_classes", []) if str(item).strip()}
        class_ids = [class_id for class_id, label in self.names.items() if label.lower() in targets or str(class_id) in targets]
        results = self.model.predict(
            source=crop,
            conf=float(self.config.get("confidence", 0.5)),
            imgsz=int(self.config.get("imgsz", 640)),
            classes=class_ids or None,
            verbose=False,
        )
        detections: list[Detection] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                class_id = int(box.cls[0].item())
                label = self.names.get(class_id, str(class_id))
                confidence = float(box.conf[0].item())
                if targets and label.lower() not in targets and str(class_id) not in targets:
                    continue
                coords = [int(value) for value in box.xyxy[0].tolist()]
                detections.append(Detection(label, confidence, (coords[0] + x1, coords[1] + y1, coords[2] + x1, coords[3] + y1)))
        return detections


def normalize_roi(value: Any, width: int, height: int) -> tuple[int, int, int, int]:
    """Convert a normalized x/y/width/height ROI into a safe pixel rectangle."""
    if not isinstance(value, dict):
        return 0, 0, width, height
    x = max(0.0, min(1.0, float(value.get("x", 0))))
    y = max(0.0, min(1.0, float(value.get("y", 0))))
    w = max(0.01, min(1.0 - x, float(value.get("width", 1))))
    h = max(0.01, min(1.0 - y, float(value.get("height", 1))))
    x1 = min(int(x * width), width - 1)
    y1 = min(int(y * height), height - 1)
    x2 = min(width, max(x1 + 1, int((x + w) * width)))
    y2 = min(height, max(y1 + 1, int((y + h) * height)))
    return x1, y1, x2, y2


class ConsecutiveDetectionGate:
    def __init__(self):
        self.count = 0

    def update(self, detections: list[Detection], required_hits: int) -> bool:
        if detections:
            self.count += 1
        else:
            self.count = 0
        return bool(detections) and self.count >= max(1, int(required_hits))


class DetectionWorker:
    def __init__(
        self,
        settings_getter: Callable[[], dict[str, Any]],
        event_callback: Callable[[list[Detection], bool], None],
        frame_getter: Callable[[], bytes | None] | None = None,
    ):
        self.settings_getter = settings_getter
        self.event_callback = event_callback
        self.frame_getter = frame_getter
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_detections: list[Detection] = []
        self.last_alert_at = 0.0
        self.last_inference_ms: float | None = None
        self.actual_fps: float | None = None
        self._last_completed_at: float | None = None
        self._alert_gate = ConsecutiveDetectionGate()
        self.last_frame_size: tuple[int, int] | None = None
        self.last_updated_at: float | None = None
        self.current_config: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="piwatch-yolo", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None

    def status(self) -> dict[str, Any]:
        config = self.settings_getter()
        detector = YoloDetector(config)
        return {
            "enabled": bool(config.get("enabled")),
            "running": self.running,
            "available": detector.available,
            "model_path": config.get("model_path"),
            "imgsz": int(config.get("imgsz", 640)),
            "sample_fps": max(0, int(config.get("sample_fps", 2))),
            "target_classes": config.get("target_classes", []),
            "last_detections": [d.__dict__ for d in self.last_detections],
            "last_inference_ms": self.last_inference_ms,
            "actual_fps": self.actual_fps,
            "frame_size": list(self.last_frame_size) if self.last_frame_size else None,
            "updated_at": self.last_updated_at,
            "error": self.last_error,
        }

    def detection_status(self) -> dict[str, Any]:
        config = self.current_config
        return {
            "enabled": bool(config.get("enabled")),
            "running": self.running,
            "model_path": config.get("model_path"),
            "imgsz": int(config.get("imgsz", 640)),
            "sample_fps": max(0, int(config.get("sample_fps", 2))),
            "last_detections": [d.__dict__ for d in self.last_detections],
            "last_inference_ms": self.last_inference_ms,
            "actual_fps": self.actual_fps,
            "frame_size": list(self.last_frame_size) if self.last_frame_size else None,
            "updated_at": self.last_updated_at,
            "error": self.last_error,
        }

    def _run(self) -> None:
        try:
            import cv2
        except ImportError:
            self.last_error = "opencv_not_installed"
            return
        detectors: dict[str, YoloDetector] = {}
        try:
            while not self._stop.is_set():
                config = self.settings_getter()
                self.current_config = dict(config)
                if not config.get("enabled"):
                    time.sleep(1)
                    continue
                model_path = str(config.get("model_path", "yolo26n.pt"))
                detector = detectors.get(model_path)
                if detector is None:
                    detector = YoloDetector(config)
                    detectors[model_path] = detector
                if config.get("source_type", "usb") == "csi":
                    payload = self.frame_getter() if self.frame_getter else None
                    frame = cv2.imdecode(__import__("numpy").frombuffer(payload, dtype="uint8"), cv2.IMREAD_COLOR) if payload else None
                    ok = frame is not None
                else:
                    capture = cv2.VideoCapture(config.get("device", "/dev/video0"))
                    ok, frame = capture.read()
                    capture.release()
                if not ok:
                    self.last_error = "camera_frame_unavailable"
                    time.sleep(0.5)
                    continue
                started = time.monotonic()
                detections = detector.detect(frame, config)
                inference_seconds = time.monotonic() - started
                self.last_inference_ms = round(inference_seconds * 1000, 1)
                completed_at = time.monotonic()
                if self._last_completed_at is not None and completed_at > self._last_completed_at:
                    self.actual_fps = round(1 / (completed_at - self._last_completed_at), 2)
                self._last_completed_at = completed_at
                self.last_frame_size = (int(frame.shape[1]), int(frame.shape[0]))
                self.last_updated_at = time.time()
                self.last_error = None
                self.last_detections = detections
                required_hits = max(1, int(config.get("alert_consecutive_frames", 3)))
                now = time.monotonic()
                cooldown = max(0, int(config.get("alert_cooldown_seconds", 300)))
                if self._alert_gate.update(detections, required_hits):
                    should_notify = now - self.last_alert_at >= cooldown
                    if should_notify:
                        self.last_alert_at = now
                    self.event_callback(detections, should_notify)
                sample_fps = max(0, int(config.get("sample_fps", 2)))
                if sample_fps:
                    target_period = 1 / sample_fps
                    time.sleep(max(0.001, target_period - inference_seconds))
        except Exception as exc:
            self.last_error = traceback.format_exc(limit=8)
        finally:
            return
