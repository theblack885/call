import base64
import io
import os
import threading
import time
import zipfile
from tkinter import filedialog

import customtkinter as ctk
import requests
from PIL import Image

import sounds
import store
from chat import Chat, fmt_size, preview
from engine import Engine

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
        super().__init__()
        self.cfg = store.load_cfg()
        sounds.enabled = bool(self.cfg.get("sounds", True))
        self.engine = Engine(self.cfg)
        self.engine.configure()
        self.chat = Chat(self.cfg)
        self.chat.configure()
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
        row.pack(fill="x", padx=14, pady=(6, 12))
        self.listen_lbl = ctk.CTkLabel(row, text="कॉल रिसीव करना: बंद", text_color="#8A93A8")
        self.listen_lbl.pack(side="left")
        self.listen_sw = ctk.CTkSwitch(row, text="", command=self.toggle_listen, progress_color=ACCENT)
        self.listen_sw.pack(side="right")

        self.status = ctk.CTkLabel(self, text="", text_color=AMBER)
        self.status.pack()

        tabs = ctk.CTkTabview(self, fg_color=CARD, segmented_button_selected_color=ACCENT,
                              segmented_button_selected_hover_color=ACCENT,
                              segmented_button_unselected_color="#1F2937")
        tabs.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        for t in ["डायल", "चैट", "AI", "हिस्ट्री", "नोट्स", "सेटअप"]:
            tabs.add(t)
        self.build_dial(tabs.tab("डायल"))
        self.build_chat(tabs.tab("चैट"))
        self.build_ai(tabs.tab("AI"))
        self.build_history(tabs.tab("हिस्ट्री"))
        self.build_notes(tabs.tab("नोट्स"))
        self.build_setup(tabs.tab("सेटअप"))

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(300, self.refresh)

    # ---------- tabs ----------
    def key(self, ch):
        sounds.dtmf(ch)
        self.num.insert("end", ch)

    def back(self):
        sounds.play("tap")
        self.num.delete(len(self.num.get()) - 1, "end")

    def build_dial(self, f):
        self.num = ctk.CTkEntry(f, justify="center", font=font(28, True), height=56,
                                placeholder_text="सामने वाले का ID")
        self.num.pack(fill="x", padx=14, pady=(14, 8))
        grid = ctk.CTkFrame(f, fg_color="transparent")
        grid.pack()
        for i, k in enumerate("123456789"):
            ctk.CTkButton(grid, text=k, width=84, height=56, font=font(22), fg_color="#1F2937",
                          command=lambda c=k: self.key(c)).grid(row=i // 3, column=i % 3, padx=5, pady=5)
        ctk.CTkButton(grid, text="⌫", width=84, height=56, font=font(22), fg_color="#1F2937",
                      command=self.back).grid(row=3, column=0, padx=5, pady=5)
        ctk.CTkButton(grid, text="0", width=84, height=56, font=font(22), fg_color="#1F2937",
                      command=lambda: self.key("0")).grid(row=3, column=1, padx=5, pady=5)
        ctk.CTkButton(grid, text="📞", width=84, height=56, font=font(22), fg_color=GREEN,
                      hover_color="#17a34a", command=self.do_call).grid(row=3, column=2, padx=5, pady=5)
        ctk.CTkLabel(f, text="8 अंक का ID (इंटरनेट कॉल). Android ऐप वाले भी इसी से जुड़ते हैं.",
                     text_color="#7C8499", wraplength=380).pack(pady=10)

    def build_chat(self, f):
        self.snd_sw = ctk.CTkSwitch(f, text="आवाज़ें (sounds)", progress_color=ACCENT, command=self.toggle_sounds)
        self.snd_sw.pack(anchor="w", padx=10, pady=(10, 4))
        if sounds.enabled:
            self.snd_sw.select()
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=6)
        self.new_id = ctk.CTkEntry(row, placeholder_text="नई चैट: सामने वाले का ID")
        self.new_id.pack(side="left", fill="x", expand=True)
        self.new_id.bind("<Return>", lambda e: self.open_new_chat())
        ctk.CTkButton(row, text="चैट", width=70, fg_color=ACCENT, text_color=BG,
                      command=self.open_new_chat).pack(side="left", padx=(6, 0))
        self.chat_note = ctk.CTkLabel(f, text="", text_color=AMBER, wraplength=400)
        self.chat_note.pack()
        self.chat_list = ctk.CTkScrollableFrame(f, fg_color="transparent")
        self.chat_list.pack(fill="both", expand=True, padx=4, pady=4)

    def build_ai(self, f):
        sc = ctk.CTkScrollableFrame(f, fg_color="transparent")
        sc.pack(fill="both", expand=True)
        self.ai_sw = ctk.CTkSwitch(sc, text="AI असिस्टेंट चालू (आने वाली कॉल AI उठाए)", progress_color=ACCENT,
                                   command=self.save_ai)
        self.ai_sw.pack(anchor="w", pady=6)
        if self.cfg["ai_mode"]:
            self.ai_sw.select()
        self.e_name = self.entry(sc, "आपका नाम (AI यही बोलेगा, चैट में भी दिखेगा)", self.cfg["name"])
        self.e_code = self.entry(sc, "सीक्रेट कोड (कम से कम 4 अंक)", self.cfg["code"], show="*")
        self.e_trusted = self.entry(sc, "भरोसेमंद IDs (कॉमा से अलग, इनकी कॉल सीधी आएगी)", self.cfg["trusted"])
        ctk.CTkButton(sc, text="सेव करो", fg_color=ACCENT, text_color=BG, command=self.save_ai).pack(pady=8, fill="x")

        ctk.CTkLabel(sc, text="आवाज़ पहचान मॉडल (हिंदी, ~42 MB)", font=font(14, True)).pack(anchor="w", pady=(10, 0))
        self.model_lbl = ctk.CTkLabel(sc, text="", text_color="#8A93A8", wraplength=380, justify="left")
        self.model_lbl.pack(anchor="w")
        ctk.CTkButton(sc, text="मॉडल डाउनलोड करो", fg_color=VIOLET, command=self.download_model).pack(pady=6, fill="x")

        ctk.CTkLabel(sc, text="लाइव AI लॉग", font=font(14, True)).pack(anchor="w", pady=(10, 0))
        self.log_box = ctk.CTkTextbox(sc, height=200)
        self.log_box.pack(fill="x")
        self.log_box.configure(state="disabled")

    def build_history(self, f):
        self.hist_box = ctk.CTkTextbox(f, height=420)
        self.hist_box.pack(fill="both", expand=True, padx=8, pady=8)
        self.hist_box.configure(state="disabled")
        ctk.CTkButton(f, text="रिकॉर्डिंग्स फ़ोल्डर खोलो", fg_color="#1F2937", command=self.open_rec).pack(pady=6)

    def build_notes(self, f):
        self.note_new = ctk.CTkTextbox(f, height=90)
        self.note_new.pack(fill="x", padx=8, pady=8)
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack()
        ctk.CTkButton(row, text="नोट सेव करो", fg_color=ACCENT, text_color=BG, command=self.save_note).pack(side="left", padx=4)
        ctk.CTkButton(row, text="आखिरी नोट हटाओ", fg_color="#1F2937", command=self.del_note).pack(side="left", padx=4)
        self.notes_box = ctk.CTkTextbox(f, height=300)
        self.notes_box.pack(fill="both", expand=True, padx=8, pady=8)
        self.notes_box.configure(state="disabled")

    def build_setup(self, f):
        ctk.CTkLabel(f, text="Firebase सेटअप (Android ऐप वाला ही प्रोजेक्ट)", font=font(14, True)).pack(anchor="w", padx=10, pady=(12, 0))
        ctk.CTkLabel(f, text="google-services.json खोलो → \"project_id\" की value यहाँ डालो.\nAPI key खाली छोड़ सकते हो (पहले खाली ही आज़माओ).",
                     text_color="#8A93A8", wraplength=430, justify="left").pack(anchor="w", padx=10)
        self.e_proj = self.entry(f, "Firebase Project ID", self.cfg["project_id"])
        self.e_key = self.entry(f, "API key (optional)", self.cfg["api_key"])
        ctk.CTkButton(f, text="सेव करो", fg_color=ACCENT, text_color=BG, command=self.save_setup).pack(pady=10, padx=10, fill="x")

    def entry(self, parent, label, value, show=None):
        ctk.CTkLabel(parent, text=label, text_color="#8A93A8").pack(anchor="w", padx=10, pady=(8, 0))
        e = ctk.CTkEntry(parent, show=show) if show else ctk.CTkEntry(parent)
        e.pack(fill="x", padx=10)
        e.insert(0, value)
        return e

    # ---------- actions ----------
    def copy_id(self):
        sounds.play("tap")
        self.clipboard_clear()
        self.clipboard_append(self.cfg["my_id"])
        self.flash("ID कॉपी हो गया")

    def flash(self, msg):
        try:
            self.status.configure(text=msg)
        except Exception:
            pass
        self.msg_until = time.time() + 5

    def toggle_listen(self):
        on = bool(self.listen_sw.get())
        self.engine.set_listening(on)
        if on and not self.engine.st.listening:
            self.listen_sw.deselect()

    def toggle_sounds(self):
        on = bool(self.snd_sw.get())
        sounds.enabled = on
        self.cfg["sounds"] = on
        store.save_cfg(self.cfg)
        if on:
            sounds.play("receive")

    def do_call(self):
        n = self.num.get().strip()
        if len(n) < 4:
            self.flash("सही ID डालिए")
            return
        sounds.play("tap")
        self.engine.dial(n)

    def open_new_chat(self):
        p = "".join(c for c in self.new_id.get() if c.isdigit())
        if len(p) < 4:
            self.flash("सही ID डालिए")
            return
        if p == str(self.cfg.get("my_id", "")).strip():
            self.flash("अपने आप से चैट नहीं कर सकते")
            return
        self.new_id.delete(0, "end")
        self.open_chat(p)

    def open_chat(self, peer):
        p = str(peer or "").strip()
        if not p:
            self.flash("सही ID डालिए")
            return
        try:
            sounds.play("tap")
        except Exception:
            pass
        try:
            self.chat.add_peer(p)
        except Exception as e:
            self.flash(f"⚠️ {e}")
        w = self.chat_wins.get(p)
        if w is not None:
            try:
                if w.winfo_exists():
                    w.deiconify()
                    w.lift()
                    w.focus_force()
                    return
            except Exception:
                self.chat_wins.pop(p, None)
        try:
            win = ChatWindow(self, p)
            self.chat_wins[p] = win
            self._chat_sig = None
        except Exception as e:
            self.flash(f"⚠️ चैट नहीं खुली: {e}")

    def save_ai(self):
        c = self.cfg
        c["ai_mode"] = bool(self.ai_sw.get())
        c["name"] = self.e_name.get().strip()
        code = "".join(ch for ch in self.e_code.get() if ch.isdigit())
        if len(code) >= 4:
            c["code"] = code
        else:
            self.flash("कोड कम से कम 4 अंक का हो")
        c["trusted"] = self.e_trusted.get().strip()
        store.save_cfg(c)
        self.flash("सेव हो गया")

    def save_setup(self):
        self.cfg["project_id"] = self.e_proj.get().strip()
        self.cfg["api_key"] = self.e_key.get().strip()
        store.save_cfg(self.cfg)
        self.engine.configure()
        self.chat.configure()
        self.flash("सेटअप सेव हुआ")

    def save_note(self):
        t = self.note_new.get("1.0", "end").strip()
        if t:
            store.add_note(f"{store.now_str()}\n{t}")
            self.note_new.delete("1.0", "end")

    def del_note(self):
        store.delete_first_note()

    def open_rec(self):
        store.REC_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(store.REC_DIR))
        except Exception:
            pass

    def download_model(self):
        if store.model_ready():
            self.model_msg = "✅ पहले से तैयार है"
            return

        def run():
            try:
                zp = store.APP_DIR / "model.zip"
                with requests.get(store.MODEL_URL, stream=True, timeout=30) as r:
                    total = int(r.headers.get("content-length", 0))
                    read = 0
                    with open(zp, "wb") as f:
                        for chunk in r.iter_content(1 << 15):
                            f.write(chunk)
                            read += len(chunk)
                            if total:
                                self.model_msg = f"डाउनलोड हो रहा है… {read * 90 // total}%"
                self.model_msg = "खोला जा रहा है…"
                with zipfile.ZipFile(zp) as z:
                    z.extractall(store.APP_DIR)
                zp.unlink()
                self.model_msg = "✅ तैयार है — caller बोलकर भी कोड बता सकेगा"
            except Exception as e:
                self.model_msg = f"⚠️ {e}"

        threading.Thread(target=run, daemon=True).start()

    def set_box(self, box, key, text):
        if self._cache.get(key) != text:
            self._cache[key] = text
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text)
            box.configure(state="disabled")

    def show_toast(self, ev):
        t = ctk.CTkToplevel(self)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(fg_color="#111827")
        w, h = 330, 84
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        t.geometry(f"{w}x{h}+{sw - w - 20}+{sh - h - 80}")
        ctk.CTkLabel(t, text=f"💬 {ev['title']}", font=font(14, True), text_color=ACCENT).pack(anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(t, text=ev["text"][:120], wraplength=300, justify="left").pack(anchor="w", padx=12)

        def go(e=None):
            try:
                t.destroy()
            except Exception:
                pass
            self.open_chat(ev["peer"])

        t.bind("<Button-1>", go)

        def bye():
            try:
                t.destroy()
            except Exception:
                pass

        t.after(5500, bye)

    # ---------- refresh loop ----------
    def refresh(self):
        try:
            if not self.winfo_exists():
                return
            st = self.engine.st
            self.listen_lbl.configure(
                text="कॉल रिसीव करना: चालू" if st.listening else "कॉल रिसीव करना: बंद",
                text_color=ACCENT if st.listening else "#8A93A8")

            # कॉल की आवाज़ें
            if st.phase != self._prev_phase:
                if st.phase == "active":
                    sounds.play("call_connect")
                elif st.phase == "idle" and self._prev_phase != "idle":
                    sounds.play("call_end")
                self._prev_phase = st.phase
            if st.phase == "outgoing" and time.time() - self._last_rb > 4:
                self._last_rb = time.time()
                sounds.play("ringback")

            if st.phase != "idle":
                if self.call_win is None or not self.call_win.winfo_exists():
                    self.call_win = CallWindow(self, self.engine)
                self.call_win.update_view()
            else:
                if self.call_win is not None and self.call_win.winfo_exists():
                    self.call_win.destroy()
                    self.call_win = None
                if st.last_msg:
                    self.flash(st.last_msg)
                    st.last_msg = ""
            if time.time() > self.msg_until:
                self.status.configure(text="")

            # चैट: नए मैसेज का पॉपअप और सूची
            for ev in self.chat.pop_events():
                self.show_toast(ev)
            peers = self.chat.peers()
            sig = (self.chat.version, self.chat.read_ver, tuple(peers))
            if sig != self._chat_sig:
                self._chat_sig = sig
                for w in self.chat_list.winfo_children():
                    w.destroy()
                if not peers:
                    ctk.CTkLabel(self.chat_list, text="अभी कोई चैट नहीं। ऊपर ID डालकर शुरू कीजिए.",
                                 text_color="#7C8499").pack(pady=20)
                for p in peers:
                    conv = self.chat.conversation(p)
                    last = conv[-1] if conv else None
                    name = self.chat.peer_name(p) or p
                    un = self.chat.unread(p)
                    line2 = (("आप: " if last["from"] == self.cfg["my_id"] else "") + preview(last)) if last else f"ID: {p}"
                    ctk.CTkButton(
                        self.chat_list, anchor="w", height=58, fg_color="#1F2937", hover_color="#2A3547",
                        text=f"{name}" + (f"   🔵 {un}" if un else "") + f"\n{line2[:48]}",
                        command=lambda pp=p: self.open_chat(pp)).pack(fill="x", pady=3)
            self.chat_note.configure(text=("⚠️ " + self.chat.error[:110]) if self.chat.error else "")

            self.model_lbl.configure(
                text=self.model_msg or ("✅ तैयार है — caller बोलकर भी कोड बता सकेगा" if store.model_ready()
                                        else "डाउनलोड नहीं है — बिना इसके caller को कीपैड से कोड डालना पड़ेगा"))
            self.set_box(self.log_box, "log", "\n\n".join(st.log[:25]) or "अभी तक कोई AI कॉल नहीं हुई।")
            data = store.load_data()
            self.set_box(self.hist_box, "hist", "\n\n".join(data["history"]) or "अभी कोई कॉल नहीं।")
            self.set_box(self.notes_box, "notes", "\n\n— — —\n\n".join(data["notes"]) or "अभी कोई नोट नहीं।")
        except Exception as e:
            self.flash(f"⚠️ {e}")
        self.after(300, self.refresh)

    def on_close(self):
        try:
            self.engine.hangup()
        except Exception:
            pass
        try:
            self.chat.running = False
        except Exception:
            pass
        self.destroy()
