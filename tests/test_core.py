from pathlib import Path
from tempfile import TemporaryDirectory
from app.database import Database
from app.storage import StorageManager
from app.yolo_detector import normalize_roi

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
