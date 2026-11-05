"""The coordinator: when is Ala allowed to act?

Carried over from Duet, where it scored 0.88 on Full-Duplex-Bench v3's self-corrections. The paper's headline failure is
"models commit intermediate parameters before the correction arrives". Three things prevent it:

* Epochs. Every time the customer starts speaking the epoch advances. Work planned under an older epoch was planned on
  words they may be changing. It's void the moment new speech brings words. Speech with no words (a cough, room noise)
  leaves it standing.
* The commit gate. Nothing changes while the customer is speaking, or until they've been quiet for a hold after their last
  word. A plan that meets new words at the gate is dropped, so a pre-correction value can never reach the cart.
* The ledger. An identical action already done in this conversation is answered from memory, never done twice.

New for KREEV: the hold depends on what's at stake and how the words end. A cart edit can be corrected later, so it waits
less than an order. Words can stretch any hold (an unfinished or self-correcting sentence), and how the customer sounds can
stretch it too, through `extra_hold`. Nothing here ever shortens a hold.

Everything here is per conversation.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .language import cues


TRANSCRIPT_COVERS_S = 0.5    # a final this close to the end of speech has the whole of it


class Superseded(Exception):
    """The customer said more before a planned action reached the gate."""


@dataclass
class CallRecord:
    tool: str
    args: Dict[str, Any]
    epoch: int
    started: float
    finished: float = 0.0
    outcome: str = "pending"  # ok | error | unknown | cached
    result: Any = None
    error: str = ""


def canonical(tool: str, args: Dict[str, Any]) -> str:
    """The ledger's key: the tool plus its arguments, case- and order-insensitive."""
    def norm(v: Any) -> Any:
        if isinstance(v, str):
            return " ".join(v.lower().split())
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, dict):
            return {k: norm(x) for k, x in sorted(v.items())}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v
    clean = {k: norm(v) for k, v in sorted((args or {}).items()) if v is not None}
    return tool + ":" + json.dumps(clean, sort_keys=True, default=str, ensure_ascii=False)


@dataclass
class Coordinator:
    commit_hold_s: float = 0.8          # quiet time before a cart edit may run
    order_hold_s: float = 1.4           # quiet time before an order step may run
    revising_hold_s: float = 1.8        # when the customer has been correcting themselves
    dangling_hold_s: float = 2.2        # when the last words leave the sentence open
    words_wait_s: float = 2.5           # after speech ends, how long its words may take to arrive
    clock: Callable[[], float] = time.monotonic

    epoch: int = 0
    committed_epoch: int = -1           # the epoch whose turn the listener has closed
    user_speaking: bool = False
    last_speech_end: float = 0.0
    last_heard_at: float = float("-inf")
    transcript_due: bool = False        # speech ended and its transcript hasn't arrived yet
    last_words_epoch: int = -1          # the epoch in which words were last heard
    open_utterance: str = ""            # what the customer said since Ala last acted
    ledger: Dict[str, CallRecord] = field(default_factory=dict)
    history: List[CallRecord] = field(default_factory=list)
    # the voice's say (ala/perception): a longer hold from how the customer sounds. It never shortens one.
    extra_hold: Optional[Callable[[], float]] = None
    heard_log: List[Tuple[int, str]] = field(default_factory=list)
    heard_count: int = 0

    # -- what the ears report ---------------------------------------------------------------------
    def user_started_speaking(self) -> None:
        self.epoch += 1
        self.user_speaking = True

    def user_stopped_speaking(self) -> None:
        """Words may still be on their way, unless a transcript landed just now: a service with its own endpointing often
        finishes before the VAD does, and waiting for words that already came adds a second to every turn."""
        self.user_speaking = False
        self.last_speech_end = self.clock()
        self.transcript_due = self.last_speech_end - self.last_heard_at > TRANSCRIPT_COVERS_S

    def heard(self, text: str) -> None:
        """A final transcript. Words that arrive after the turn closed are the tail of a segment still being
        transcribed when the end-of-turn detector fired, often right before a correction ("...from checking. Wait, no,
        make it savings"). A plan made without them is stale, so they advance the epoch as new speech does."""
        text = (text or "").strip()
        if not text:
            return
        if self.committed_epoch == self.epoch and not self.user_speaking:
            self.epoch += 1
        self.last_words_epoch = self.epoch
        self.last_heard_at = self.clock()
        self.transcript_due = False
        self.open_utterance = (self.open_utterance + " " + text).strip()
        self.heard_log = (self.heard_log + [(self.heard_count, text)])[-40:]
        self.heard_count += 1

    def words_from(self, n: int) -> str:
        """What the customer said from the n-th piece heard on."""
        return " ".join(text for i, text in self.heard_log if i >= n)

    def overtaken(self, epoch: int) -> bool:
        """Has the customer said new words since a plan made at `epoch`? Speech without words leaves it standing."""
        return self.last_words_epoch > epoch

    async def settle(self, epoch: int, max_wait_s: float) -> None:
        """Before a model call is spent on a closed turn: if the last words are still being transcribed, wait for them
        (at most max_wait_s after the speech ended; a noise burst has no words)."""
        while self.transcript_due and self.clock() - self.last_speech_end < max_wait_s:
            if self.overtaken(epoch):
                raise Superseded()
            await asyncio.sleep(0.03)
        if self.overtaken(epoch):
            raise Superseded()

    def agent_acted(self) -> None:
        self.open_utterance = ""

    def turn_committed(self) -> None:
        """The listener decided the customer's turn is over."""
        self.committed_epoch = self.epoch

    async def wait_committed(self, epoch: Optional[int] = None) -> int:
        """Planning may start early, during a pause; acting may not."""
        epoch = self.epoch if epoch is None else epoch
        while self.committed_epoch < epoch:
            if self.overtaken(epoch):
                raise Superseded()
            await asyncio.sleep(0.03)
        if self.overtaken(epoch):
            raise Superseded()
        return epoch

    def hold(self, base: Optional[float] = None) -> float:
        """How long the customer must have been quiet before an action may commit."""
        text = self.open_utterance
        hold = self.commit_hold_s if base is None else base
        if cues.unfinished(text):
            hold = max(hold, self.dangling_hold_s)
        elif cues.revising(text):
            hold = max(hold, self.revising_hold_s)
        if self.extra_hold is not None:
            try:
                hold = max(hold, float(self.extra_hold()))
            except Exception:  # the voice only ever adds to the hold
                pass
        return hold

    async def gate(self, epoch: Optional[int] = None, base: Optional[float] = None) -> int:
        """Wait until the customer has been quiet for the hold. Raises Superseded if a plan made under `epoch` has been
        overtaken by new words, even if the new turn has since closed. Returns the epoch it commits under."""
        epoch = await self.wait_committed(epoch)
        while True:
            if self.overtaken(epoch):
                raise Superseded()
            quiet_for = self.clock() - self.last_speech_end
            if self.user_speaking or (self.epoch != epoch and self.transcript_due and quiet_for < self.words_wait_s):
                await asyncio.sleep(0.05)
                continue
            hold = self.hold(base)
            if quiet_for >= hold:
                return self.epoch
            await asyncio.sleep(min(0.05, hold - quiet_for))

    async def execute(self, tool: str, args: Dict[str, Any], run: Callable[[], Awaitable[Any]], *, state_changing: bool = True,
                      timeout_s: float = 5.0, epoch: Optional[int] = None, base_hold: Optional[float] = None,
                      once: bool = False) -> CallRecord:
        """Run an action through the gate. `once`: the ledger refuses an identical repeat in this conversation."""
        key = canonical(tool, args)
        previous = self.ledger.get(key)
        if once and previous is not None and previous.outcome in ("ok", "unknown"):
            cached = CallRecord(tool, args, self.epoch, self.clock(), self.clock(), "cached", previous.result, previous.error)
            self.history.append(cached)
            return cached
        epoch = await self.gate(epoch, base_hold)
        record = CallRecord(tool, args, epoch, time.time())
        self.history.append(record)
        # Past the gate the action is committed. If the customer barges in now and the reply is cancelled, it still runs
        # to completion and is recorded: dropping it would leave something that happened unaccounted for.
        committed = asyncio.ensure_future(self._run_committed(key, record, run, state_changing, timeout_s))
        return await asyncio.shield(committed)

    async def _run_committed(self, key: str, record: CallRecord, run, state_changing: bool, timeout_s: float) -> CallRecord:
        attempts = 1 if state_changing else 2
        for attempt in range(attempts):
            try:
                record.result = await asyncio.wait_for(run(), timeout=timeout_s)
                record.outcome = "ok"
                break
            except asyncio.TimeoutError:
                record.error = "timed out after %.0f s" % timeout_s
                record.outcome = "unknown" if state_changing else "error"
            except Exception as exc:
                record.error = "%s: %s" % (type(exc).__name__, exc)
                record.outcome = "error"
            if attempt + 1 < attempts:
                await asyncio.sleep(0.3)
        record.finished = time.time()
        # a refusal isn't something that happened: remembering one would refuse the same action for the rest of the call,
        # such as an order after the customer has now heard the read-back
        if record.outcome in ("ok", "unknown") and getattr(record.result, "ok", True):
            self.ledger[key] = record
        return record
