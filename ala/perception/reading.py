"""How the customer said it: the voice read together with the words.

A Reading is advice, never an action. It can hold an action longer, ask back a value, or change how Ala speaks. An action
still needs words. Urgent and frustrated each need a cue from the voice and one from the words, so a naturally loud
speaker isn't told to calm down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..language import cues
from ..language.numbers import find_quantities, normalize, tokens
from .features import _MEASURED, Baseline, Features, raised

FILLER_PAUSE_S = 0.6        # "half a, um, ... kilo": a pause after a filler, before the value
MIDPHRASE_PAUSE_S = 1.0     # "half a ... kilo": a long pause in the middle of the phrase
HEDGE_GAP_WORDS = 3         # a hedge counts within this many words of the value

LABELS = {"urgent": "urgently, with a raised voice", "frustrated": "frustrated", "unsure": "unsure of the value",
          "raised voice": "with a raised voice"}


def hedged_values(words: str) -> List[Tuple[str, str]]:
    """(hedge, value) for each value a hedge sits next to: "around 500 grams", "రెండు కిలోలు అనుకుంటా". "I think I want
    rice" is a softened request, not an unsure value."""
    text = normalize(words)
    h = cues.hedge(text)
    if not h:
        return []
    toks = tokens(text)
    spans = [(s, e) for _, s, e in toks]
    at = text.find(h)
    hedge_tok = next((i for i, (s, e) in enumerate(spans) if s <= at < e), 0)
    hedge_end = next((i for i, (s, e) in enumerate(spans) if s < at + len(h) <= e), hedge_tok)
    out = []
    for q in find_quantities(text):
        gap = (q.start - hedge_end - 1) if q.start > hedge_end else (hedge_tok - q.end)
        if gap <= HEDGE_GAP_WORDS:
            out.append((h, " ".join(t for t, _, _ in toks[q.start:q.end])))
    return out


@dataclass
class Reading:
    urgent: bool = False            # a raised voice, with words that stop or refuse
    unsure: bool = False            # hedged or hesitant about a value
    frustrated: bool = False        # a raised voice, with words of complaint
    raised_voice: bool = False      # effort well above the speaker's usual
    hesitant: bool = False          # a long pause before the value
    cues: List[str] = field(default_factory=list)
    numbers: Dict[str, float] = field(default_factory=dict)
    values: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.urgent:
            return "urgent"
        if self.frustrated:
            return "frustrated"
        if self.unsure:
            return "unsure"
        if self.raised_voice:
            return "raised voice"
        return ""

    def note(self) -> str:
        """For the minds: how the customer sounded, and what to do about it in the reply. Advisory, it decides no action."""
        advice = {"urgent": "Keep your reply very short and calm.",
                  "frustrated": "Say sorry once, in a few words, then fix it. No cheerfulness."}.get(self.label)
        if not advice:
            return ""
        return "The customer sounded %s (%s). %s" % (LABELS[self.label], "; ".join(self.cues), advice)

    def event(self) -> Dict[str, object]:
        return {"label": self.label, "cues": "; ".join(self.cues), "numbers": {k: round(v, 2) for k, v in self.numbers.items()}}


def read(segments: List[Features], words: str) -> Reading:
    """Read a request's measured segments together with its words."""
    r = Reading()
    segs = [s for s in segments if s is not None]
    w = " ".join((words or "").split())
    last_words = next((s.words for s in reversed(segs) if s.words), "") or w
    best: List[str] = []
    for s in segs:
        found = s.raised()
        if len(found) > len(best):
            best = found
            r.numbers.update({"effort_" + k: v for k, v in s.delta.items()})
    r.raised_voice = bool(best)
    stop = cues.urgent_words(last_words)
    if r.raised_voice and stop:
        r.urgent = True
        r.cues += ["raised voice: " + ", ".join(best), '"%s"' % stop]
    complaint = cues.frustrated(w)
    if complaint and (r.raised_voice or r.urgent or len(segs) == 0):
        r.frustrated = not r.urgent
        if r.frustrated:
            r.cues += (["raised voice: " + ", ".join(best)] if best else []) + ['"%s"' % complaint]
    elif complaint and not r.urgent:
        # the words alone are worth a calmer reply, but the voice gets no credit for it
        r.frustrated = True
        r.cues.append('"%s"' % complaint)
    if find_quantities(w):
        _read_unsure(r, segs, w)
    if r.raised_voice and not (r.urgent or r.frustrated):
        r.cues.append("raised voice: " + ", ".join(best))
    return r


def _read_unsure(r: Reading, segs: List[Features], w: str) -> None:
    """Unsure of a value: a hedge next to it, or a long pause before it, after a filler or mid-phrase."""
    hedges = hedged_values(w)
    r.values = [v for _, v in hedges]
    pause, before = 0.0, ""
    valued = [i for i, s in enumerate(segs) if find_quantities(s.words or "")]
    if valued and valued[-1] > 0:
        i = valued[-1]
        pause, before = max(0.0, segs[i].on - segs[i - 1].off), segs[i - 1].words or ""
    if valued:
        pause = max(pause, segs[valued[-1]].longest_pause_s)
    if pause:
        r.numbers["pause_s"] = pause
    filler = cues.filler(w)
    midphrase = bool(before) and cues.unfinished(before)
    if (filler and pause >= FILLER_PAUSE_S) or (midphrase and pause >= MIDPHRASE_PAUSE_S):
        r.hesitant = True
    if hedges:
        r.unsure = True
        r.cues.append('"%s"' % hedges[0][0])
    if r.hesitant:
        r.unsure = True
        r.cues.append("paused %.1f s before the value" % pause + (' after "%s"' % filler if filler else ""))


class Listener:
    """The tone channel of one conversation: the speaker's baseline, and the segments of the open request.

    A segment gets its place the moment it ends (`expect`), and its measurements fill it a moment later (`add`). Its words
    come a little after it ended (`heard`): they belong to the latest segment still without words that ended at least
    ASR_MIN_S before they arrived, which keeps them in order in quick bursts and off a noise burst that never gets words."""

    ASR_MIN_S = 0.12

    def __init__(self) -> None:
        self.baseline = Baseline()
        self.segments: List[Features] = []
        self.last: Optional[Reading] = None

    def expect(self, ended: float) -> Features:
        slot = Features(ended=ended, measured=False)
        self.segments.append(slot)
        return slot

    def add(self, f: Optional[Features], into: Optional[Features] = None) -> Optional[Features]:
        if into is not None:
            into.measured = True
            if f is None:
                return None
            for name in _MEASURED:
                setattr(into, name, getattr(f, name))
            f = into
        elif f is None:
            return None
        else:
            f.ended = f.ended or f.off
            self.segments.append(f)
        f.delta = self.baseline.effort(f)
        if not self.baseline.ready() or not raised(f.delta):
            self.baseline.add(f)
        return f

    def heard(self, text: str, at: float) -> None:
        if not self.segments:
            return
        waiting = [s for s in self.segments if not s.worded and s.ended <= at - self.ASR_MIN_S]
        s = waiting[-1] if waiting else self.segments[-1]
        s.words = " ".join((s.words + " " + (text or "")).split())
        s.worded = True

    def awaiting_words(self) -> bool:
        """The latest segment was a raised voice whose words haven't arrived: actions wait for them."""
        s = self.segments[-1] if self.segments else None
        return s is not None and s.measured and not s.worded and bool(s.raised())

    def reading(self, words: str) -> Reading:
        self.last = read([s for s in self.segments if s.measured], words)
        return self.last

    def close(self, before: float) -> None:
        """A request was answered: the segments that ended before its reply began are done with."""
        self.segments = [s for s in self.segments if s.ended > before]
