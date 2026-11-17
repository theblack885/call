import json
import os
import random
import time
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "AICallGuard"
APP_DIR.mkdir(parents=True, exist_ok=True)
REC_DIR = APP_DIR / "recordings"
MODEL_DIR = APP_DIR / "vosk-model-small-hi-0.22"
MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip"
CFG_PATH = APP_DIR / "config.json"
DATA_PATH = APP_DIR / "data.json"

DEFAULT_CFG = {
    "project_id": "",
    "api_key": "",
    "my_id": "",
    "name": "",
    "code": "3050",
    "ai_mode": True,
    "trusted": "",
    "voice": "hi-IN-SwaraNeural",
}


def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    cfg.update(_read(CFG_PATH, {}))
    if not cfg["my_id"]:
        cfg["my_id"] = str(random.randint(10000000, 99999999))
        _write(CFG_PATH, cfg)
    return cfg


def save_cfg(cfg):
    _write(CFG_PATH, cfg)


def load_data():
    d = _read(DATA_PATH, {})
    d.setdefault("history", [])
    d.setdefault("notes", [])
    return d


def add_history(line):
    d = load_data()
    d["history"].insert(0, line)
    d["history"] = d["history"][:50]
    _write(DATA_PATH, d)


def add_note(text):
    d = load_data()
    d["notes"].insert(0, text)
    d["notes"] = d["notes"][:100]
    _write(DATA_PATH, d)


def delete_first_note():
    d = load_data()
    if d["notes"]:
        d["notes"].pop(0)
        _write(DATA_PATH, d)


def model_ready():
    return (MODEL_DIR / "conf").is_dir() and (MODEL_DIR / "am").is_dir()


def now_str():
    return time.strftime("%d %b %H:%M")
