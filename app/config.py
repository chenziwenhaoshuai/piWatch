from pathlib import Path
import os
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PIWATCH_DATA_DIR", BASE_DIR / "data"))
RECORDINGS_DIR = Path(os.getenv("PIWATCH_RECORDINGS_DIR", BASE_DIR / "recordings"))
DB_PATH = DATA_DIR / "piwatch.db"
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
for directory in (DATA_DIR, RECORDINGS_DIR, STATIC_DIR, TEMPLATES_DIR):
    directory.mkdir(parents=True, exist_ok=True)
