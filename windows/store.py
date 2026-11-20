import json
import os
import random
import threading
import time
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "AICallGuard"
APP_DIR.mkdir(parents=True, exist_ok=True)
REC_DIR = APP_DIR / "recordings"
MODEL_DIR = APP_DIR / "vosk-model-small-hi-0.22"
MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip"
CFG_PATH = APP_DIR / "config.json"
DATA_PATH = APP_DIR / "data.json"

_lock = threading.RLock()

DEFAULT_CFG = {
    "project_id": "",
    "api_key": "",
    "my_id": "",
    "name": "",
    "code": "3050",
    "ai_mode": True,
    "trusted": "",
    "voice": "hi-IN-SwaraNeural",
    "sounds": True,
}


def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if data is not None else default
    except Exception:
        return default


def _write(path, obj):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _load_data_unlocked():
    d = _read(DATA_PATH, {})
    if not isinstance(d, dict):
        d = {}
    if not isinstance(d.get("history"), list):
        d["history"] = []
    if not isinstance(d.get("notes"), list):
        d["notes"] = []
    if not isinstance(d.get("read"), dict):
        d["read"] = {}
    if not isinstance(d.get("peers"), list):
        d["peers"] = []
    return d


def load_cfg():
    with _lock:
        cfg = dict(DEFAULT_CFG)
        loaded = _read(CFG_PATH, {})
        if isinstance(loaded, dict):
            cfg.update(loaded)
        if not str(cfg.get("my_id") or "").strip():
            cfg["my_id"] = str(random.randint(10000000, 99999999))
            _write(CFG_PATH, cfg)
        return cfg


def save_cfg(cfg):
    with _lock:
        _write(CFG_PATH, cfg)


def load_data():
    with _lock:
        return _load_data_unlocked()


def _save_data(d):
    with _lock:
        _write(DATA_PATH, d)


def add_history(line):
    with _lock:
        d = _load_data_unlocked()
        d["history"].insert(0, line)
        d["history"] = d["history"][:50]
        _save_data(d)


def add_note(text):
    with _lock:
        d = _load_data_unlocked()
        d["notes"].insert(0, text)
        d["notes"] = d["notes"][:100]
        _save_data(d)


def delete_first_note():
    with _lock:
        d = _load_data_unlocked()
        if d["notes"]:
            d["notes"].pop(0)
            _save_data(d)


def set_read(peer, ts):
    p = str(peer or "").strip()
    if not p:
        return
    with _lock:
        d = _load_data_unlocked()
        d["read"][p] = ts
        _save_data(d)


def add_peer(peer):
    p = str(peer or "").strip()
    if not p:
        return
    with _lock:
        d = _load_data_unlocked()
        if p not in d["peers"]:
            d["peers"].append(p)
            _save_data(d)


def model_ready():
    return (MODEL_DIR / "conf").is_dir() and (MODEL_DIR / "am").is_dir()


def now_str():
    return time.strftime("%d %b %H:%M")
