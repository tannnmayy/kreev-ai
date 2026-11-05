"""The perception layer of one call: audio in, decisions out.

It keeps the last minute of audio, measures each stretch of speech, and classifies every transcript before LiveKit sees it. An
utterance judged a backchannel, echo, side talk or background is dropped here, so it can neither stop Ala nor reach the minds.
Every decision is published with its reason for the "What Ala hears" panel.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Any, Callable, Deque, Dict, Optional, Tuple

import numpy as np
from livekit import rtc

from ..coordinator import Coordinator
from ..language import cues
from ..perception import overlap
from ..perception.features import analyse, level_db, median_pitch, speech_level, to_16k
from ..perception.reading import Listener, Reading
from ..perception.turn import SMART_TURN, TurnAssessment, assess
from ..shop.catalog import Catalog

log = logging.getLogger("ala.listening")

RAISED_WAIT_S = 4.0
SIDE_TALK_NOTICE_S = 6.0


class AudioTape:
    """The last minute of customer audio with wall-clock times, downmixed to mono."""

    def __init__(self, seconds: float = 60.0) -> None:
        self.frames: Deque[Tuple[float, int, np.ndarray]] = collections.deque()
        self.seconds = seconds

    def add(self, frame: rtc.AudioFrame) -> np.ndarray:
        pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        if frame.num_channels > 1:
            pcm = pcm.reshape(-1, frame.num_channels).mean(axis=1)
        now = time.time()
        self.frames.append((now, frame.sample_rate, pcm))
        while self.frames and now - self.frames[0][0] > self.seconds:
            self.frames.popleft()
        return pcm

    def since(self, t0: float) -> Optional[Tuple[np.ndarray, float]]:
        """The audio since t0 at 16 kHz, and when it began."""
        chunks = [(t, rate, pcm) for t, rate, pcm in self.frames if t >= t0]
        if not chunks:
            return None
        rate = chunks[-1][1]
        same = [(t, pcm) for t, r, pcm in chunks if r == rate]
        audio = np.concatenate([pcm for _, pcm in same])
        return to_16k(audio, rate), same[0][0] - len(same[0][1]) / rate


class Perception:
    def __init__(self, coord: Coordinator, catalog: Catalog, *, publish: Callable[[Dict[str, Any]], None],
                 interrupt: Callable[[], None], trace: Callable[..., None], notice_side_talk: Callable[[], None]) -> None:
        self.coord = coord
        self.catalog = catalog
        self.publish = publish
        self.interrupt = interrupt
        self.trace = trace
        self.notice_side_talk = notice_side_talk
        self.tape = AudioTape()
        self.listener = Listener()
        self.ala_speaking = False
        self._over_ala = False
        self.said_now = ""
        self._speech_from = 0.0
        self._speech_floor = 0.0
        self._jobs: set = set()
        self._level = -90.0
        self._side_talk_since: Optional[float] = None
        self._side_talk_told = False
        coord.extra_hold = self.extra_hold

    # --- audio -------------------------------------------------------------------------------------------------
    def on_audio(self, frame: rtc.AudioFrame) -> None:
        pcm = self.tape.add(frame)
        self._level = level_db(pcm)

    def speech_started(self) -> None:
        self._speech_from = time.time()
        self._over_ala = self.ala_speaking

    @property
    def overlapping(self) -> bool:
        """Did these words begin over Ala? Barge-in pauses Ala within half a second, usually before the transcript arrives, so
        asking whether Ala is speaking now would turn every "mm hmm" into a turn of its own."""
        return self.ala_speaking or self._over_ala

    def speech_ended(self) -> None:
        now = time.time()
        got = self.tape.since(max(self._speech_from - 0.5, self._speech_floor))
        self._speech_floor = now
        if got is None:
            return
        audio, start = got
        slot = self.listener.expect(now)
        job = asyncio.ensure_future(self._measure(audio, start, slot))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    async def _measure(self, audio: np.ndarray, start: float, slot) -> None:
        try:
            f = await asyncio.to_thread(analyse, audio, start)
        except Exception as exc:     # the voice is a second channel; the words still work without it
            log.warning("a segment could not be measured: %s", exc)
            f = None
        f = self.listener.add(f, into=slot)
        if f is not None:
            self.trace("tone_segment", **f.summary())
            self.publish({"kind": "segment", **f.summary()})

    def _latest(self):
        measured = [s for s in self.listener.segments if s.measured and s.speech_s]
        return measured[-1] if measured else None

    def _level_vs_usual(self) -> Optional[float]:
        """These words' loudness against the customer's usual. It's measured from the tape here, because the segment's own
        measurement isn't ready until the speech has ended, and the words often come first."""
        usual = self.listener.baseline.usual_loudness()
        got = self.tape.since(self._speech_from) if self._speech_from else None
        level = speech_level(got[0]) if got is not None else None
        return None if usual is None or level is None else float(level - usual)

    def _sounds_distant(self, words: str) -> bool:
        level = self._level_vs_usual()
        return level is not None and level <= -overlap.QUIET_BELOW_USUAL_DB and not overlap.shop_talk(words, self.catalog)

    # --- words -------------------------------------------------------------------------------------------------
    def on_transcript(self, text: str, final: bool) -> bool:
        """Decisions 2 and 3, as words arrive. False: drop them, they aren't a turn."""
        words = (text or "").strip()
        if not words:
            return True
        if not final:
            # interim words can stop Ala at once, and a plain backchannel or echo never needs to go further
            if self.overlapping and cues.control(words) == "stop":
                self.interrupt()
                self._decide("interruption", "a stop word, heard before the sentence ended", words)
                return True
            if self.overlapping and (cues.control(words) == "backchannel" or overlap.is_echo(words, self.said_now)
                                     or self._sounds_distant(words)):
                return False
            return True
        latest = self._latest()
        over, self._over_ala = self.overlapping, False
        verdict = overlap.classify(words, ala_speaking=over, said_now=self.said_now,
                                   raised_voice=bool(latest and latest.raised()), level_vs_usual=self._level_vs_usual(),
                                   catalog=self.catalog)
        if verdict.kind != "speech":
            self._decide(verdict.kind, verdict.reason, words)
        if verdict.stop and over:
            self.interrupt()
        if verdict.kind == "side_talk":
            now = time.time()
            self._side_talk_since = self._side_talk_since or now
            if now - self._side_talk_since >= SIDE_TALK_NOTICE_S and not self._side_talk_told:
                self._side_talk_told = True
                self.notice_side_talk()
        elif verdict.reply:
            self._side_talk_since, self._side_talk_told = None, False
        if verdict.reply:
            self.listener.heard(words, time.time())
        return verdict.reply

    def _decide(self, kind: str, reason: str, words: str) -> None:
        self.trace("perception", decision=kind, reason=reason, text=words)
        self.publish({"kind": "decision", "decision": kind, "reason": reason, "text": words[:120]})

    # --- the turn ----------------------------------------------------------------------------------------------
    def extra_hold(self) -> float:
        """The coordinator's extra hold: the voice can only ever add to it."""
        if self.listener.awaiting_words():
            return RAISED_WAIT_S
        return self.coord.revising_hold_s if self.listener.reading(self.coord.open_utterance).unsure else 0.0

    async def reading(self, words: str) -> Reading:
        if self._jobs:
            await asyncio.wait(list(self._jobs), timeout=0.3)
        r = self.listener.reading(words)
        if r.label:
            self.trace("tone", **r.event())
            self.publish({"kind": "tone", **r.event()})
        return r

    async def assess(self, words: str, *, expecting_answer: bool, reading: Reading) -> TurnAssessment:
        """Decision 1: how long must the customer stay quiet? Smart Turn runs in a thread, off the audio path."""
        p_end = None
        got = self.tape.since(time.time() - 8.0)
        if got is not None:
            try:
                p_end = await asyncio.wait_for(asyncio.to_thread(SMART_TURN.score, got[0]), 0.25)
            except Exception:
                p_end = None
        c = self.coord
        a = assess(words, base_s=c.commit_hold_s, dangling_s=c.dangling_hold_s, revising_s=c.revising_hold_s, p_end=p_end,
                   expecting_answer=expecting_answer, unsure=reading.unsure, raised_waiting=self.listener.awaiting_words())
        self.trace("turn", hold_s=a.hold_s, reasons=a.reasons, p_end=p_end)
        self.publish({"kind": "turn", "hold_s": a.hold_s, "reasons": a.reasons, "p_end": p_end})
        return a

    def answered(self, before: float) -> None:
        self.listener.close(before)

    # --- the panel ---------------------------------------------------------------------------------------------
    def hud(self) -> Dict[str, Any]:
        got = self.tape.since(time.time() - 0.25)
        pitch = None
        if got is not None and len(got[0]) >= 2000 and self._level > -50:
            f0 = median_pitch(got[0])
            pitch = None if np.isnan(f0) else round(f0)
        return {"kind": "hud", "level": round(self._level, 1), "pitch": pitch, "ala_speaking": self.ala_speaking,
                "customer_speaking": self.coord.user_speaking}
