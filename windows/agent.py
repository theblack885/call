import asyncio
import io
import json
import re
import time

import numpy as np

WORD_DIGITS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "thirty": "30", "fifty": "50",
    "शून्य": "0", "जीरो": "0", "ज़ीरो": "0", "एक": "1", "वन": "1", "दो": "2", "टू": "2",
    "तीन": "3", "थ्री": "3", "चार": "4", "फोर": "4", "पांच": "5", "पाँच": "5", "फाइव": "5",
    "छह": "6", "छः": "6", "सिक्स": "6", "सात": "7", "सेवन": "7", "आठ": "8", "एट": "8",
    "नौ": "9", "नाइन": "9", "तीस": "30", "पचास": "50", "थर्टी": "30", "फिफ्टी": "50",
}

REPLIES = [
    "ठीक है, मैंने आपकी बात नोट कर ली है। सीधे बात करने के लिए कृपया सीक्रेट कोड बोलिए या कीपैड पर डालिए।",
    "मैं समझ गया। लेकिन सीधे बात तभी होगी जब आप सही कोड बताएँगे।",
    "क्षमा कीजिए, कोड सही नहीं है। कृपया कोड साफ़ आवाज़ में दोबारा बोलिए।",
    "मैं AI असिस्टेंट हूँ और बिना कोड के कॉल आगे नहीं भेज सकता। कृपया कोड बताइए।",
]


def decode_mp3(data: bytes) -> np.ndarray:
    import av

    container = av.open(io.BytesIO(data))
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    out = []
    for frame in container.decode(audio=0):
        for f in resampler.resample(frame):
            out.append(f.to_ndarray().reshape(-1))
    try:
        for f in resampler.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    except Exception:
        pass
    container.close()
    if not out:
        return np.zeros(3200, np.int16)
    return np.concatenate(out).astype(np.int16)


async def tts_pcm(text: str, voice: str) -> np.ndarray:
    import edge_tts

    data = b""
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            data += chunk["data"]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, decode_mp3, data)


class Agent:
    """Call utha kar caller se baat karne wala AI (awaaz: Vosk se sunna, edge-tts se bolna)."""

    def __init__(self, owner, code, voice, send_frame, on_unlock, on_giveup, log,
                 model_dir=None, max_rounds=12):
        self.owner = owner
        self.code = code
        self.voice = voice
        self.send_frame = send_frame
        self.on_unlock = on_unlock
        self.on_giveup = on_giveup
        self.log = log
        self.model_dir = model_dir
        self.max_rounds = max_rounds

        self.active = False
        self.listening = False
        self.speaking = True
        self.unlocked = False
        self.ignore_until = 0.0
        self.rounds = 0
        self.silent = 0
        self.digits = ""
        self.model = None
        self.rec = None
        self._silence_task = None

    def _load(self):
        from vosk import Model, Recognizer, SetLogLevel

        SetLogLevel(-1)
        self.model = Model(str(self.model_dir))
        return Recognizer(self.model, 16000.0)

    async def start(self):
        self.active = True
        loop = asyncio.get_running_loop()
        if self.model_dir:
            try:
                self.rec = await loop.run_in_executor(None, self._load)
                self.log("🎙️ आवाज़ पहचान तैयार")
            except Exception as e:
                self.log(f"⚠️ voice model load नहीं हुआ: {e}")
        else:
            self.log("ℹ️ Voice model डाउनलोड नहीं है — caller को कीपैड से कोड डालना होगा")
        await self.say(
            f"नमस्ते! मैं {self.owner} का AI असिस्टेंट बोल रहा हूँ। वे अभी सीधे बात नहीं कर सकते। "
            "कृपया अपना नाम और काम बताइए, या अगर आपके पास सीक्रेट कोड है तो वह बोलिए।"
        )

    async def say(self, text, after=None):
        if not self.active:
            return
        self.speaking = True
        self.listening = False
        self.log("🤖 " + text)
        try:
            pcm = await tts_pcm(text, self.voice)
        except Exception as e:
            self.log(f"⚠️ आवाज़ बनाने में दिक्कत (इंटरनेट?): {e}")
            pcm = np.zeros(3200, np.int16)
        await self._stream(pcm)
        self.ignore_until = time.monotonic() + 0.3
        self.speaking = False
        if after is not None and self.active:
            await after()
        if self.active and not self.unlocked:
            if self.rec is not None:
                try:
                    self.rec.Reset()
                except Exception:
                    pass
            self.listening = True
            self._schedule_silence()

    async def _stream(self, pcm):
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        k = 0
        for i in range(0, len(pcm), 320):
            if not self.active:
                return
            frame = pcm[i:i + 320]
            if len(frame) < 320:
                frame = np.pad(frame, (0, 320 - len(frame)))
            self.send_frame(frame)
            k += 1
            delay = t0 + k * 0.02 - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)

    def feed(self, pcm):
        if not self.active or not self.listening or self.speaking or self.rec is None:
            return
        if time.monotonic() < self.ignore_until:
            return
        try:
            if self.rec.AcceptWaveform(pcm.tobytes()):
                text = json.loads(self.rec.Result()).get("text", "").strip()
                if text:
                    self.listening = False
                    asyncio.ensure_future(self._heard(text))
        except Exception as e:
            self.log(f"⚠️ {e}")

    def on_digit(self, ch):
        if not self.active or self.unlocked:
            return
        self.digits = (self.digits + ch)[-16:]
        self.log("⌨️ कीपैड: " + "*" * len(self.digits))
        if self.digits.endswith(self.code):
            asyncio.ensure_future(self._unlock())

    def _code_ok(self, text):
        toks = re.split(r"[\s,.\-]+", text.lower())
        joined = "".join(WORD_DIGITS.get(t, t) for t in toks)
        return self.code in "".join(c for c in joined if c.isdigit())

    async def _heard(self, text):
        self._cancel_silence()
        self.silent = 0
        self.log("👤 " + text)
        self.rounds += 1
        if self._code_ok(text):
            await self._unlock()
        elif self.rounds >= self.max_rounds:
            await self.say("आपका कोड नहीं मिला। कृपया बाद में दोबारा कॉल कीजिए। धन्यवाद।", after=self.on_giveup)
        else:
            await self.say(REPLIES[(self.rounds - 1) % len(REPLIES)])

    async def _unlock(self):
        if self.unlocked:
            return
        self.unlocked = True
        self.listening = False
        self._cancel_silence()
        self.log("✅ सही कोड मिला")
        await self.say("कोड सही है। कृपया लाइन पर रहिए, मैं आपको कनेक्ट कर रहा हूँ।", after=self.on_unlock)

    def _schedule_silence(self):
        self._cancel_silence()
        self._silence_task = asyncio.ensure_future(self._silence_wait())

    def _cancel_silence(self):
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        self._silence_task = None

    async def _silence_wait(self):
        try:
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            return
        if not self.active or self.unlocked or self.speaking or not self.listening:
            return
        self.silent += 1
        if self.silent >= 3:
            await self.say("कोई आवाज़ नहीं आई। कृपया बाद में कॉल कीजिए। धन्यवाद।", after=self.on_giveup)
        else:
            await self.say("क्या आप मुझे सुन पा रहे हैं? अपना नाम और काम बताइए, या कोड बोलिए।")

    def stop(self):
        self.active = False
        self.listening = False
        self._cancel_silence()
        self.rec = None
        self.model = None
