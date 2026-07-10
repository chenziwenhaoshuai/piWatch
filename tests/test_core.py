from pathlib import Path
from tempfile import TemporaryDirectory
from app.database import Database
from app.storage import StorageManager
from app.yolo_detector import normalize_roi
from app.camera import CameraManager
from app.system_monitor import parse_cpu_times, parse_meminfo

def test_settings_and_events():
    with TemporaryDirectory() as tmp:
        db=Database(Path(tmp)/'test.db'); db.set_setting('camera',{'source_type':'csi'}); assert db.get_setting('camera')['source_type']=='csi'; event_id=db.create_event('motion',None,{'x':1}); assert db.list_events()[0]['id']==event_id

def test_storage_status():
    with TemporaryDirectory() as tmp:
        db=Database(Path(tmp)/'test.db'); assert StorageManager(db).disk_status()['total_bytes']>0


def test_normalize_roi_clamps_to_frame():
    assert normalize_roi({"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}, 1280, 720) == (320, 180, 960, 540)
    x1, y1, x2, y2 = normalize_roi({"x": 2, "y": -1, "width": 2, "height": 2}, 100, 80)
    assert 0 <= x1 < x2 <= 100
    assert 0 <= y1 < y2 <= 80


def test_camera_configuration_is_bounded():
    camera = CameraManager()
    camera.configure({"source_type": "csi", "device": "csi:0", "width": 9999, "height": 1, "fps": 100})
    assert (camera.width, camera.height, camera.fps) == (3840, 240, 60)


def test_system_monitor_parsers():
    assert parse_cpu_times("cpu  10 2 3 20 5 1 1 0\n") == (42, 25)
    memory = parse_meminfo("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
    assert memory["used_bytes"] == 750 * 1024
    assert memory["used_percent"] == 75.0
