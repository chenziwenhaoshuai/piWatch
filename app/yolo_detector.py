from __future__ import annotations

import threading
import time
import subprocess
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
        try:
            import ultralytics  # noqa: F401
            import cv2  # noqa: F401
            return True
        except ImportError:
            return False

    def load(self) -> None:
        if self.model is not None:
            return
        try:
            from ultralytics import YOLO

            self.model = YOLO(self.config.get("model_path", "yolo11n.pt"))
            names = getattr(self.model, "names", {})
            self.names = {int(key): str(value) for key, value in names.items()} if isinstance(names, dict) else {}
            self.error = None
        except Exception as exc:  # Model download and hardware errors are runtime configuration errors.
            self.error = str(exc)
            raise

    def detect(self, frame: Any) -> list[Detection]:
        self.load()
        height, width = frame.shape[:2]
        roi = normalize_roi(self.config.get("roi"), width, height)
        x1, y1, x2, y2 = roi
        crop = frame[y1:y2, x1:x2]
        results = self.model.predict(
            source=crop,
            conf=float(self.config.get("confidence", 0.5)),
            imgsz=int(self.config.get("imgsz", 640)),
            verbose=False,
        )
        targets = {str(item).strip().lower() for item in self.config.get("target_classes", []) if str(item).strip()}
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
    return int(x * width), int(y * height), max(int((x + w) * width), 1), max(int((y + h) * height), 1)


class DetectionWorker:
    def __init__(self, settings_getter: Callable[[], dict[str, Any]], event_callback: Callable[[list[Detection]], None]):
        self.settings_getter = settings_getter
        self.event_callback = event_callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_detections: list[Detection] = []
        self.last_alert_at = 0.0

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
            "target_classes": config.get("target_classes", []),
            "last_detections": [d.__dict__ for d in self.last_detections],
            "error": self.last_error,
        }

    def _run(self) -> None:
        try:
            import cv2
        except ImportError:
            self.last_error = "opencv_not_installed"
            return
        capture = None
        detector: YoloDetector | None = None
        detector_config: dict[str, Any] | None = None
        try:
            while not self._stop.is_set():
                config = self.settings_getter()
                if not config.get("enabled"):
                    time.sleep(1)
                    continue
                if detector_config != config:
                    detector = YoloDetector(config)
                    detector_config = dict(config)
                if config.get("source_type", "usb") == "csi":
                    if capture is None:
                        capture = subprocess.Popen(
                            ["rpicam-vid", "-t", "0", "--width", str(config.get("width", 1280)), "--height", str(config.get("height", 720)), "--framerate", str(config.get("fps", 15)), "--codec", "mjpeg", "-o", "-"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                        )
                    ok, frame = _read_mjpeg_frame(capture.stdout, cv2)
                else:
                    if capture is None:
                        capture = cv2.VideoCapture(config.get("device", "/dev/video0"))
                    ok, frame = capture.read()
                if not ok:
                    self.last_error = "camera_frame_unavailable"
                    if hasattr(capture, "release"):
                        capture.release()
                    else:
                        capture.terminate()
                    capture = None
                    time.sleep(2)
                    continue
                detections = detector.detect(frame) if detector else []
                self.last_detections = detections
                now = time.monotonic()
                cooldown = max(0, int(config.get("alert_cooldown_seconds", 300)))
                if detections and now - self.last_alert_at >= cooldown:
                    self.last_alert_at = now
                    self.event_callback(detections)
                time.sleep(max(0.05, 1 / max(1, int(config.get("sample_fps", 2)))))
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            if capture is not None:
                if hasattr(capture, "release"):
                    capture.release()
                else:
                    capture.terminate()


def _read_mjpeg_frame(stream: Any, cv2: Any) -> tuple[bool, Any]:
    if stream is None:
        return False, None
    buffer = b""
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return False, None
        buffer += chunk
        start = buffer.find(b"\xff\xd8")
        end = buffer.find(b"\xff\xd9", start + 2)
        if start >= 0 and end >= 0:
            frame = cv2.imdecode(__import__("numpy").frombuffer(buffer[start:end + 2], dtype="uint8"), cv2.IMREAD_COLOR)
            return frame is not None, frame
