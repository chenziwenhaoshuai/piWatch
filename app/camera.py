from __future__ import annotations
from pathlib import Path
from shutil import which
import subprocess
class CameraManager:
    def __init__(self): self.selected_type, self.device, self.process = "usb", "/dev/video0", None
    def list_devices(self):
        devices = [{"source_type":"usb","device":str(p),"label":p.name} for p in sorted(Path("/dev").glob("video*"))] if Path("/dev").exists() else []
        if which("rpicam-hello") or which("libcamera-hello"): devices.append({"source_type":"csi","device":"csi:0","label":"CSI Camera 0"})
        return devices or [{"source_type":"csi","device":"csi:0","label":"CSI Camera 0 (候选)"},{"source_type":"usb","device":"/dev/video0","label":"USB Camera 0 (候选)"}]
    def select(self, source_type, device=None):
        if source_type not in {"csi","usb"}: raise ValueError("camera_type_must_be_csi_or_usb")
        self.selected_type, self.device = source_type, device or ("csi:0" if source_type == "csi" else "/dev/video0")
    def status(self): return {"source_type":self.selected_type,"device":self.device,"connected":self.connected(),"streaming":bool(self.process and self.process.poll() is None)}
    def connected(self): return (Path(self.device).exists() if self.selected_type == "usb" else bool(which("rpicam-hello") or which("libcamera-hello")))
