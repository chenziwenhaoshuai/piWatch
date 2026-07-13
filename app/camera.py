from __future__ import annotations

from pathlib import Path
from queue import Full, Queue
from shutil import which
import subprocess
import threading
from typing import Iterator


class CameraManager:
    def __init__(self):
        self.selected_type = "csi"
        self.device = "csi:0"
        self.width = 1280
        self.height = 720
        self.fps = 15
        self._lifecycle_lock = threading.RLock()
        self.process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._condition = threading.Condition()
        self._latest_frame: bytes | None = None
        self._frame_sequence = 0
        self._recording_queue: Queue[bytes] = Queue(maxsize=600)
        self._last_error: str | None = None
        self._stopping = False

    def list_devices(self):
        devices = (
            [
                {"source_type": "usb", "device": str(path), "label": path.name}
                for path in sorted(Path("/dev").glob("video*"))
            ]
            if Path("/dev").exists()
            else []
        )
        if self._camera_command():
            devices.insert(0, {"source_type": "csi", "device": "csi:0", "label": "CSI Camera 0"})
        return devices or [
            {"source_type": "csi", "device": "csi:0", "label": "CSI Camera 0 (candidate)"},
            {"source_type": "usb", "device": "/dev/video0", "label": "USB Camera 0 (candidate)"},
        ]

    def configure(self, settings: dict) -> None:
        with self._lifecycle_lock:
            source_type = settings.get("source_type", "csi")
            if source_type not in {"csi", "usb"}:
                raise ValueError("camera_type_must_be_csi_or_usb")
            device = settings.get("device") or ("csi:0" if source_type == "csi" else "/dev/video0")
            width = max(320, min(3840, int(settings.get("width", 1280))))
            height = max(240, min(2160, int(settings.get("height", 720))))
            fps = max(1, min(60, int(settings.get("fps", 15))))
            changed = (source_type, device, width, height, fps) != (
                self.selected_type,
                self.device,
                self.width,
                self.height,
                self.fps,
            )
            if changed:
                self.stop_preview()
            self.selected_type, self.device = source_type, device
            self.width, self.height, self.fps = width, height, fps

    def select(self, source_type: str, device: str | None = None) -> None:
        self.configure({"source_type": source_type, "device": device})

    def status(self):
        return {
            "source_type": self.selected_type,
            "device": self.device,
            "connected": self.connected(),
            "streaming": bool(self.process and self.process.poll() is None),
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "error": self._last_error,
        }

    def connected(self):
        if self.selected_type == "usb":
            return Path(self.device).exists()
        return bool(self._camera_command())

    def frames(self) -> Iterator[bytes]:
        self._ensure_preview()
        sequence = -1
        while True:
            with self._condition:
                ready = self._condition.wait_for(
                    lambda: self._frame_sequence != sequence or self._last_error is not None,
                    timeout=10,
                )
                if not ready:
                    raise RuntimeError("camera_frame_timeout")
                if self._last_error and self._latest_frame is None:
                    raise RuntimeError(self._last_error)
                sequence = self._frame_sequence
                frame = self._latest_frame
            if frame:
                yield frame

    def recording_frames(self) -> Iterator[bytes]:
        self._ensure_preview()
        while True:
            frame = self._recording_queue.get(timeout=10)
            yield frame

    def snapshot(self, timeout: float = 10) -> bytes:
        self._ensure_preview()
        with self._condition:
            sequence = self._frame_sequence
            ready = self._condition.wait_for(
                lambda: self._frame_sequence != sequence or self._latest_frame is not None or self._last_error,
                timeout=timeout,
            )
            if not ready or self._latest_frame is None:
                raise RuntimeError(self._last_error or "camera_frame_timeout")
            return self._latest_frame

    def latest_frame(self) -> bytes | None:
        self._ensure_preview()
        with self._condition:
            return self._latest_frame

    def stop_preview(self) -> None:
        with self._lifecycle_lock:
            self._stopping = True
            process = self.process
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            thread = self._thread
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=3)
            with self._condition:
                self.process = None
                self._thread = None
                self._latest_frame = None
                self._frame_sequence = 0
                self._recording_queue = Queue(maxsize=600)
                self._last_error = None
                self._condition.notify_all()
            self._stopping = False

    def _camera_command(self) -> str | None:
        return which("rpicam-vid") or which("libcamera-vid")

    def _ensure_preview(self) -> None:
        with self._lifecycle_lock:
            with self._condition:
                if self.process and self.process.poll() is None and self._thread and self._thread.is_alive():
                    return
                if self.selected_type != "csi":
                    raise RuntimeError("usb_preview_not_implemented")
                command = self._camera_command()
                if not command:
                    raise RuntimeError("rpicam_vid_not_installed")
                self._latest_frame = None
                self._frame_sequence = 0
                self._last_error = None
                self._stopping = False
                self.process = subprocess.Popen(
                    [
                        command,
                        "--timeout",
                        "0",
                        "--nopreview",
                        "--width",
                        str(self.width),
                        "--height",
                        str(self.height),
                        "--framerate",
                        str(self.fps),
                        "--codec",
                        "mjpeg",
                        "--quality",
                        "80",
                        "--output",
                        "-",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self._thread = threading.Thread(target=self._read_frames, name="piwatch-camera", daemon=True)
                self._thread.start()

    def _read_frames(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        buffer = bytearray()
        try:
            while not self._stopping:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        if len(buffer) > 1:
                            del buffer[:-1]
                        break
                    end = buffer.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        if start:
                            del buffer[:start]
                        break
                    frame = bytes(buffer[start : end + 2])
                    del buffer[: end + 2]
                    self._publish_recording_frame(frame)
                    with self._condition:
                        self._latest_frame = frame
                        self._frame_sequence += 1
                        self._condition.notify_all()
            if not self._stopping:
                error = "camera_process_exited"
                if process.stderr:
                    detail = process.stderr.read().decode("utf-8", errors="replace").strip()
                    if detail:
                        error = detail[-1000:]
                with self._condition:
                    self._last_error = error
                    self._condition.notify_all()
        except Exception as exc:
            with self._condition:
                self._last_error = str(exc)
                self._condition.notify_all()

    def _publish_recording_frame(self, frame: bytes) -> None:
        try:
            self._recording_queue.put_nowait(frame)
        except Full:
            try:
                self._recording_queue.get_nowait()
            except Exception:
                pass
            try:
                self._recording_queue.put_nowait(frame)
            except Full:
                pass
