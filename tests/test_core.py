from pathlib import Path
from tempfile import TemporaryDirectory
from app.database import Database
from app.storage import StorageManager, in_daily_window
from datetime import datetime
from app.yolo_detector import normalize_roi
from app.camera import CameraManager
from app.system_monitor import parse_cpu_times, parse_meminfo
from app.notifications import EmailNotifier, notification_allows

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


def test_recording_important_reasons_are_unique():
    with TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        recording_id = db.create_recording("csi", str(Path(tmp) / "clip.mp4"), False)
        db.mark_recording_important(recording_id, "motion")
        db.mark_recording_important(recording_id, "motion")
        db.mark_recording_important(recording_id, "yolo")
        recording = db.get_recording(recording_id)
        assert recording["important"] is True
        assert recording["important_reasons"] == ["motion", "yolo"]


def test_recording_filter_and_delete():
    with TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        first = db.create_recording("csi", str(Path(tmp) / "first.mp4"), False)
        second = db.create_recording("csi", str(Path(tmp) / "second.mp4"), False)
        db.mark_recording_important(second, "yolo")
        assert [item["id"] for item in db.list_recordings(10, True)] == [second]
        db.delete_recording(first)
        assert db.get_recording(first) is None


def test_daily_alert_window_supports_midnight():
    assert in_daily_window(datetime(2026, 1, 1, 23, 0), "22:00", "06:00") is True
    assert in_daily_window(datetime(2026, 1, 1, 5, 59), "22:00", "06:00") is True
    assert in_daily_window(datetime(2026, 1, 1, 12, 0), "22:00", "06:00") is False
    assert in_daily_window(datetime(2026, 1, 1, 9, 0), "08:00", "10:00") is True


def test_email_notifier_requires_configuration():
    notifier = EmailNotifier({"enabled": True, "smtp_host": "", "recipient": ""})
    assert notifier.enabled() is False


def test_notification_event_filters_and_alert_window():
    recording = {"alert_start": "22:00", "alert_end": "06:00"}
    settings = {"enabled": True, "send_motion": False, "send_yolo": True, "alert_window_only": False}
    assert notification_allows("motion", settings, recording, datetime(2026, 1, 1, 23, 0)) is False
    assert notification_allows("yolo", settings, recording, datetime(2026, 1, 1, 12, 0)) is True
    settings["alert_window_only"] = True
    assert notification_allows("yolo", settings, recording, datetime(2026, 1, 1, 12, 0)) is False
    assert notification_allows("yolo", settings, recording, datetime(2026, 1, 1, 23, 0)) is True
