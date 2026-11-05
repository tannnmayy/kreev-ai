"""Decisions 2 and 3: talked over, should Ala stop? And was that said to Ala at all?

Six kinds of overlap, not one. LiveKit's acoustic models see only the sound; this adds the words and the state, which is
where Telugu backchannels, side conversations and Ala's own echo can be told apart. Everything here runs on a transcript
that has just arrived, before LiveKit treats it as a turn, so an ignored utterance never reaches the minds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from ..language import cues
from ..language.numbers import find_quantities, normalize

ECHO_SIMILARITY = 82        # how closely a transcript must match what Ala is saying to be its own voice
QUIET_BELOW_USUAL_DB = 9.0  # this much quieter than the customer's usual: probably someone else, or across the room


@dataclass(frozen=True)
class Verdict:
    kind: str        # interruption | backchannel | side_talk | background | echo | noise | speech
    stop: bool       # should Ala stop talking?
    reply: bool      # should this reach the minds as a turn?
    reason: str


def shop_talk(words: str, catalog=None) -> bool:
    """Does it sound like shopping? Then it's for Ala, however quietly it was said."""
    if cues.is_yes(words) or cues.is_no(words) or cues.control(words) in ("stop", "repeat", "resume"):
        return True
    if cues.asks_cart(words) or cues.asks_total(words) or cues.wants_checkout(words):
        return True
    # a measure is shopping, and so is a bare number on its own; a number inside a sentence ("the news at nine") says nothing
    quantities = find_quantities(words)
    if any(q.unit for q in quantities) or (quantities and len(words.split()) <= 3):
        return True
    if cues.wants_add(words) or cues.wants_remove(words):
        return True
    return bool(catalog is not None and catalog.mentions(words))


def is_echo(words: str, said_now: str) -> bool:
    """Did Ala just hear itself? A laptop speaker can leak a sentence into the microphone."""
    w, s = normalize(words), normalize(said_now)
    if not w or not s or len(w.split()) < 2:
        return False
    return fuzz.partial_ratio(w, s) >= ECHO_SIMILARITY


def classify(words: str, *, ala_speaking: bool, said_now: str = "", raised_voice: bool = False,
             level_vs_usual: Optional[float] = None, catalog=None) -> Verdict:
    """What this utterance is. `level_vs_usual`: its loudness minus the speaker's usual, in dB, when measured."""
    t = (words or "").strip()
    if not t:
        return Verdict("noise", False, False, "no words")
    if ala_speaking and is_echo(t, said_now):
        return Verdict("echo", False, False, "the words match what Ala is saying")
    kind = cues.control(t)
    if kind == "stop":
        return Verdict("interruption", True, True, "a stop word")
    if ala_speaking and kind == "backchannel":
        if raised_voice:
            return Verdict("interruption", True, True, "a raised backchannel means stop")
        return Verdict("backchannel", False, False, "the customer is only listening")
    if cues.addressed_elsewhere(t) and not (catalog is not None and cues.wants_add(t) and catalog.mentions(t) and
                                            not cues.is_question(t)):
        return Verdict("side_talk", ala_speaking, False, "the customer is calling someone else")
    if level_vs_usual is not None and level_vs_usual <= -QUIET_BELOW_USUAL_DB and not shop_talk(t, catalog):
        return Verdict("background", False, False, "quiet, and nothing about shopping")
    if ala_speaking:
        return Verdict("interruption", True, True, "words over Ala")
    return Verdict("speech", False, True, "an ordinary turn")
