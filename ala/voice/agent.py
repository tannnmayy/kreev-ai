"""Ala's LiveKit worker.

    uv run python -m ala.voice.agent dev        # serve calls, with auto-reload
    uv run python -m ala.voice.agent start      # serve calls
    uv run python -m ala.voice.agent download-files

LiveKit is the transport: WebRTC audio, VAD, speech services, playback and barge-in. Everything that decides anything is ours:
perception (ala/voice/listening.py) filters what reaches the brain, the coordinator holds actions, and the brain replaces the
framework's LLM step. One call = one room = one brain, coordinator and perception. The router is shared, because free quotas
belong to the account, not the call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncIterable, Dict, List, Optional

from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobExecutorType, JobProcess, llm, stt
from livekit.agents.types import FlushSentinel
from livekit.agents.voice import ModelSettings, room_io
from livekit.plugins import silero

from ..config import SETTINGS
from ..coordinator import Coordinator, Superseded
from ..language.speakable import speakable
from ..minds.brain import Brain
from ..minds.rules import open_request
from ..minds.router import Router
from ..perception.turn import SMART_TURN
from ..shop.catalog import Catalog
from ..shop.engine import ShopEngine
from ..shop.replies import Say
from ..shop.state import Customer, SessionState
from ..shop.store import MemoryStore, Store, open_store
from .listening import Perception
from .speech import NoLLM, Voice, build_stt, build_voice

log = logging.getLogger("ala.agent")
logging.getLogger("ala").setLevel(logging.INFO)

# With no LiveKit Cloud project set, talk to the local development server
if not os.environ.get("LIVEKIT_URL"):
    os.environ.update(LIVEKIT_URL="ws://localhost:7880", LIVEKIT_API_KEY="devkey", LIVEKIT_API_SECRET="secret")

SETTLE_MAX_S = 1.2       # how long after speech ends Ala waits for its transcript
HUD_EVERY_S = 0.25
STILL_THERE = Say("Are you still there?", "ఉన్నారా అండి?")
TAKE_YOUR_TIME = Say("Take your time.", "పర్వాలేదు, మీ సమయం తీసుకోండి.")
TIME_UP = Say("We've reached the time limit for this demo call. Your cart is saved. Goodbye.",
              "ఈ డెమో కాల్ సమయం అయిపోయింది. మీ కార్ట్ సేవ్ అయింది. ఉంటానండి.")
CART_RESTORED = Say("Welcome back. Your cart is just as you left it.", "మళ్ళీ స్వాగతం. మీ కార్ట్ అలాగే ఉంది.")

CATALOG = Catalog.load()
ROUTER = Router()
_store: Optional[Store] = None


def store() -> Store:
    global _store
    if _store is None:
        try:
            _store = open_store()
        except Exception as exc:
            log.warning("no database (%s); this call's data won't outlive it", str(exc)[:120])
            _store = MemoryStore()
    return _store


class Trace:
    """The run log: one JSON line per event, kept for the flag button and the benchmarks."""

    def __init__(self, room: str) -> None:
        self.path = ""
        self.tail: List[Dict[str, Any]] = []
        if SETTINGS.trace_dir:
            os.makedirs(SETTINGS.trace_dir, exist_ok=True)
            self.path = os.path.join(SETTINGS.trace_dir, room + ".jsonl")

    def __call__(self, kind: str, **data: Any) -> None:
        row = {"t": time.time(), "kind": kind, **data}
        self.tail = (self.tail + [row])[-300:]
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def last_minute(self) -> List[Dict[str, Any]]:
        cutoff = time.time() - 60
        return [r for r in self.tail if r["t"] >= cutoff]


class AlaAgent(Agent):
    def __init__(self, brain: Brain, coord: Coordinator, perception: Perception, voice: Voice, trace: Trace, publish) -> None:
        super().__init__(instructions="Ala")
        self.brain = brain
        self.coord = coord
        self.perception = perception
        self.voice = voice
        self.trace = trace
        self.publish = publish
        self.reply_lang = "te"
        self._consumed = 0
        self._pending_consumed: Optional[int] = None
        self._asked_at = 0.0
        self.typed_turn = False     # typed words are a finished thought; spoken ones may go on after a pause

    # the ears: keep a copy of the audio, and judge every transcript before LiveKit does
    async def stt_node(self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings):
        async def tee():
            async for frame in audio:
                self.perception.on_audio(frame)
                yield frame

        async for event in Agent.default.stt_node(self, tee(), model_settings):
            if isinstance(event, stt.SpeechEvent) and event.alternatives:
                text = event.alternatives[0].text
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                    if not self.perception.on_transcript(text, True):
                        continue
                    self.coord.heard(text)
                    self.trace("heard", text=text)
                    self.publish({"kind": "caption", "who": "customer", "text": text, "final": True})
                elif event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT:
                    if not self.perception.on_transcript(text, False):
                        continue
                    self.publish({"kind": "caption", "who": "customer", "text": text, "final": False})
            yield event

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        self.coord.turn_committed()
        self.trace("user_turn", text=(new_message.text_content or "").strip())

    def _user_messages(self, chat_ctx: llm.ChatContext) -> List[str]:
        return [item.text_content.strip() for item in chat_ctx.items
                if isinstance(item, llm.ChatMessage) and item.role == "user" and item.text_content]

    def _open_request(self, chat_ctx: llm.ChatContext) -> str:
        """Everything said since the last answered request. A turn cut short is re-read with what followed, so a correction
        always sees what it corrects."""
        return open_request(self._user_messages(chat_ctx)[self._consumed:])

    async def _quiet_for(self, seconds: float, epoch: int) -> None:
        c = self.coord
        while True:
            if c.overtaken(epoch):
                raise Superseded()
            quiet = 0.0 if c.user_speaking else c.clock() - c.last_speech_end
            if quiet >= seconds:
                return
            await asyncio.sleep(0.05)

    async def llm_node(self, chat_ctx: llm.ChatContext, tools: List[llm.Tool], model_settings: ModelSettings):
        text = self._open_request(chat_ctx)
        if not text:
            return
        upto = len(self._user_messages(chat_ctx))
        epoch = self.coord.epoch
        self._asked_at = time.time()
        spoke = False
        typed, self.typed_turn = self.typed_turn, False
        try:
            await self.coord.settle(epoch, SETTLE_MAX_S)
            reading = await self.perception.reading(text)
            pending = self.brain.shop.state.pending
            a = await self.perception.assess(text, expecting_answer=pending is not None, reading=reading)
            if a.hold_s > self.coord.commit_hold_s:
                # the sentence sounds unfinished: wait before spending a model call on it
                await self._quiet_for(a.hold_s, epoch)
            async for speech in self.brain.respond(text, reading=reading, epoch=epoch, seen=typed or self.voice.engine is None):
                self._pending_consumed = upto
                self.reply_lang = speech.lang
                self.voice.use(speech.lang, self.brain.pace)
                self.perception.said_now = speech.text
                self.publish({"kind": "caption", "who": "ala", "text": speech.text, "lang": speech.lang, "route": speech.route,
                              "final": True, "bridge": speech.kind == "bridge"})
                self.trace("ala_says", text=speech.text, lang=speech.lang, route=speech.route, part=speech.kind)
                spoke = True
                if self.brain.acted or typed or self.voice.engine is None:
                    # it happened, so this request is answered even if the customer cuts the reply off: re-reading it with
                    # their next words could do "two more" twice
                    self._consumed = upto
                yield speech.text + " "
                yield FlushSentinel()
        except Superseded:
            self.trace("superseded", text=text)
            return
        if not spoke:
            # nothing to say ("wait", a backchannel): the request is answered all the same
            self._consumed = upto
            self.coord.agent_acted()
            self.perception.answered(self._asked_at)

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        async def spoken():
            async for chunk in text:
                yield speakable(chunk, self.reply_lang)

        async for frame in Agent.default.tts_node(self, spoken(), model_settings):
            yield frame

    def answered(self) -> None:
        """Ala has started answering, so the customer's next words are a new request. A reply drafted in a pause and thrown
        away before any of it played doesn't count: those words are read again with what followed."""
        if self._pending_consumed is not None:
            self._consumed = self._pending_consumed
            self._pending_consumed = None
            self.coord.agent_acted()
            self.perception.answered(self._asked_at)

    def delivered(self, said: str, interrupted: bool) -> None:
        """An assistant message reached the conversation, as far as the customer heard it."""
        self.answered()
        self.brain.reply_heard(self.brain.last_reply, said, interrupted)
        if interrupted:
            self.publish({"kind": "heard", "text": said})


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(min_speech_duration=0.08, min_silence_duration=0.35, activation_threshold=0.5)
    SMART_TURN.load()


def _load() -> float:
    try:
        return min(1.0, len(server.active_jobs) / 5.0)
    except Exception:
        return 0.0


server = AgentServer(setup_fnc=prewarm, load_fnc=_load, load_threshold=0.99, job_executor_type=JobExecutorType.THREAD,
                     num_idle_processes=int(os.environ.get("ALA_IDLE_PROCESSES", "1")), initialize_process_timeout=240.0)


def turn_detection() -> Any:
    """LiveKit's audio turn detector where it can run, with Telugu switched on for testing; plain VAD otherwise. Our own
    holds, Smart Turn and cue words work on top of either."""
    mode = os.environ.get("ALA_TURN", "vad")
    if mode == "livekit":
        try:
            from livekit.agents import inference
            return inference.TurnDetector(unlikely_threshold={"te": float(os.environ.get("ALA_TURN_TE", "0.3"))})
        except Exception as exc:
            log.warning("LiveKit's turn detector isn't available: %s", str(exc)[:160])
    return "vad"


def room_options(can_listen: bool, can_speak: bool) -> room_io.RoomOptions:
    """Noise suppression needs LiveKit Cloud; the local server goes without it. With no speech services at all the call
    still runs, on typed words and captions."""
    url = os.environ.get("LIVEKIT_URL", "")
    audio: Dict[str, Any] = {}
    if "livekit.cloud" in url and os.environ.get("ALA_NOISE", "nc") != "off":
        try:
            from livekit.plugins import noise_cancellation
            audio["noise_cancellation"] = noise_cancellation.NC()
        except Exception as exc:
            log.warning("no noise suppression: %s", str(exc)[:120])
    return room_io.RoomOptions(audio_input=room_io.AudioInputOptions(**audio) if can_listen else False,
                               audio_output=can_speak, text_input=False)


async def restore(meta: Dict[str, Any]) -> SessionState:
    """The cart comes back after a refresh, and a returning customer's habits with their name."""
    key = str(meta.get("session") or "")
    name = " ".join(str(meta.get("name") or "").split())[:40]
    s = store()
    state = SessionState()
    try:
        saved = await s.load_session(key) if key else None
        if saved:
            state = SessionState.from_dict(saved)
        if name:
            profile = await s.load_profile(name)
            state.customer = Customer(name=name, returning=bool(profile), avoid=(profile or {}).get("avoid", []),
                                      usual=(profile or {}).get("usual", []), cut=(profile or {}).get("cut", {}))
    except Exception as exc:
        log.warning("could not restore the session: %s", str(exc)[:160])
    return state


@server.rtc_session(agent_name=SETTINGS.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except ValueError:
        meta = {}
    if meta.get("warm"):
        return      # the page woke the worker up; there's no call yet
    room = ctx.room.name
    trace = Trace(room)

    def publish(msg: Dict[str, Any]) -> None:
        try:
            data = json.dumps(msg, default=str, ensure_ascii=False)
            asyncio.ensure_future(ctx.room.local_participant.publish_data(data, reliable=msg.get("kind") != "hud", topic="ala"))
        except Exception as exc:     # the screen is a view; it must never break the conversation
            log.debug("could not publish: %s", exc)

    await ctx.connect()
    state = await restore(meta)
    try:
        stock = await store().load_stock()
    except Exception:
        stock = {}

    coord = Coordinator(commit_hold_s=SETTINGS.commit_hold_s, order_hold_s=SETTINGS.order_hold_s,
                        revising_hold_s=SETTINGS.revising_hold_s, dangling_hold_s=SETTINGS.dangling_hold_s)
    shop = ShopEngine(CATALOG, state, stock)

    def push_state() -> None:
        publish({"kind": "state", **shop.view()})

    shop.on_change = push_state

    def on_event(kind: str, **data: Any) -> None:
        trace(kind, **data)
        if kind in ("route", "tool", "tone_ask", "minds_busy", "listen"):
            publish({"kind": "brain", "event": kind, **data})

    brain = Brain(shop, ROUTER, coord, store(), str(meta.get("session") or room), on_event=on_event)
    if meta.get("lang") in ("te", "en"):
        brain.language.reply = meta["lang"]

    listening = build_stt(CATALOG)
    voice = build_voice()
    session: Optional[AgentSession] = None

    def interrupt() -> None:
        if session is not None:
            try:
                asyncio.ensure_future(_interrupt(session))
            except Exception as exc:
                log.debug("could not interrupt: %s", exc)

    def say(text: Say) -> None:
        if session is not None:
            lang = brain.language.reply
            voice.use(lang, brain.pace)
            agent.reply_lang = lang
            publish({"kind": "caption", "who": "ala", "text": text.text(lang), "lang": lang, "final": True})
            if voice.engine is not None:
                session.say(text.text(lang))

    perception = Perception(coord, CATALOG, publish=publish, interrupt=interrupt, trace=trace,
                            notice_side_talk=lambda: say(TAKE_YOUR_TIME))
    agent = AlaAgent(brain, coord, perception, voice, trace, publish)

    turn: Dict[str, Any] = {
        "turn_detection": turn_detection(),
        "endpointing": {"min_delay": SETTINGS.endpoint_min_s, "max_delay": SETTINGS.endpoint_max_s},
        "preemptive_generation": {"enabled": False},    # every early plan would spend free quota
        "interruption": {"min_duration": 0.6, "resume_false_interruption": True, "false_interruption_timeout": 0.7},
    }
    if os.environ.get("ALA_INTERRUPT") in ("adaptive", "vad"):
        turn["interruption"]["mode"] = os.environ["ALA_INTERRUPT"]
    session = AgentSession(vad=ctx.proc.userdata.get("vad"), stt=listening, llm=NoLLM(), tts=voice.engine, turn_handling=turn,
                           user_away_timeout=14.0)

    asked_if_there = {"done": False}

    @session.on("user_state_changed")
    def _user_state(ev) -> None:
        if ev.new_state == "speaking":
            asked_if_there["done"] = False
            coord.user_started_speaking()
            perception.speech_started()
        elif ev.old_state == "speaking":
            coord.user_stopped_speaking()
            perception.speech_ended()
        elif ev.new_state == "away" and not asked_if_there["done"] and not perception.ala_speaking:
            asked_if_there["done"] = True
            say(STILL_THERE)
        trace("user_state", state=ev.new_state)

    @session.on("agent_state_changed")
    def _agent_state(ev) -> None:
        perception.ala_speaking = ev.new_state == "speaking"
        if ev.new_state == "speaking":
            agent.answered()
        publish({"kind": "agent_state", "state": ev.new_state})
        trace("agent_state", state=ev.new_state)

    @session.on("conversation_item_added")
    def _item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) == "assistant":
            interrupted = bool(getattr(item, "interrupted", False))
            said = item.text_content or ""
            timing = {k: round(v, 3) for k, v in (getattr(item, "metrics", None) or {}).items() if isinstance(v, (int, float))}
            trace("agent_said", text=said, interrupted=interrupted, **timing)
            agent.delivered(said, interrupted)

    @ctx.room.on("data_received")
    def _from_client(packet: rtc.DataPacket) -> None:
        if packet.topic != "ala.client":
            return
        try:
            msg = json.loads(packet.data.decode("utf-8"))
        except ValueError:
            return
        kind = msg.get("kind")
        if kind == "text":
            text = str(msg.get("text", "")).strip()[:500]
            if text and session is not None:
                trace("typed", text=text)
                coord.heard(text)
                coord.turn_committed()
                agent.typed_turn = True
                publish({"kind": "caption", "who": "customer", "text": text, "final": True})
                session.interrupt()
                session.generate_reply(user_input=text)
        elif kind == "standin" and hasattr(listening, "expect"):
            listening.expect(str(msg.get("text", "")))
        elif kind == "flag":
            flag = {"note": str(msg.get("note", ""))[:500], "at": time.time(), "room": room, "trace": trace.last_minute()}
            trace("flag", note=flag["note"])
            asyncio.ensure_future(store().save_flag("%s-%d" % (room, int(flag["at"])), flag))
            publish({"kind": "flagged"})
        elif kind == "sold_out":
            sku = str(msg.get("sku", ""))
            if sku in CATALOG.by_sku:
                shop.stock[sku] = 0
                asyncio.ensure_future(store().set_stock(sku, 0))
                push_state()
                trace("sold_out", sku=sku)

    async def hud() -> None:
        while ctx.room.isconnected():
            publish(perception.hud())
            await asyncio.sleep(HUD_EVERY_S)

    async def time_limit() -> None:
        await asyncio.sleep(SETTINGS.max_call_minutes * 60)
        say(TIME_UP)
        await asyncio.sleep(8)
        ctx.shutdown("time limit")

    trace("session_start", room=room, stt=type(listening).__name__, tts=voice.model, models=ROUTER.available)
    await session.start(room=ctx.room, agent=agent, room_options=room_options(listening is not None, voice.engine is not None))
    tasks = [asyncio.ensure_future(hud()), asyncio.ensure_future(time_limit())]

    async def stop_tasks() -> None:
        for t in tasks:
            t.cancel()

    ctx.add_shutdown_callback(stop_tasks)
    publish({"kind": "ready", "voice": voice.model or None, "listening": type(listening).__name__ if listening else None,
             "models": ROUTER.status(), "text_only": listening is None})
    push_state()
    if state.cart:
        say(CART_RESTORED)
    else:
        first = brain.greeting()
        brain.language.reply = first.lang
        agent.reply_lang = first.lang
        voice.use(first.lang, brain.pace)
        publish({"kind": "caption", "who": "ala", "text": first.text, "lang": first.lang, "final": True})
        if voice.engine is not None:
            session.say(first.text)


async def _interrupt(session: AgentSession) -> None:
    await session.interrupt()


if __name__ == "__main__":
    agents.cli.run_app(server)
