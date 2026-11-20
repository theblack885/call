import base64
import datetime
import io
import re
import threading
import time
import uuid

import sounds
import store
from fs import Firestore

MAX_BYTES = 8 * 1024 * 1024
CHUNK = 700_000
CACHE_DIR = store.APP_DIR / "cache"
UTC = datetime.timezone.utc


def parse_ts(s):
    if not s:
        return None
    try:
        s = re.sub(r"(\.\d{6})\d+", r"\1", str(s))
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_ts(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def preview(m):
    t = m["type"]
    if t == "image":
        return "📷 फ़ोटो"
    if t == "video":
        return "🎥 वीडियो"
    if t == "file":
        return "📎 " + (m["name"] or "फ़ाइल")
    return m["text"]


def fmt_size(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n // 1024} KB"
    return f"{n} B"


class Chat:
    """Android ऐप वाली चैट से जुड़ी चैट (Firebase Firestore, REST से)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.msgs = {}
        self.thumbs = {}
        self._thumb_loading = set()
        self.uploads = {}
        self.events = []
        self.active_peer = ""
        self.version = 0
        self.read_ver = 0
        self.error = ""
        self.running = False
        self.wake = threading.Event()
        self.fs = None
        self._conf = None
        self._first = True
        self.cursor = None
        self.notified = time.time()
        d = store.load_data()
        self.read = dict(d.get("read", {}))
        self.extra = list(d.get("peers", []))

    # ---------- setup ----------
    def configure(self):
        pid = self.cfg["project_id"].strip()
        key = self.cfg["api_key"].strip()
        if not pid:
            self.fs = None
            return
        if (pid, key) != self._conf:
            self._conf = (pid, key)
            with self.lock:
                self.msgs.clear()
                self.thumbs.clear()
            self.fs = Firestore(pid, key)
            self._first = True
        if not self.running:
            self.running = True
            threading.Thread(target=self._loop, daemon=True).start()
        self.wake.set()

    def _mk(self):
        return Firestore(*self._conf)

    def poll_now(self):
        self.wake.set()

    # ---------- firestore helpers ----------
    def _all_message_docs(self):
        fs = self.fs
        if fs is None:
            return []
        for attr in ("list_docs", "list_collection", "query", "get"):
            fn = getattr(fs, attr, None)
            if not callable(fn):
                continue
            try:
                res = fn("messages")
                if isinstance(res, dict):
                    docs = res.get("documents") or res.get("items") or res.get("data") or []
                    if isinstance(docs, list):
                        return [d for d in docs if isinstance(d, dict)]
                    out = []
                    for k, v in res.items():
                        if isinstance(v, dict):
                            item = dict(v)
                            item.setdefault("_id", str(k))
                            out.append(item)
                    if out:
                        return out
                elif isinstance(res, list):
                    return [d for d in res if isinstance(d, dict)]
            except Exception:
                continue
        return []

    def _iter_messages_for(self, user_id, field):
        fs = self.fs
        if fs is None:
            return []
        fn = getattr(fs, "messages_for", None)
        if callable(fn):
            return fn(user_id, field) or []
        return [
            d for d in self._all_message_docs()
            if str(d.get(field, "")) == str(user_id)
        ]

    def _iter_messages_since(self, since_str):
        fs = self.fs
        if fs is None:
            return []
        fn = getattr(fs, "messages_since", None)
        if callable(fn):
            return fn(since_str) or []
        since_dt = parse_ts(since_str)
        out = []
        for d in self._all_message_docs():
            dt = parse_ts(d.get("createdAt"))
            if since_dt is None or dt is None or dt >= since_dt:
                out.append(d)
        out.sort(
            key=lambda item: parse_ts(item.get("createdAt"))
            or datetime.datetime.min.replace(tzinfo=UTC)
        )
        return out

    @staticmethod
    def _create_doc(fs, collection, payload):
        for fn_name in ("create_call", "create", "add", "create_doc", "post"):
            fn = getattr(fs, fn_name, None)
            if callable(fn):
                return fn(collection, payload)
        raise AttributeError("'Firestore' object has no create method for documents")

    # ---------- polling ----------
    def _loop(self):
        while self.running:
            try:
                if self.fs is None:
                    time.sleep(1)
                    continue
                if self._first:
                    self._load_all()
                    self._first = False
                else:
                    self._poll_once()
                self.error = ""
            except Exception as e:
                self.error = str(e)
            self.wake.wait(2.0 if self.active_peer else 4.0)
            self.wake.clear()

    def _add(self, d):
        mid = str(d.get("_id") or d.get("id") or uuid.uuid4().hex)
        with self.lock:
            if mid in self.msgs:
                return None
            dt = parse_ts(d.get("createdAt"))
            m = {
                "id": mid,
                "from": d.get("from", ""),
                "to": d.get("to", ""),
                "type": d.get("type", "text"),
                "text": d.get("text", ""),
                "name": d.get("name", ""),
                "mime": d.get("mime", ""),
                "size": int(d.get("size") or 0),
                "chunks": int(d.get("chunks") or 0),
                "blobId": d.get("blobId", ""),
                "fromName": d.get("fromName", ""),
                "ts": dt.timestamp() if dt else time.time(),
                "dt": dt,
            }
            self.msgs[mid] = m
            self.version += 1
            return m

    def _load_all(self):
        me = self.cfg["my_id"]
        for field in ("to", "from"):
            for d in self._iter_messages_for(me, field):
                self._add(d)
        with self.lock:
            dts = [m["dt"] for m in self.msgs.values() if m["dt"]]
            incoming = [m["ts"] for m in self.msgs.values() if m["to"] == me]
        self.notified = max(incoming) if incoming else time.time()
        if dts:
            self.cursor = max(dts) - datetime.timedelta(seconds=5)
        else:
            self.cursor = datetime.datetime.now(UTC) - datetime.timedelta(hours=1)

    def _poll_once(self):
        me = self.cfg["my_id"]
        for _ in range(10):
            docs = self._iter_messages_since(fmt_ts(self.cursor))
            last = None
            for d in docs:
                dt = parse_ts(d.get("createdAt"))
                if dt:
                    last = dt
                if d.get("from") != me and d.get("to") != me:
                    continue
                m = self._add(d)
                if m is not None:
                    self._on_new(m)
            if last:
                if len(docs) >= 200:
                    self.cursor = max(self.cursor, last)
                else:
                    self.cursor = max(self.cursor, last - datetime.timedelta(seconds=5))
            if len(docs) < 200:
                break

    def _on_new(self, m):
        me = self.cfg["my_id"]
        if m["to"] != me or m["ts"] <= self.notified:
            return
        self.notified = m["ts"]
        sounds.play("receive" if m["type"] == "text" else "attach_received")
        if self.active_peer == m["from"]:
            self.mark_read(m["from"])
        else:
            with self.lock:
                self.events.append({
                    "peer": m["from"],
                    "title": m["fromName"] or m["from"],
                    "text": preview(m),
                })

    def pop_events(self):
        with self.lock:
            ev = self.events
            self.events = []
        return ev

    # ---------- queries (UI) ----------
    def conversation(self, peer):
        me = self.cfg["my_id"]
        with self.lock:
            return sorted(
                (m for m in self.msgs.values()
                 if (m["from"] == peer and m["to"] == me) or (m["from"] == me and m["to"] == peer)),
                key=lambda m: m["ts"],
            )

    def peers(self):
        me = self.cfg["my_id"]
        last = {}
        with self.lock:
            for m in self.msgs.values():
                p = m["to"] if m["from"] == me else m["from"]
                if p:
                    last[p] = max(last.get(p, 0), m["ts"])
        for p in self.extra:
            last.setdefault(p, 0)
        return [p for p, _ in sorted(last.items(), key=lambda x: -x[1])]

    def peer_name(self, peer):
        with self.lock:
            c = [m for m in self.msgs.values() if m["from"] == peer and m["fromName"]]
        return max(c, key=lambda m: m["ts"])["fromName"] if c else ""

    def unread(self, peer):
        me = self.cfg["my_id"]
        r = float(self.read.get(peer, 0))
        with self.lock:
            return sum(1 for m in self.msgs.values() if m["from"] == peer and m["to"] == me and m["ts"] > r)

    def mark_read(self, peer):
        me = self.cfg["my_id"]
        with self.lock:
            ts = [m["ts"] for m in self.msgs.values() if m["from"] == peer and m["to"] == me]
        if ts and max(ts) > float(self.read.get(peer, 0)):
            self.read[peer] = max(ts)
            store.set_read(peer, max(ts))
            self.read_ver += 1

    def add_peer(self, peer):
        if peer not in self.extra:
            self.extra.append(peer)
            store.add_peer(peer)
            self.read_ver += 1

    def uploads_for(self, peer):
        return [u for u in list(self.uploads.values()) if u["peer"] == peer]

    # ---------- send ----------
    def _base(self, peer, typ):
        return {"from": self.cfg["my_id"], "to": peer, "type": typ, "fromName": self.cfg["name"]}

    def send_text(self, peer, text):
        t = text.strip()
        if not t or self._conf is None:
            return
        sounds.play("send")

        def run():
            try:
                self._create_doc(self._mk(), "messages", {**self._base(peer, "text"), "text": t})
                self.wake.set()
            except Exception as e:
                self.error = str(e)
                sounds.play("error")

        threading.Thread(target=run, daemon=True).start()

    def send_path(self, peer, path, kind):
        if self._conf is None:
            return

        def run():
            try:
                self._send_path(peer, path, kind)
            except Exception as e:
                self.error = str(e)
                sounds.play("error")

        threading.Thread(target=run, daemon=True).start()

    def _send_path(self, peer, path, kind):
        import os

        name = os.path.basename(path)
        thumb = ""
        mime = "application/octet-stream"
        if kind == "image":
            from PIL import Image, ImageOps

            im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            im.thumbnail((1280, 1280))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=75)
            data = buf.getvalue()
            t = im.copy()
            t.thumbnail((220, 220))
            tb = io.BytesIO()
            t.save(tb, "JPEG", quality=60)
            thumb = base64.b64encode(tb.getvalue()).decode()
            name = os.path.splitext(name)[0] + ".jpg"
            mime = "image/jpeg"
        else:
            if os.path.getsize(path) > MAX_BYTES:
                raise RuntimeError("फ़ाइल 8 MB से बड़ी है, नहीं भेज सकते")
            with open(path, "rb") as f:
                data = f.read()
            if kind == "video":
                thumb = self._video_thumb(path)
                mime = "video/mp4"
        if len(data) > MAX_BYTES:
            raise RuntimeError("फ़ाइल 8 MB से बड़ी है, नहीं भेज सकते")

        fs = self._mk()
        blob = uuid.uuid4().hex[:20]
        b64 = base64.b64encode(data).decode()
        parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
        self.uploads[blob] = {"peer": peer, "name": name, "percent": 0}
        try:
            for i, p in enumerate(parts):
                fs.patch(f"blobs/{blob}_{i}", {"d": p})
                self.uploads[blob]["percent"] = (i + 1) * 100 // len(parts)
            self._create_doc(fs, "messages", {
                **self._base(peer, kind),
                "name": name,
                "mime": mime,
                "size": len(data),
                "chunks": len(parts),
                "blobId": blob,
                "thumb": thumb,
            })
            sounds.play("attach_sent")
            self.wake.set()
        finally:
            self.uploads.pop(blob, None)

    @staticmethod
    def _video_thumb(path):
        try:
            import av

            c = av.open(path)
            for frame in c.decode(video=0):
                im = frame.to_image().convert("RGB")
                im.thumbnail((220, 220))
                b = io.BytesIO()
                im.save(b, "JPEG", quality=60)
                c.close()
                return base64.b64encode(b.getvalue()).decode()
            c.close()
        except Exception:
            pass
        return ""

    # ---------- download ----------
    def thumb(self, m):
        mid = m["id"]
        if mid in self.thumbs:
            return self.thumbs[mid]
        if mid not in self._thumb_loading:
            self._thumb_loading.add(mid)
            threading.Thread(target=self._load_thumb, args=(mid,), daemon=True).start()
        return None

    def _load_thumb(self, mid):
        try:
            d = self._mk().get(f"messages/{mid}")
            self.thumbs[mid] = (d or {}).get("thumb", "") or ""
        except Exception:
            self.thumbs[mid] = ""
        self.version += 1

    def fetch_file(self, m):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", m["name"] or "file")
        f = CACHE_DIR / f"chat_{m['blobId']}_{safe}"
        if f.exists() and f.stat().st_size > 0:
            return f
        fs = self._mk()
        try:
            with open(f, "wb") as out:
                for i in range(m["chunks"]):
                    d = fs.get(f"blobs/{m['blobId']}_{i}")
                    if not d or "d" not in d:
                        raise RuntimeError(f"फ़ाइल का हिस्सा {i} नहीं मिला")
                    out.write(base64.b64decode(d["d"]))
        except Exception:
            try:
                f.unlink()
            except Exception:
                pass
            raise
        return fV
