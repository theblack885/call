import os
import sys
import traceback
from pathlib import Path

_BOOT_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "AICallGuard"
_BOOT_DIR.mkdir(parents=True, exist_ok=True)
_BOOT_LOG = _BOOT_DIR / "startup.log"


def _b(msg):
    try:
        with open(_BOOT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass


_b("--- EXE started ---")


def _ex_hook(tp, val, tb):
    _b("CRASH:\n" + "".join(traceback.format_exception(tp, val, tb)))
    try:
        sys.__excepthook__(tp, val, tb)
    except Exception:
        pass


sys.excepthook = _ex_hook

_b("importing stdlib...")
import base64
import io
import threading
import time
import zipfile
from tkinter import filedialog

_b("importing third-party...")
import customtkinter as ctk
import requests
from PIL import Image

_b("importing local modules...")
import sounds
import store
from chat import Chat, fmt_size, preview
from engine import Engine
_b("all imports done")

ACCENT = "#5CF2D6"
VIOLET = "#8B7CFF"
BG = "#07090F"
CARD = "#111827"
DANGER = "#FF4D6A"
GREEN = "#22C55E"
AMBER = "#FFC857"

ctk.set_appearance_mode("dark")


def font(size=14, bold=False):
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")


# ====================== कॉल की खिड़की ======================
class CallWindow(ctk.CTkToplevel):
    def __init__(self, app, engine):
        super().__init__(app)
        self.engine = engine
        self.mode = ""
        self.title("कॉल")
        self.geometry("380x720")
        self.configure(fg_color="#0B1020")
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        self.avatar = ctk.CTkLabel(self, text="?", width=100, height=100, corner_radius=50,
                                   fg_color=ACCENT, text_color=BG, font=font(44, True))
        self.avatar.pack(pady=(30, 10))
        self.name = ctk.CTkLabel(self, text="", font=font(24, True))
        self.name.pack()
        self.sub = ctk.CTkLabel(self, text="", text_color="#7C8499")
        self.sub.pack()
        self.status = ctk.CTkLabel(self, text="", text_color=ACCENT, font=font(15))
        self.status.pack(pady=(8, 2))
        self.notice = ctk.CTkLabel(self, text="", text_color=AMBER, wraplength=330, justify="center")
        self.notice.pack(pady=2)

        self.ring_f = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkButton(self.ring_f, text="✖  काटो", width=130, height=56, fg_color=DANGER, hover_color="#d63a56",
                      font=font(16, True), command=self._decline).pack(side="left", padx=14)
        ctk.CTkButton(self.ring_f, text="📞  उठाओ", width=130, height=56, fg_color=GREEN, hover_color="#17a34a",
                      font=font(16, True), command=engine.answer).pack(side="left", padx=14)

        self.act_f = ctk.CTkFrame(self, fg_color="transparent")
        row1 = ctk.CTkFrame(self.act_f, fg_color="transparent")
        row1.pack(pady=4)
        self.b_mute = ctk.CTkButton(row1, text="🎤 म्यूट", width=100,
                                    command=lambda: self._tap(engine.toggle_mute))
        self.b_mute.pack(side="left", padx=4)
        self.b_hold = ctk.CTkButton(row1, text="⏸ होल्ड", width=100,
                                    command=lambda: self._tap(engine.toggle_hold))
        self.b_hold.pack(side="left", padx=4)
        self.b_rec = ctk.CTkButton(row1, text="🔴 रिकॉर्ड", width=100,
                                   command=lambda: self._tap(engine.toggle_record))
        self.b_rec.pack(side="left", padx=4)
        row2 = ctk.CTkFrame(self.act_f, fg_color="transparent")
        row2.pack(pady=4)
        self.b_pad = ctk.CTkButton(row2, text="⌨ कीपैड", width=100, command=self._toggle_pad)
        self.b_pad.pack(side="left", padx=4)
        self.b_note = ctk.CTkButton(row2, text="📝 नोट", width=100, command=self._toggle_note)
        self.b_note.pack(side="left", padx=4)

        self.pad_f = ctk.CTkFrame(self.act_f, fg_color="transparent")
        for i, k in enumerate("123456789*0#"):
            ctk.CTkButton(self.pad_f, text=k, width=56, height=44, fg_color="#1F2937",
                          command=lambda c=k: self._digit(c)).grid(row=i // 3, column=i % 3, padx=3, pady=3)
        self.note_box = ctk.CTkTextbox(self.act_f, width=320, height=80)
        self.note_box.bind("<KeyRelease>", lambda e: setattr(engine.st, "note", self.note_box.get("1.0", "end")))
        self.pad_on = False
        self.note_on = False

        self.hang = ctk.CTkButton(self, text="📵  कॉल खत्म", height=52, width=200, fg_color=DANGER,
                                  hover_color="#d63a56", font=font(16, True), command=engine.hangup)
        self.hang.pack(side="bottom", pady=24)

    def _tap(self, fn):
        sounds.play("tap")
        fn()

    def _digit(self, c):
        sounds.dtmf(c)
        self.engine.send_digit(c)

    def _decline(self):
        if self.engine.st.phase == "incoming":
            self.engine.decline()
        else:
            self.engine.hangup()

    def _toggle_pad(self):
        sounds.play("tap")
        self.pad_on = not self.pad_on
        if self.pad_on:
            self.pad_f.pack(pady=6)
        else:
            self.pad_f.pack_forget()

    def _toggle_note(self):
        sounds.play("tap")
        self.note_on = not self.note_on
        if self.note_on:
            self.note_box.pack(pady=6)
        else:
            self.note_box.pack_forget()

    def update_view(self):
        st = self.engine.st
        label = st.peer_name or st.peer_id
        self.avatar.configure(text=(label[:1] or "?"))
        self.name.configure(text=label)
        self.sub.configure(text=st.peer_id if st.peer_name else "")

        secs = int(time.time() - st.started) if st.started else 0
        timer = f"{secs // 60:02d}:{secs % 60:02d}"
        ringing_owner = st.ai and st.owner_ringing
        if st.phase == "outgoing":
            text = "कॉल लगाई जा रही है…"
        elif st.phase == "incoming":
            text = "आपको कॉल आ रही है"
        elif st.phase == "connecting":
            text = "कनेक्ट हो रहा है…"
        elif ringing_owner:
            text = "कोड सही — लाइन पर है"
        elif st.phase == "active" and st.ai:
            text = f"AI असिस्टेंट बात कर रहा है • {timer}"
        else:
            text = timer
        self.status.configure(text=text)

        notes = []
        if st.info:
            notes.append(st.info)
        if st.remote_hold:
            notes.append("⏸ सामने वाले ने आपको होल्ड पर रखा है")
        if st.hold:
            notes.append("⏸ आपने कॉल होल्ड पर रखी है")
        if st.recording:
            notes.append("🔴 रिकॉर्डिंग चालू")
        if st.remote_recording:
            notes.append("🔴 सामने वाला कॉल रिकॉर्ड कर रहा है")
        self.notice.configure(text="\n".join(notes))

        if st.phase == "incoming" or ringing_owner:
            mode = "ring"
        elif st.phase == "active" and not st.ai:
            mode = "active"
        else:
            mode = "end"
        if mode != self.mode:
            self.mode = mode
            self.ring_f.pack_forget()
            self.act_f.pack_forget()
            self.hang.pack_forget()
            if mode == "ring":
                self.ring_f.pack(pady=40)
                self.lift()
                self.attributes("-topmost", True)
            elif mode == "active":
                self.act_f.pack(pady=10)
                self.hang.pack(side="bottom", pady=24)
            else:
                self.hang.pack(side="bottom", pady=24)

        if mode == "active":
            self.b_mute.configure(fg_color=ACCENT if st.muted else "#1F2937",
                                  text_color=BG if st.muted else "white")
            self.b_hold.configure(fg_color=AMBER if st.hold else "#1F2937",
                                  text_color=BG if st.hold else "white")
            self.b_rec.configure(fg_color=DANGER if st.recording else "#1F2937", text_color="white")


# ====================== चैट की खिड़की ======================
class ChatWindow(ctk.CTkToplevel):
    def __init__(self, app, peer):
        super().__init__(app)
        self.app = app
        self.peer = str(peer).strip()
        self.chat = app.chat
        self.me = str(app.cfg.get("my_id", "")).strip()
        self.imgs = {}
        self.sig = None
        self.status = ""
        self.status_until = 0.0

        self.title(f"चैट • {self.peer}")
        self.geometry("480x720")
        self.configure(fg_color="#0B1020")

        head = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0)
        head.pack(fill="x")
        self.avatar = ctk.CTkLabel(head, text=(self.peer[:1] or "?"), width=42, height=42, corner_radius=21,
                                   fg_color=ACCENT, text_color=BG, font=font(18, True))
        self.avatar.pack(side="left", padx=12, pady=10)
        col = ctk.CTkFrame(head, fg_color="transparent")
        col.pack(side="left")
        self.title_lbl = ctk.CTkLabel(col, text=self.peer, font=font(16, True))
        self.title_lbl.pack(anchor="w")
        ctk.CTkLabel(col, text=f"ID: {self.peer}", text_color="#7C8499", font=font(11)).pack(anchor="w")
        ctk.CTkButton(head, text="📞", width=44, height=36, fg_color=GREEN, hover_color="#17a34a",
                      command=self.call).pack(side="right", padx=12)

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=6, pady=6)

        self.up_lbl = ctk.CTkLabel(self, text="", text_color=ACCENT, font=font(12))
        self.up_lbl.pack(fill="x", padx=12)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=8)
        for txt, kind in (("🖼", "image"), ("🎥", "video"), ("📎", "file")):
            ctk.CTkButton(bar, text=txt, width=40, height=40, fg_color="#1F2937",
                          command=lambda k=kind: self.attach(k)).pack(side="left", padx=2)
        self.entry = ctk.CTkEntry(bar, placeholder_text="मैसेज लिखिए", height=40)
        self.entry.pack(side="left", fill="x", expand=True, padx=4)
        self.entry.bind("<Return>", self.send)
        ctk.CTkButton(bar, text="➤", width=44, height=40, fg_color=ACCENT, text_color=BG,
                      command=self.send).pack(side="left")

        self.chat.active_peer = self.peer
        try:
            self.chat.mark_read(self.peer)
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(150, self._safe_lift)
        self.after(300, self.refresh)

    def _safe_lift(self):
        try:
            if self.winfo_exists():
                self.lift()
                self.focus_force()
        except Exception:
            pass

    def close(self):
        if getattr(self.chat, "active_peer", "") == self.peer:
            self.chat.active_peer = ""
        self.app.chat_wins.pop(self.peer, None)
        try:
            self.destroy()
        except Exception:
            pass

    def call(self):
        sounds.play("tap")
        self.app.engine.dial(self.peer)

    def say(self, text, secs=5):
        self.status = text
        self.status_until = time.time() + secs

    def send(self, event=None):
        t = self.entry.get().strip()
        if t:
            self.chat.send_text(self.peer, t)
            self.entry.delete(0, "end")

    def attach(self, kind):
        types = {
            "image": [("फ़ोटो", "*.jpg *.jpeg *.png *.bmp *.gif *.webp"), ("सभी", "*.*")],
            "video": [("वीडियो", "*.mp4 *.mkv *.mov *.avi *.3gp *.webm"), ("सभी", "*.*")],
            "file": [("सभी फ़ाइलें", "*.*")],
        }[kind]
        p = filedialog.askopenfilename(parent=self, filetypes=types)
        if p:
            self.chat.send_path(self.peer, p, kind)

    def open_media(self, m):
        self.say("⬇ डाउनलोड हो रहा है…", 60)

        def run():
            try:
                f = self.chat.fetch_file(m)
                sounds.play("download_done")
                self.status = ""
                os.startfile(str(f))
            except Exception as e:
                sounds.play("error")
                self.say(f"⚠️ {e}", 8)

        threading.Thread(target=run, daemon=True).start()

    def thumb_image(self, m):
        if m["id"] in self.imgs:
            return self.imgs[m["id"]]
        b64 = self.chat.thumb(m)
        if not b64:
            return None
        try:
            im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
            img = ctk.CTkImage(light_image=im, dark_image=im, size=im.size)
            self.imgs[m["id"]] = img
            return img
        except Exception:
            return None

    def bubble(self, m):
        mine = m["from"] == self.me
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=3)
        box = ctk.CTkFrame(row, fg_color=ACCENT if mine else "#1F2937", corner_radius=16)
        box.pack(side="right" if mine else "left", padx=6)
        fg = BG if mine else "white"
        t = m["type"]
        if t in ("image", "video"):
            img = self.thumb_image(m)
            if img:
                lbl = ctk.CTkLabel(box, text="", image=img, cursor="hand2")
            else:
                loading = self.chat.thumbs.get(m["id"]) is None
                lbl = ctk.CTkLabel(box, text=("⏳ लोड हो रहा है…" if loading else
                                              ("🖼 फ़ोटो" if t == "image" else "🎥 वीडियो")),
                                   text_color=fg, cursor="hand2", width=160, height=70)
            lbl.pack(padx=8, pady=(8, 2))
            lbl.bind("<Button-1>", lambda e, mm=m: self.open_media(mm))
            cap = ("▶ " if t == "video" else "") + f"{m['name']} • {fmt_size(m['size'])}"
            ctk.CTkLabel(box, text=cap, text_color=fg, font=font(11), wraplength=260).pack(padx=8)
        elif t == "file":
            lbl = ctk.CTkLabel(box, text=f"📎 {m['name']}\n{fmt_size(m['size'])} • खोलने के लिए क्लिक करें",
                               text_color=fg, justify="left", wraplength=260, cursor="hand2")
            lbl.pack(padx=12, pady=8)
            lbl.bind("<Button-1>", lambda e, mm=m: self.open_media(mm))
        else:
            ctk.CTkLabel(box, text=m["text"], text_color=fg, wraplength=300, justify="left",
                         font=font(14)).pack(padx=12, pady=(8, 2))
        stamp = time.strftime("%H:%M", time.localtime(m["ts"])) + ("  ✓" if mine else "")
        ctk.CTkLabel(box, text=stamp, text_color=fg, font=font(10)).pack(anchor="e", padx=10, pady=(0, 6))

    def refresh(self):
        try:
            if not self.winfo_exists():
                return
            msgs = self.chat.conversation(self.peer)
            sig = (self.chat.version, len(msgs))
            if sig != self.sig:
                self.sig = sig
                for w in self.body.winfo_children():
                    w.destroy()
                for m in msgs:
                    self.bubble(m)
                self.after(80, lambda: self.body._parent_canvas.yview_moveto(1.0))
            name = self.chat.peer_name(self.peer)
            if name:
                self.title_lbl.configure(text=name)
                self.avatar.configure(text=name[:1])
            ups = self.chat.uploads_for(self.peer)
            if ups:
                txt = "   ".join(f"⬆ {u['name']} • {u['percent']}%" for u in ups)
            elif self.status and time.time() < self.status_until:
                txt = self.status
            elif self.chat.error:
                txt = "⚠️ " + self.chat.error[:90]
            else:
                txt = ""
            self.up_lbl.configure(text=txt)
            self.chat.mark_read(self.peer)
        except Exception as e:
            try:
                if self.winfo_exists():
                    self.up_lbl.configure(text=f"⚠️ {e}"[:90])
            except Exception:
                pass
        self.after(700, self.refresh)


# ====================== मुख्य ऐप ======================
class App(ctk.CTk):
    def __init__(self):
        _b("App.__init__ start")
        super().__init__()
        _b("super().__init__ done")
        self.cfg = store.load_cfg()
        _b("load_cfg done")
        sounds.enabled = bool(self.cfg.get("sounds", True))
        self.engine = Engine(self.cfg)
        _b("Engine created")
        self.engine.configure()
        _b("engine.configure done")
        self.chat = Chat(self.cfg)
        _b("Chat created")
        self.chat.configure()
        _b("chat.configure done")
        self.chat_wins = {}
        self.call_win = None
        self.model_msg = ""
        self._cache = {}
        self.msg_until = 0.0
        self._prev_phase = "idle"
        self._last_rb = 0.0
        self._chat_sig = None

        self.title("AI Call Guard")
        self.geometry("520x840")
        self.configure(fg_color=BG)

        ctk.CTkLabel(self, text="AI Call Guard", font=font(28, True)).pack(anchor="w", padx=20, pady=(16, 0))
        ctk.CTkLabel(self, text="आपका अपना कॉलिंग सिस्टम + AI रिसेप्शनिस्ट + चैट",
                     text_color="#8A93A8").pack(anchor="w", padx=20)

        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=22)
        card.pack(fill="x", padx=16, pady=12)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 0))
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left")
        ctk.CTkLabel(left, text="आपका ID", text_color="#8A93A8", font=font(12)).pack(anchor="w")
        ctk.CTkLabel(left, text=self.cfg["my_id"], text_color=ACCENT, font=font(28, True)).pack(anchor="w")
        ctk.CTkButton(top, text="कॉपी", width=70, fg_color="#1F2937", command=self.copy_id).pack(side="right")
        row = ctk.CTkFrame(card, fg_color="transparent")
