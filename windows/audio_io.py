import collections
import queue
import threading
import time
import wave

import numpy as np
import sounddevice as sd

BIAS = 0x84
CLIP = 32635


def mulaw_encode(pcm: np.ndarray) -> bytes:
    x = pcm.astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    x = np.minimum(np.abs(x), CLIP) + BIAS
    exp = np.floor(np.log2(x)).astype(np.int32) - 7
    mant = (x >> (exp + 3)) & 0x0F
    out = (~(sign | (exp << 4) | mant)) & 0xFF
    return out.astype(np.uint8).tobytes()


def mulaw_decode(b: bytes) -> np.ndarray:
    u = (~np.frombuffer(b, dtype=np.uint8).astype(np.int32)) & 0xFF
    sign = u & 0x80
    exp = (u >> 4) & 0x07
    mant = u & 0x0F
    s = (((mant << 3) + BIAS) << exp) - BIAS
    return np.where(sign != 0, -s, s).astype(np.int16)


class Mic:
    def __init__(self, on_frame):
        self.on_frame = on_frame
        self.stream = None

    def start(self):
        self.stream = sd.InputStream(
            samplerate=16000, channels=1, dtype="int16", blocksize=320, callback=self._cb
        )
        self.stream.start()

    def _cb(self, indata, frames, time_info, status):
        try:
            self.on_frame(indata[:, 0].copy())
        except Exception:
            pass

    def stop(self):
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        self.stream = None


class Speaker:
    def __init__(self):
        self.q = queue.Queue(maxsize=30)
        self.stream = None
        self.primed = False

    def start(self):
        self.stream = sd.OutputStream(
            samplerate=16000, channels=1, dtype="int16", blocksize=320, callback=self._cb
        )
        self.stream.start()

    def _cb(self, outdata, frames, time_info, status):
        if not self.primed:
            if self.q.qsize() < 3:
                outdata.fill(0)
                return
            self.primed = True
        try:
            f = self.q.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            self.primed = False
            return
        n = min(len(f), frames)
        outdata[:n, 0] = f[:n]
        if n < frames:
            outdata[n:] = 0

    def push(self, f):
        try:
            self.q.put_nowait(f)
        except queue.Full:
            try:
                self.q.get_nowait()
            except Exception:
                pass
            try:
                self.q.put_nowait(f)
            except Exception:
                pass

    def stop(self):
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        self.stream = None


class Recorder:
    """Dono taraf ki awaaz mix karke WAV mein likhta hai."""

    def __init__(self, path):
        self.path = path
        self.local = collections.deque(maxlen=100)
        self.remote = collections.deque(maxlen=100)
        self.running = False
        self.th = None
        self.w = None

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.w = wave.open(str(self.path), "wb")
        self.w.setnchannels(1)
        self.w.setsampwidth(2)
        self.w.setframerate(16000)
        self.running = True
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def add_local(self, f):
        if self.running:
            self.local.append(f)

    def add_remote(self, f):
        if self.running:
            self.remote.append(f)

    def _loop(self):
        nxt = time.perf_counter()
        while self.running:
            a = self.local.popleft() if self.local else None
            b = self.remote.popleft() if self.remote else None
            mix = np.zeros(320, np.int32)
            if a is not None:
                mix[: len(a)] += a[:320].astype(np.int32)
            if b is not None:
                mix[: len(b)] += b[:320].astype(np.int32)
            try:
                self.w.writeframes(np.clip(mix, -32768, 32767).astype(np.int16).tobytes())
            except Exception:
                break
            nxt += 0.02
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.perf_counter()

    def stop(self):
        self.running = False
        try:
            if self.th:
                self.th.join(1)
        except Exception:
            pass
        try:
            if self.w:
                self.w.close()
        except Exception:
            pass
