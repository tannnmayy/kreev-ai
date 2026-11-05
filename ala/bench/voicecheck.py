"""A customer who really speaks: real audio over WebRTC, to the real agent, with stand-in ears and mouth.

    ALA_STANDIN=1 uv run python -m ala.voice.agent start     # and the LiveKit and web servers
    uv run python -m ala.bench.voicecheck

The smoke test types, so it never touches the audio path. This one streams speech into the room as a microphone would, in real
time and with silence between lines, then watches what Ala does: when it plays audio, when it stops, and what reaches the cart.
It checks the timing and the state, which is what needs no real recogniser: barge-in, backchannels, the read-back rule.

Clips come from Windows' built-in speech voices, so this needs Windows, or a folder of WAV files named as in LINES.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp
import numpy as np
from livekit import rtc

from ..config import ROOT
from .bakeoff import load_wav

RATE = 16000
FRAME = 160      # 10 ms
CLIPS = ROOT / ".tools" / "voice-clips"
LINES = {
    "prawns": "Half a kilo of prawns please.",
    "rice": "One kilo of rice and a kilo of onions.",
    "cart": "What is in my cart right now?",
    "stop": "Stop.",
    "mhm": "Mm hmm.",
    "order": "Place the order.",
    "yes": "Yes.",
    "wait": "One kilo of tomatoes. No wait, make it two kilos of tomatoes.",
    "tv": "The match will begin after the news at nine, stay with us tonight.",
}


def make_clips(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    missing = {k: v for k, v in LINES.items() if not (folder / (k + ".wav")).exists()}
    if not missing:
        return
    script = "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Rate = 1; "
    for name, line in missing.items():
        script += "$s.SetOutputToWaveFile('%s'); $s.Speak('%s'); " % (folder / (name + ".wav"), line.replace("'", "''"))
    script += "$s.Dispose()"
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], check=True)


class Caller:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.room = rtc.Room()
        self.source = rtc.AudioSource(RATE, 1)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.messages: List[Dict[str, Any]] = []
        self.state: Dict[str, Any] = {}
        self.ala_level = -90.0
        self.ala_audio_at = 0.0
        self.agent_state = ""
        self.ready = asyncio.Event()
        self._tasks: List[asyncio.Task] = []

    @property
    def ala_sounding(self) -> bool:
        return time.monotonic() - self.ala_audio_at < 0.25

    def on_data(self, packet: rtc.DataPacket) -> None:
        if packet.topic != "ala":
            return
        msg = json.loads(packet.data.decode("utf-8"))
        kind = msg.get("kind")
        if kind == "hud":
            return
        msg["at"] = time.monotonic()
        self.messages.append(msg)
        if kind == "state":
            self.state = msg
        elif kind == "agent_state":
            self.agent_state = msg["state"]
        elif kind == "ready":
            self.ready.set()

    async def hear(self, track: rtc.Track) -> None:
        async for event in rtc.AudioStream(track, sample_rate=RATE, num_channels=1):
            pcm = np.frombuffer(event.frame.data, dtype=np.int16).astype(np.float32) / 32768.0
            if float(np.sqrt(np.mean(pcm * pcm))) > 0.01:
                self.ala_audio_at = time.monotonic()

    async def microphone(self) -> None:
        """Always sending, as a real microphone is: speech when there is some, silence otherwise."""
        silence = np.zeros(FRAME, dtype=np.int16)
        next_at = time.monotonic()
        buffer = np.zeros(0, dtype=np.int16)
        while True:
            if not len(buffer) and not self.queue.empty():
                buffer = self.queue.get_nowait()
            chunk, buffer = (buffer[:FRAME], buffer[FRAME:]) if len(buffer) else (silence, buffer)
            if len(chunk) < FRAME:
                chunk = np.pad(chunk, (0, FRAME - len(chunk)))
            await self.source.capture_frame(rtc.AudioFrame(chunk.tobytes(), RATE, 1, FRAME))
            next_at += FRAME / RATE
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))

    async def join(self, base: str) -> None:
        query = urlencode({"name": "", "session": "voice-%d" % int(time.time()), "invite": ""})
        async with aiohttp.ClientSession() as http:
            async with http.get("%s/api/token?%s" % (base.rstrip("/"), query)) as r:
                r.raise_for_status()
                details = await r.json()

        @self.room.on("track_subscribed")
        def _track(track: rtc.Track, *_: Any) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                self._tasks.append(asyncio.ensure_future(self.hear(track)))

        self.room.on("data_received", self.on_data)
        await self.room.connect(details["url"], details["token"])
        mic = rtc.LocalAudioTrack.create_audio_track("mic", self.source)
        await self.room.local_participant.publish_track(mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        self._tasks.append(asyncio.ensure_future(self.microphone()))

    async def say(self, name: str, words: Optional[str] = None, gain: float = 0.7) -> float:
        """Speak a clip and return how long it lasts. The stand-in ears are told the words first."""
        await self.room.local_participant.publish_data(json.dumps({"kind": "standin", "text": words or LINES[name]}), reliable=True,
                                                       topic="ala.client")
        await asyncio.sleep(0.05)
        pcm = load_wav(self.folder / (name + ".wav"))
        self.queue.put_nowait((np.clip(pcm, -1, 1) * 32767 * gain).astype(np.int16))
        return len(pcm) / RATE

    async def until(self, test: Callable[[], Any], seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if test():
                return True
            await asyncio.sleep(0.02)
        return bool(test())

    def captions(self, since: float) -> List[str]:
        return [m["text"] for m in self.messages if m.get("kind") == "caption" and m.get("who") == "ala" and m["at"] >= since]

    def cart(self) -> Dict[str, float]:
        return {x["sku"]: x["qty"] for x in self.state.get("cart", [])}

    async def quiet(self, seconds: float = 25.0) -> None:
        """Wait until Ala has finished speaking."""
        await asyncio.sleep(0.3)
        await self.until(lambda: not self.ala_sounding and self.agent_state != "speaking", seconds)
        await asyncio.sleep(0.5)

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        await self.room.disconnect()


async def run(base: str, folder: Path) -> int:
    make_clips(folder)
    c = Caller(folder)
    results: List[bool] = []

    def check(ok: Any, what: str, detail: Any = "") -> None:
        results.append(bool(ok))
        print("%s %s %s" % ("ok  " if ok else "FAIL", what, detail if detail != "" else ""), flush=True)

    await c.join(base)
    await asyncio.wait_for(c.ready.wait(), 60)
    check(not next((m for m in c.messages if m.get("kind") == "ready"), {}).get("text_only"), "the call has voice in both directions")

    check(await c.until(lambda: c.ala_sounding, 15), "the greeting is heard as real audio")
    await c.quiet()

    # 1. a plain spoken turn
    t0 = time.monotonic()
    length = await c.say("prawns")
    ok = await c.until(lambda: "royyalu" in c.cart(), 12)
    check(ok, "a spoken request reaches the cart", c.cart())
    await c.until(lambda: c.ala_sounding, 8)
    gap = time.monotonic() - t0 - length
    check(c.ala_sounding, "Ala answers aloud", "%.2f s after the customer stopped (stand-in services)" % gap)
    await c.quiet()

    # 2. a backchannel during a long reply must not stop Ala
    t0 = time.monotonic()
    await c.say("rice")
    await c.until(lambda: c.ala_sounding, 10)
    await c.quiet()
    await c.say("cart")
    await c.until(lambda: c.ala_sounding, 10)
    await asyncio.sleep(0.8)
    t1 = time.monotonic()
    await c.say("mhm")
    await asyncio.sleep(1.2)
    went_on = await c.until(lambda: c.ala_sounding, 3.0)
    heard = [m for m in c.messages if m.get("kind") == "heard" and m["at"] >= t1]
    check(went_on and not heard, "a backchannel leaves the reply running", "it went on %.1f s after the sound began" % (time.monotonic() - t1))
    await c.quiet()

    # 3. a stop word cuts Ala off, quickly, and it knows how much was heard
    await c.say("cart", "what is in my cart and how much is the total")
    await c.until(lambda: c.ala_sounding, 10)
    await asyncio.sleep(0.8)
    t1 = time.monotonic()
    length = await c.say("stop")
    stopped = await c.until(lambda: not c.ala_sounding and time.monotonic() - t1 > 0.1, 6)
    check(stopped, "a stop word silences Ala", "%.2f s after it began, the word lasted %.2f s" % (time.monotonic() - t1, length))
    await asyncio.sleep(1.2)
    check(any(m.get("kind") == "heard" and m["at"] >= t1 for m in c.messages), "Ala records how much of the reply was heard")
    await c.quiet()

    # 3b. a whole sentence over a reply takes the turn
    await c.say("cart", "what is in my cart and how much is the total")
    await c.until(lambda: c.ala_sounding, 10)
    await asyncio.sleep(0.8)
    t1 = time.monotonic()
    await c.say("cart", "what is in my cart right now")
    await c.until(lambda: any(m.get("kind") == "heard" and m["at"] >= t1 for m in c.messages), 6)
    check(await c.until(lambda: len(c.captions(t1)) >= 1, 6), "a real interruption takes the turn", c.captions(t1)[:1])
    await c.quiet()

    # 3c. a voice in the room that isn't talking to Ala
    await c.say("cart", "what is in my cart and how much is the total")
    await c.until(lambda: c.ala_sounding, 10)
    await asyncio.sleep(0.8)
    t1, before = time.monotonic(), c.cart()
    await c.say("tv", gain=0.12)      # about 15 dB below the customer, as a television across the room would be
    await asyncio.sleep(8.0)
    check(c.cart() == before and not c.captions(t1), "a quiet voice in the room gets no answer", c.captions(t1)[:1])
    await c.quiet()

    # 4. a yes that cuts the read-back short must not place the order
    await c.say("order")
    await c.until(lambda: c.ala_sounding, 10)
    await asyncio.sleep(0.8)
    t1 = time.monotonic()
    await c.say("yes")
    await asyncio.sleep(3.0)
    check(not c.state.get("orders"), "a yes over an unfinished read-back places no order", c.captions(t1)[-1:])
    check(any("?" in x for x in c.captions(t1)), "and Ala asks again", "")
    await c.quiet()

    # 5. the read-back heard to the end, then yes
    t1 = time.monotonic()
    await c.say("yes")
    check(await c.until(lambda: c.state.get("orders"), 10), "a yes after the whole read-back places the order", c.captions(t1)[-1:])
    await c.quiet()

    # 6. a correction in one breath leaves only the corrected value
    await c.say("wait")
    await c.until(lambda: "tamatalu" in c.cart() or "tomato" in str(c.cart()), 12)
    await asyncio.sleep(1.5)
    tomato = [v for k, v in c.cart().items() if "tomat" in k or "tamat" in k]
    check(tomato == [2], "a self-correction leaves only the corrected quantity", c.cart())
    await c.quiet()

    await c.close()
    print("%d of %d voice checks passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8787")
    parser.add_argument("--clips", type=Path, default=CLIPS)
    a = parser.parse_args()
    sys.exit(asyncio.run(run(a.url, a.clips)))


if __name__ == "__main__":
    main()
