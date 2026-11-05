"""The bake-off: measured picks for listening, thinking and turn-taking.

    uv run python -m ala.bench.bakeoff keys                    # which keys work, and what each provider says
    uv run python -m ala.bench.bakeoff think                   # time to first answer, per fast-mind candidate
    uv run python -m ala.bench.bakeoff listen clips/lines.tsv  # word errors and item accuracy, per listening service
    uv run python -m ala.bench.bakeoff turns  clips/turns.tsv  # end-of-turn: voice only, then voice + words

clips/lines.tsv:  file <tab> what was said <tab> items that matter, comma separated (fish names, quantities, prices)
clips/turns.tsv:  file <tab> finished | unfinished <tab> transcript

Scoring on the words that matter matters more than plain word error: "రొయ్యలు" misheard costs a customer far more than a dropped
"అండి". For turns, the rule under test is that words only ever lengthen the wait, so a false cut-off rate is reported for each
layer, and a layer stays only if it earns its place.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..language import cues
from ..language.numbers import normalize
from ..minds import prompts
from ..minds.router import AllBusy, Router
from ..perception.features import to_16k
from ..perception.turn import SMART_TURN, UNFINISHED_BELOW


def _words(text: str) -> List[str]:
    return re.findall(r"[\wఀ-౿]+", normalize(text))


def edit_distance(a: Sequence[str], b: Sequence[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(reference: str, heard: str) -> float:
    ref = _words(reference)
    return edit_distance(ref, _words(heard)) / max(1, len(ref))


def cer(reference: str, heard: str) -> float:
    ref = "".join(_words(reference))
    return edit_distance(list(ref), list("".join(_words(heard)))) / max(1, len(ref))


def items_right(items: Iterable[str], heard: str) -> Tuple[int, int]:
    wanted = [normalize(i) for i in items if i.strip()]
    h = normalize(heard)
    return sum(1 for i in wanted if i in h), len(wanted)


def read_tsv(path: Path) -> List[List[str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            rows.append(line.split("\t"))
    return rows


def load_wav(path: Path) -> np.ndarray:
    import soundfile as sf
    pcm, rate = sf.read(str(path), dtype="float32", always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1)
    return to_16k(pcm, rate)


# --- turn-taking: the ablation --------------------------------------------------------------------------------------

@dataclass
class TurnScore:
    layer: str
    false_cutoffs: float     # share of unfinished clips Ala would have answered too early
    false_waits: float       # share of finished clips it would have waited on
    n: int


def score_turns(rows: Sequence[Tuple[str, Optional[float], str]]) -> List[TurnScore]:
    """rows: (finished | unfinished, Smart Turn's probability, transcript). A layer "waits" when it would stretch the hold."""
    def run(name: str, waits) -> TurnScore:
        unfinished = [r for r in rows if r[0] == "unfinished"]
        finished = [r for r in rows if r[0] == "finished"]
        cut = sum(1 for r in unfinished if not waits(r))
        extra = sum(1 for r in finished if waits(r))
        return TurnScore(name, cut / max(1, len(unfinished)), extra / max(1, len(finished)), len(rows))

    def voice(r) -> bool:
        return r[1] is not None and r[1] < UNFINISHED_BELOW

    def words(r) -> bool:
        return cues.unfinished(r[2]) or cues.revising(r[2])

    return [run("silence only", lambda r: False), run("voice", voice), run("words", words),
            run("voice + words", lambda r: voice(r) or words(r))]


async def cmd_turns(path: Path) -> None:
    rows = []
    for name, label, *rest in read_tsv(path):
        p = SMART_TURN.score(load_wav(path.parent / name))
        rows.append((label.strip(), p, rest[0] if rest else ""))
    for s in score_turns(rows):
        print("%-16s false cut-offs %5.1f%%   extra waits %5.1f%%   (%d clips)" % (s.layer, s.false_cutoffs * 100, s.false_waits * 100, s.n))


# --- thinking --------------------------------------------------------------------------------------------------------

TURNS = ["half a kilo of prawns but not too big", "అర కిలో రొయ్యలు శుభ్రం చేసి ఇవ్వండి, ఇంకా ఒక కిలో బియ్యం", "what would go well with fish pulusu tonight",
         "నాకు వేపుడుకి మంచి చేప చెప్పండి", "actually make that two and remove the onions"]


async def cmd_think(rounds: int) -> None:
    router = Router()
    for name in router.chains["fast"]:
        if not router.providers[name].usable:
            print("%-24s no key" % name)
            continue
        times, ok = [], 0
        one = Router(chains={"fast": [name]}, providers=router.providers)
        for i in range(rounds):
            payload = prompts.turn_payload(words=TURNS[i % len(TURNS)], reply_in="te" if i % 2 else "en", state={}, products=[], recent=[])
            started = time.monotonic()
            try:
                await one.complete("fast", prompts.messages(prompts.FAST_SYSTEM, payload), max_tokens=200, timeout_s=8)
                times.append(time.monotonic() - started)
                ok += 1
            except AllBusy as exc:
                print("   %s: %s" % (name, str(exc)[:100]))
        if times:
            print("%-24s median %.2f s   worst %.2f s   %d of %d answered" % (name, statistics.median(times), max(times), ok, rounds))
        await one.close()
    await router.close()


# --- listening -------------------------------------------------------------------------------------------------------

async def transcribe(service: str, pcm: np.ndarray) -> str:
    from livekit import rtc
    pcm16 = (np.clip(pcm, -1, 1) * 32767).astype(np.int16)
    frame = rtc.AudioFrame(pcm16.tobytes(), 16000, 1, len(pcm16))
    if service == "sarvam":
        from livekit.plugins import sarvam
        stt = sarvam.STT(language="unknown", mode="codemix")
    elif service == "gladia":
        from livekit.plugins import gladia
        stt = gladia.STT(languages=["te", "en"], code_switching=True)
    else:
        raise ValueError(service)
    event = await stt.recognize(frame)
    return event.alternatives[0].text if event.alternatives else ""


async def cmd_listen(path: Path) -> None:
    rows = read_tsv(path)
    services = [s for s, k in (("sarvam", "SARVAM_API_KEY"), ("gladia", "GLADIA_API_KEY")) if os.environ.get(k)]
    if not services:
        print("No listening keys in .env.local.")
        return
    for service in services:
        w, c, right, total, took = [], [], 0, 0, []
        for name, text, *rest in rows:
            pcm = load_wav(path.parent / name)
            started = time.monotonic()
            heard = await transcribe(service, pcm)
            took.append(time.monotonic() - started)
            w.append(wer(text, heard))
            c.append(cer(text, heard))
            r, t = items_right((rest[0] if rest else "").split(","), heard)
            right, total = right + r, total + t
        print("%-10s word error %5.1f%%   character error %5.1f%%   items right %d of %d   median %.2f s" % (
            service, statistics.mean(w) * 100, statistics.mean(c) * 100, right, total, statistics.median(took)))


async def check_model(router: Router, name: str) -> str:
    """One tiny real request, so a mistyped key or a retired model shows up now and not in front of a judge."""
    if not router.providers[name].usable:
        return "no key"
    one = Router(chains={"ping": [name]}, providers=router.providers, http=router.http)
    try:
        started = time.monotonic()
        await one.complete("ping", [{"role": "user", "content": 'Answer with JSON only: {"ok": true}'}], max_tokens=20, timeout_s=10)
        return "works, %.2f s" % (time.monotonic() - started)
    except AllBusy as exc:
        return "FAILED: %s" % str(exc)[:90]


async def check_livekit() -> str:
    if not os.environ.get("LIVEKIT_URL"):
        return "not set (the local server is used)"
    from livekit import api
    lk = api.LiveKitAPI()
    try:
        await lk.room.list_rooms(api.ListRoomsRequest())
        return "works"
    except Exception as exc:
        return "FAILED: %s" % type(exc).__name__
    finally:
        await lk.aclose()


async def cmd_keys() -> None:
    router = Router()
    names = list(dict.fromkeys(n for chain in router.chains.values() for n in chain))
    for name in names:
        print("%-24s %s" % (name, await check_model(router, name)))
    print("%-24s %s" % ("livekit", await check_livekit()))
    for name in ("SARVAM_API_KEY", "GLADIA_API_KEY", "DATABASE_URL"):
        print("%-24s %s" % (name, "set, checked on the first call" if os.environ.get(name) else "not set"))
    await router.close()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("what", choices=["keys", "think", "listen", "turns"])
    parser.add_argument("path", nargs="?")
    parser.add_argument("--rounds", type=int, default=10)
    a = parser.parse_args()
    if a.what == "keys":
        asyncio.run(cmd_keys())
    elif a.what == "think":
        asyncio.run(cmd_think(a.rounds))
    elif a.what == "listen":
        asyncio.run(cmd_listen(Path(a.path)))
    else:
        asyncio.run(cmd_turns(Path(a.path)))


if __name__ == "__main__":
    main()
