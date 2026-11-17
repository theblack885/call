import asyncio
import datetime
import threading
import time

import numpy as np
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

import store
from agent import Agent
from audio_io import Mic, Recorder, Speaker, mulaw_decode, mulaw_encode
from fs import Firestore


class State:
    def __init__(self):
        self.phase = "idle"  # idle, outgoing, incoming, connecting, active
        self.peer_id = ""
        self.peer_name = ""
        self.info = ""
        self.muted = False
        self.hold = False
        self.remote_hold = False
        self.recording = False
        self.remote_recording = False
        self.ai = False
        self.owner_ringing = False
        self.ringing = False
        self.started = 0.0
        self.note = ""
        self.listening = False
        self.last_msg = ""
        self.log = []


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.st = State()
        self.fs = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()

        self.pc = None
        self.audio_ch = None
        self.ctl_ch = None
        self.call_id = None
        self.is_caller = False
        self.offer_sdp = None
        self.answer_applied = False
        self.remote_set = False
        self.connected = False
        self.was_ai = False
        self.recorded = False
        self.agent = None
        self.mic = None
        self.speaker = None
        self.recorder = None
        self.ringback_on = False
        self.tasks = []
        self.seen = set()
        self._listen_task = None
        self._finishing = False

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    # ---------------- helpers ----------------
    def configure(self):
        c = self.cfg
        self.fs = Firestore(c["project_id"].strip(), c["api_key"].strip()) if c["project_id"].strip() else None

    def log(self, s):
        self.st.log.insert(0, s)
        del self.st.log[60:]

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def _x(self, fn, *a):
        return await self.loop.run_in_executor(None, lambda: fn(*a))

    def _spawn(self, coro):
        t = asyncio.ensure_future(coro)
        self.tasks.append(t)
        return t

    def _cancel_tasks(self):
        cur = None
        try:
            cur = asyncio.current_task()
        except RuntimeError:
            pass
        for t in self.tasks:
            if t is not cur and not t.done():
                t.cancel()
        self.tasks = [t for t in self.tasks if t is cur]

    def label(self):
        s = self.st
        return f"{s.peer_name} ({s.peer_id})" if s.peer_name else s.peer_id

    def _reset(self):
        self._cancel_tasks()
        s = self.st
        self.pc = None
        self.audio_ch = None
        self.ctl_ch = None
        self.call_id = None
        self.offer_sdp = None
        self.answer_applied = False
        self.remote_set = False
        self.connected = False
        self.was_ai = False
        self.recorded = False
        self.ringback_on = False
        s.peer_id = ""
        s.peer_name = ""
        s.info = ""
        s.muted = False
        s.hold = False
        s.remote_hold = False
        s.recording = False
        s.remote_recording = False
        s.ai = False
        s.owner_ringing = False
        s.ringing = False
        s.started = 0.0
        s.note = ""

    # ---------------- public (UI thread) ----------------
    def set_listening(self, on):
        self.st.listening = on
        if on:
            if not self.fs:
                self.log("⚠️ पहले 'सेटअप' में Firebase Project ID डालिए")
                self.st.listening = False
                return
            self._submit(self._start_listen())

    def dial(self, to):
        self._submit(self._dial(to))

    def answer(self):
        self._submit(self._answer_ui())

    def decline(self):
        self._submit(self._decline())

    def hangup(self):
        self._submit(self._hangup())

    def toggle_mute(self):
        self.st.muted = not self.st.muted

    def toggle_hold(self):
        if self.st.phase == "active" and not self.st.ai:
            self.st.hold = not self.st.hold
            self.loop.call_soon_threadsafe(self._send_ctl, "HOLD:1" if self.st.hold else "HOLD:0")

    def toggle_record(self):
        if self.st.phase != "active" or self.st.ai:
            return
        if self.recorder is None:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            path = store.REC_DIR / f"call_{stamp}_{self.st.peer_id}.wav"
            try:
                r = Recorder(path)
                r.start()
                self.recorder = r
                self.recorded = True
                self.st.recording = True
                self.loop.call_soon_threadsafe(self._send_ctl, "REC:1")
            except Exception as e:
                self.st.info = f"⚠️ रिकॉर्डिंग शुरू नहीं हुई: {e}"
        else:
            self._stop_recording()

    def send_digit(self, ch):
        self.loop.call_soon_threadsafe(self._send_ctl, f"D:{ch}")

    # ---------------- listening ----------------
    async def _start_listen(self):
        if self._listen_task is None or self._listen_task.done():
            self._listen_task = asyncio.ensure_future(self._listen_loop())

    async def _listen_loop(self):
        err_logged = False
        while self.st.listening:
            try:
                if self.st.phase == "idle" and self.fs:
                    docs = await self._x(self.fs.incoming, self.cfg["my_id"])
                    for d in docs:
                        cid = d["_id"]
                        if cid in self.seen:
                            continue
                        self.seen.add(cid)
                        await self._on_incoming(cid, d)
                        break
                err_logged = False
            except Exception as e:
                if not err_logged:
                    self.log(f"⚠️ Firebase: {e}")
                    err_logged = True
            await asyncio.sleep(1.5)

    async def _on_incoming(self, cid, d):
        s = self.st
        if s.phase != "idle":
            try:
                await self._x(self.fs.patch, f"calls/{cid}", {"status": "busy"})
            except Exception:
                pass
            return
        created = d.get("createdAt")
        if created:
            try:
                t = datetime.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() > 60:
                    return
            except Exception:
                pass
        offer = (d.get("offer") or {}).get("sdp")
        if not offer:
            return
        self._reset()
        self.is_caller = False
        self.call_id = cid
        self.offer_sdp = offer
        s.peer_id = d.get("from", "?")
        s.peer_name = d.get("fromName", "")
        s.phase = "incoming"
        self._spawn(self._signal_loop(cid))
        self._spawn(self._timeout(45, "कॉल छूट गई"))
        trusted = [x.strip() for x in self.cfg["trusted"].split(",") if x.strip()]
        if s.peer_id not in trusted and self.cfg["ai_mode"]:
            await self._accept(True)
        else:
            s.ringing = True
            self._spawn(self._ring_local())

    async def _timeout(self, sec, msg):
        await asyncio.sleep(sec)
        if self.st.phase in ("outgoing", "incoming", "connecting"):
            await self._hangup(msg)

    async def _ring_local(self):
        while self.st.ringing and self.st.phase in ("incoming", "active"):
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
            await asyncio.sleep(1.2)

    # ---------------- peer connection ----------------
    def _new_pc(self):
        cfg = RTCConfiguration(iceServers=[
            RTCIceServer("stun:stun.l.google.com:19302"),
            RTCIceServer("stun:stun1.l.google.com:19302"),
        ])
        pc = RTCPeerConnection(cfg)
        self.pc = pc

        @pc.on("datachannel")
        def on_dc(ch):
            self._register(ch)

        @pc.on("connectionstatechange")
        async def on_state():
            if pc.connectionState in ("failed", "closed") and self.pc is pc and self.st.phase != "idle":
                await self._finish("कनेक्शन टूट गया")

        return pc

    def _register(self, ch):
        if ch.label == "audio":
            self.audio_ch = ch
        else:
            self.ctl_ch = ch

        @ch.on("message")
        def on_message(m):
            if ch.label == "audio":
                if isinstance(m, (bytes, bytearray)):
                    self._on_audio(bytes(m))
            else:
                text = m if isinstance(m, str) else bytes(m).decode("utf-8", "ignore")
                self._on_ctl(text)

        @ch.on("open")
        def on_open():
            self._check_open()

        self._check_open()

    def _check_open(self):
        a, c = self.audio_ch, self.ctl_ch
        if self.connected or a is None or c is None:
            return
        if a.readyState == "open" and c.readyState == "open":
            self.connected = True
            asyncio.ensure_future(self._on_connected())

    async def _dial(self, to):
        s = self.st
        if s.phase != "idle":
            self.log("⚠️ पहले से एक कॉल चल रही है")
            return
        if not self.fs:
            self.log("⚠️ पहले 'सेटअप' में Firebase Project ID डालिए")
            return
        if to == self.cfg["my_id"]:
            self.log("⚠️ आप अपने आप को कॉल नहीं कर सकते")
            return
        self._reset()
        self.is_caller = True
        s.phase = "outgoing"
        s.peer_id = to
        try:
            pc = self._new_pc()
            a = pc.createDataChannel("audio", ordered=False, maxRetransmits=0)
            c = pc.createDataChannel("ctl")
            self._register(a)
            self._register(c)
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            fields = {
                "from": self.cfg["my_id"],
                "fromName": self.cfg["name"],
                "to": to,
                "status": "ringing",
                "offer": {"type": "offer", "sdp": pc.localDescription.sdp},
                "createdAt": datetime.datetime.now(datetime.timezone.utc),
            }
            self.call_id = await self._x(self.fs.create, "calls", fields)
            self._spawn(self._signal_loop(self.call_id))
            self._spawn(self._timeout(50, "कोई जवाब नहीं"))
        except Exception as e:
            await self._finish(f"त्रुटि: {e}")

    async def _accept(self, as_ai):
        s = self.st
        if s.phase != "incoming":
            return
        s.ringing = False
        s.phase = "connecting"
        s.ai = as_ai
        self.was_ai = as_ai
        self._spawn(self._timeout(25, "कनेक्शन नहीं बन पाया"))
        try:
            pc = self._new_pc()
            await pc.setRemoteDescription(RTCSessionDescription(sdp=self.offer_sdp, type="offer"))
            self.remote_set = True
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            await self._x(
                self.fs.patch, f"calls/{self.call_id}",
                {"answer": {"type": "answer", "sdp": pc.localDescription.sdp}, "status": "active"},
            )
        except Exception as e:
            await self._finish(f"त्रुटि: {e}")

    async def _answer_ui(self):
        if self.st.phase == "incoming":
            await self._accept(False)
        elif self.st.owner_ringing:
            await self._take_over()

    async def _decline(self):
        if self.st.phase != "incoming":
            return
        try:
            await self._x(self.fs.patch, f"calls/{self.call_id}", {"status": "declined"})
        except Exception:
            pass
        await self._finish("")

    async def _signal_loop(self, call_id):
        seen_c = set()
        while self.st.phase != "idle" and self.call_id == call_id:
            try:
                d = await self._x(self.fs.get, f"calls/{call_id}")
                if d:
                    status = d.get("status")
                    if status in ("ended", "busy", "declined"):
                        msg = {"busy": "नंबर व्यस्त है", "declined": "कॉल अस्वीकार हुई"}.get(status, "कॉल खत्म हुई")
                        await self._finish(msg)
                        return
                    if self.is_caller and not self.answer_applied:
                        ans = d.get("answer")
                        if ans and ans.get("sdp") and self.pc is not None:
                            self.answer_applied = True
                            await self.pc.setRemoteDescription(
                                RTCSessionDescription(sdp=ans["sdp"], type="answer")
                            )
                            self.remote_set = True
                            if self.st.phase == "outgoing":
                                self.st.phase = "connecting"
                if self.remote_set and self.pc is not None:
                    col = "calleeCandidates" if self.is_caller else "callerCandidates"
                    for c in await self._x(self.fs.list, f"calls/{call_id}/{col}"):
                        if c["_id"] in seen_c:
                            continue
                        seen_c.add(c["_id"])
                        await self._add_candidate(c)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log(f"⚠️ {e}")
            await asyncio.sleep(1.0 if self.st.phase != "active" else 3.0)

    async def _add_candidate(self, c):
        try:
            cs = c.get("candidate", "")
            if cs.startswith("candidate:"):
                cs = cs[len("candidate:"):]
            cand = candidate_from_sdp(cs)
            cand.sdpMid = c.get("sdpMid") or "0"
            cand.sdpMLineIndex = int(c.get("sdpMLineIndex") or 0)
            await self.pc.addIceCandidate(cand)
        except Exception:
            pass

    # ---------------- connected ----------------
    async def _on_connected(self):
        s = self.st
        if s.phase == "idle":
            return
        s.phase = "active"
        s.started = time.time()
        s.info = ""
        if not self.is_caller and s.ai:
            self._start_agent()
        else:
            self._start_audio()

    def _start_agent(self):
        self.log(f"📞 {self.label()} की कॉल — AI ने उठाई")
        name = self.cfg["name"].strip() or "मालिक"
        self.agent = Agent(
            owner=name,
            code=self.cfg["code"],
            voice=self.cfg.get("voice", "hi-IN-SwaraNeural"),
            send_frame=self._send_frame,
            on_unlock=self._owner_ring,
            on_giveup=lambda: self._hangup("AI ने कॉल खत्म की"),
            log=self.log,
            model_dir=store.MODEL_DIR if store.model_ready() else None,
        )
        self._spawn(self.agent.start())

    def _start_audio(self):
        try:
            self.speaker = Speaker()
            self.speaker.start()
            self.mic = Mic(self._mic_frame)
            self.mic.start()
        except Exception as e:
            self.st.info = f"⚠️ माइक/स्पीकर शुरू नहीं हुआ: {e}"

    def _mic_frame(self, frame):
        if self.st.muted or self.st.hold:
            return
        self.loop.call_soon_threadsafe(self._send_frame, frame)
        r = self.recorder
        if r is not None:
            r.add_local(frame)

    def _send_frame(self, frame):
        ch = self.audio_ch
        if ch is None or ch.readyState != "open":
            return
        try:
            if ch.bufferedAmount > 64000:
                return
            ch.send(mulaw_encode(frame))
        except Exception:
            pass

    def _send_ctl(self, s):
        ch = self.ctl_ch
        if ch is None or ch.readyState != "open":
            return
        try:
            ch.send(s)
        except Exception:
            pass

    def _on_audio(self, b):
        pcm = mulaw_decode(b)
        if self.agent is not None and self.st.ai:
            self.agent.feed(pcm)
            return
        if self.st.hold:
            return
        r = self.recorder
        if r is not None:
            r.add_remote(pcm)
        if self.speaker is not None:
            self.speaker.push(pcm)

    def _on_ctl(self, msg):
        s = self.st
        if msg.startswith("D:") and len(msg) > 2:
            if self.agent is not None:
                self.agent.on_digit(msg[2])
        elif msg == "BYE":
            asyncio.ensure_future(self._finish("कॉल खत्म हुई"))
        elif msg == "STATE:CONNECTING":
            s.info = "आपको कनेक्ट किया जा रहा है…"
        elif msg == "STATE:HUMAN":
            s.info = ""
        elif msg == "REC:1":
            s.remote_recording = True
        elif msg == "REC:0":
            s.remote_recording = False
        elif msg == "HOLD:1":
            s.remote_hold = True
        elif msg == "HOLD:0":
            s.remote_hold = False

    # ---------------- AI -> owner ----------------
    async def _owner_ring(self):
        s = self.st
        if s.phase != "active":
            return
        s.owner_ringing = True
        s.ringing = True
        self._send_ctl("STATE:CONNECTING")
        self._spawn(self._ringback())
        self._spawn(self._ring_local())
        self.log("🔔 आपको ring किया जा रहा है")

    async def _ringback(self):
        self.ringback_on = True
        n = 0
        loop = asyncio.get_running_loop()
        while self.ringback_on and self.st.phase == "active":
            t0 = loop.time()
            for k in range(50):
                if not self.ringback_on:
                    break
                idx = np.arange(320) + n
                n += 320
                frame = (np.sin(2 * np.pi * 425 / 16000 * idx) * 5000).astype(np.int16)
                self._send_frame(frame)
                await asyncio.sleep(max(0.0, t0 + (k + 1) * 0.02 - loop.time()))
            await asyncio.sleep(3)

    async def _take_over(self):
        s = self.st
        if not s.owner_ringing:
            return
        self.ringback_on = False
        s.ringing = False
        s.owner_ringing = False
        if self.agent is not None:
            self.agent.stop()
            self.agent = None
        s.ai = False
        self._start_audio()
        self._send_ctl("STATE:HUMAN")
        self.log("🗣️ आपने कॉल उठाई")

    # ---------------- end ----------------
    def _stop_recording(self):
        r = self.recorder
        self.recorder = None
        if r is not None:
            try:
                r.stop()
            except Exception:
                pass
        self.st.recording = False
        self.loop.call_soon_threadsafe(self._send_ctl, "REC:0")

    async def _hangup(self, msg=""):
        if self.st.phase == "idle":
            return
        self._send_ctl("BYE")
        await asyncio.sleep(0.15)
        await self._finish(msg)

    async def _finish(self, msg):
        s = self.st
        if s.phase == "idle" or self._finishing:
            return
        self._finishing = True
        try:
            dur = int(time.time() - s.started) if s.started else 0
            arrow = "↗" if self.is_caller else "↙"
            tag = " • AI" if (not self.is_caller and self.was_ai) else ""
            rec = " • 🔴" if self.recorded else ""
            when = store.now_str()
            store.add_history(f"{arrow} {self.label()} • {when} • {dur}s{tag}{rec}")
            note = s.note.strip()
            if note:
                store.add_note(f"{self.label()} • {when}\n{note}")
            if self.fs and self.call_id:
                try:
                    await self._x(self.fs.patch, f"calls/{self.call_id}", {"status": "ended"})
                except Exception:
                    pass
            self.ringback_on = False
            s.ringing = False
            self._cancel_tasks()
            if self.agent is not None:
                self.agent.stop()
                self.agent = None
            self._stop_recording()
            for dev in (self.mic, self.speaker):
                try:
                    if dev is not None:
                        dev.stop()
                except Exception:
                    pass
            self.mic = None
            self.speaker = None
            pc = self.pc
            self.pc = None
            self.audio_ch = None
            self.ctl_ch = None
            if pc is not None:
                try:
                    await pc.close()
                except Exception:
                    pass
            s.note = ""
            s.info = msg
            s.last_msg = msg
            s.phase = "idle"
        finally:
            self._finishing = False
