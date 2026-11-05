"""Stand-in ears and mouth: the real voice path, with no keys.

    ALA_STANDIN=1 uv run python -m ala.voice.agent start

Everything around them stays real: WebRTC audio, Silero VAD, LiveKit's turn handling and barge-in, perception, the coordinator,
playback and the "how much was heard" accounting. Only two services are replaced:

* The ears hear real audio and find speech by its energy, but they can't recognise words. The test customer says what it's about
  to say in a data message, and they report that text with the timing a real service would have.
* The mouth plays a quiet tone for as long as the sentence would take to say.

ala.bench.voicecheck drives this. A real recogniser or voice is measured by the bake-off, never by this file.
"""

from __future__ import annotations

import collections
from typing import Any, Deque

import numpy as np
from livekit import rtc
from livekit.agents import APIConnectOptions, stt, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

SPEECH_DB = -38.0            # louder than this is speech
QUIET_FOR_S = 0.35           # a real service finishes a phrase about this long after it ends
INTERIM_AFTER_S = 0.25
SECONDS_PER_CHAR = 0.06      # about how fast the real voice reads
RATE = 24000


def _level_db(frame: rtc.AudioFrame) -> float:
    pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
    return float(20 * np.log10(np.sqrt(np.mean(pcm * pcm)) + 1e-9))


class StandInSTT(stt.STT):
    def __init__(self) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=True))
        self.upcoming: Deque[str] = collections.deque()

    def expect(self, text: str) -> None:
        self.upcoming.append(text)

    async def _recognize_impl(self, buffer: Any, *, language: Any = NOT_GIVEN, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS):
        raise NotImplementedError("the stand-in only streams")

    def stream(self, *, language: NotGivenOr[str] = NOT_GIVEN, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> stt.RecognizeStream:
        return _Hearing(stt=self, conn_options=conn_options)


class _Hearing(stt.RecognizeStream):
    async def _run(self) -> None:
        speech_s = quiet_s = 0.0
        speaking = sent_interim = False
        words = ""

        def send(kind: stt.SpeechEventType, text: str = "") -> None:
            alternatives = [stt.SpeechData(language="en", text=text)] if text else []
            self._event_ch.send_nowait(stt.SpeechEvent(type=kind, alternatives=alternatives))

        async for item in self._input_ch:
            if not isinstance(item, rtc.AudioFrame):
                continue
            seconds = item.samples_per_channel / item.sample_rate
            if _level_db(item) > SPEECH_DB:
                quiet_s = 0.0
                speech_s += seconds
                if not speaking:
                    speaking, sent_interim = True, False
                    words = self._stt.upcoming.popleft() if self._stt.upcoming else ""
                    send(stt.SpeechEventType.START_OF_SPEECH)
                if words and not sent_interim and speech_s >= INTERIM_AFTER_S:
                    sent_interim = True
                    send(stt.SpeechEventType.INTERIM_TRANSCRIPT, words)
            elif speaking:
                quiet_s += seconds
                if quiet_s >= QUIET_FOR_S:
                    if words:
                        send(stt.SpeechEventType.FINAL_TRANSCRIPT, words)
                    send(stt.SpeechEventType.END_OF_SPEECH)
                    speaking, speech_s, words = False, 0.0, ""


class StandInTTS(tts.TTS):
    def __init__(self) -> None:
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=RATE, num_channels=1)

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> tts.ChunkedStream:
        return _Speaking(tts=self, input_text=text, conn_options=conn_options)


class _Speaking(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(request_id=utils.shortuuid(), sample_rate=RATE, num_channels=1, mime_type="audio/pcm")
        n = int(len(self.input_text) * SECONDS_PER_CHAR * RATE)
        tone = 0.1 * np.sin(2 * np.pi * 220 * np.arange(n) / RATE)
        output_emitter.push((tone * 32767).astype(np.int16).tobytes())
        output_emitter.flush()
