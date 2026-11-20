import io
import threading
import wave

import numpy as np

try:
    import winsound
except ImportError:  # Windows ke bahar
    winsound = None

RATE = 22050
enabled = True
_cache = {}
_lock = threading.Lock()

_ROWS = [697.0, 770.0, 852.0, 941.0]
_COLS = [1209.0, 1336.0, 1477.0]
_KEYS = "123456789*0#"


def _tone(freqs, ms, vol=0.4, sweep=None):
    n = RATE * ms // 1000
    i = np.arange(n)
    s = np.zeros(n)
    for f in freqs:
        inst = f + (sweep - f) * i / n if sweep else np.full(n, f, dtype=float)
        s += np.sin(np.cumsum(2 * np.pi * inst / RATE))
    s /= len(freqs)
    attack = max(1, int(RATE * 0.004))
    a = np.minimum(1.0, i / attack)
    decay = 0.35 + 0.65 * (1 - i / n)
    fade = max(1.0, RATE * 0.010)
    end = np.minimum(1.0, (n - i) / fade)
    return (s * vol * a * decay * end * 32767).astype(np.int16)


def _gap(ms):
    return np.zeros(RATE * ms // 1000, dtype=np.int16)


def _seq(*parts):
    return np.concatenate(parts)


def _build(name):
    if name == "send":
        return _tone([600.0], 120, 0.35, 1100.0)
    if name == "receive":
        return _seq(_tone([988.0], 80), _tone([1319.0], 160))
    if name == "attach_sent":
        return _seq(_tone([784.0], 70), _tone([988.0], 70), _tone([1319.0], 130))
    if name == "attach_received":
        return _seq(_tone([1319.0], 70), _tone([988.0], 70), _tone([784.0], 130))
    if name == "download_done":
        return _seq(_tone([1047.0], 90), _tone([1568.0], 140))
    if name == "error":
        return _seq(_tone([330.0], 130), _gap(30), _tone([247.0], 220))
    if name == "call_connect":
        return _seq(_tone([659.0], 90), _tone([880.0], 90), _tone([1175.0], 170))
    if name == "call_end":
        return _seq(_tone([880.0], 90), _tone([659.0], 90), _tone([494.0], 220))
    if name == "ringback":
        return _tone([425.0], 1000, 0.25)
    return _tone([1000.0], 20, 0.25)  # tap


def _wav_bytes(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _play_bytes(data):
    try:
        winsound.PlaySound(data, winsound.SND_MEMORY)
    except Exception:
        pass


def _fire(key, make):
    if not enabled or winsound is None:
        return
    with _lock:
        data = _cache.get(key)
        if data is None:
            data = _wav_bytes(make())
            _cache[key] = data
    threading.Thread(target=_play_bytes, args=(data,), daemon=True).start()


def play(name):
    _fire(name, lambda: _build(name))


def dtmf(ch):
    i = _KEYS.find(ch)
    if i < 0:
        return
    _fire("dtmf" + ch, lambda: _tone([_ROWS[i // 3], _COLS[i % 3]], 140, 0.35))
