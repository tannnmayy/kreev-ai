"""Which language to reply in.

Telugu speakers borrow English all the time, so one English word must not flip the reply. The rule is hysteresis: only a
full sentence in the other language switches, and a customer can pin a language outright ("speak English", "తెలుగులో
మాట్లాడండి"), which holds until they ask otherwise.

Speech recognisers may also write Telugu in Latin letters, so function words are checked as well as the script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .numbers import normalize

# Function words of Telugu written in Latin letters: common in code-mixed speech, rare in English
_TE_LATIN = {
    "lo", "ki", "ni", "nu", "ga", "emi", "enti", "entha", "enta", "ela", "ekkada", "eppudu", "undi", "unnayi", "unda",
    "ledu", "kavali", "kaavali", "vaddu", "kaadu", "cheyyi", "cheyandi", "cheyyandi", "veyyi", "veyandi", "pettu",
    "pettandi", "ivvu", "ivvandi", "teeseyi", "cheppandi", "naku", "maku", "meeku", "sare", "avunu", "inka", "kani",
    "ante", "chalu", "anthe", "mottam", "mottham", "kilolu", "royyalu", "chepalu", "biyyam",
}
_HI_LATIN = {"kitna", "kitne", "chahiye", "aadha", "hai", "hain", "nahi", "nahin", "mujhe", "dedo", "dijiye", "kya", "kaise"}

_PIN_TE = re.compile(
    r"తెలుగులో|తెలుగు లో|telugu lo|telugulo|speak (?:in )?telugu|in telugu|telugu please|only telugu|switch to telugu", re.I)
_PIN_EN = re.compile(
    r"speak (?:in )?english|in english|english please|only english|switch to english|ఇంగ్లీష్లో|ఇంగ్లీషులో|ఇంగ్లీష్ లో|english lo|englishlo", re.I)
_PIN_MIX = re.compile(r"any language|whatever language|mix(?:ed)? is fine", re.I)


def classify(text: str) -> str:
    """"te", "en", "hi" or "" (too little to tell)."""
    t = normalize(text)
    words = re.findall(r"[\wఀ-౿ऀ-ॿ]+", t)
    if not words:
        return ""
    te = sum(1 for w in words if any("ఀ" <= c <= "౿" for c in w))
    hi = sum(1 for w in words if any("ऀ" <= c <= "ॿ" for c in w))
    latin = [w for w in words if w.isascii() and not w.isdigit()]
    te_latin = sum(1 for w in latin if w in _TE_LATIN)
    hi_latin = sum(1 for w in latin if w in _HI_LATIN)
    total = max(1, len([w for w in words if not w.isdigit()]))
    if hi + hi_latin and (hi + hi_latin) / total >= 0.34 and hi + hi_latin > te + te_latin:
        return "hi"
    if (te + te_latin) / total >= 0.34:
        return "te"
    if latin:
        return "en"
    return ""


@dataclass
class LanguageTracker:
    reply: str = "en"                  # the language Ala speaks now
    pinned: Optional[str] = None       # set by the customer's own request
    last_heard: str = ""               # the language of the last full sentence
    hindi_seen: bool = False           # Ala has been spoken to in Hindi at least once

    def hear(self, text: str) -> str:
        """Take in a customer's words; returns the language to reply in."""
        t = normalize(text)
        if _PIN_MIX.search(t):
            self.pinned = None
        elif _PIN_TE.search(t):
            self.pinned = self.reply = "te"
        elif _PIN_EN.search(t):
            self.pinned = self.reply = "en"
        lang = classify(t)
        words = len(re.findall(r"[\wఀ-౿]+", t))
        if lang == "hi":
            self.hindi_seen = True
        if lang in ("te", "en") and words >= 2:
            self.last_heard = lang
            if self.pinned is None:
                self.reply = lang
        elif lang == "te" and words == 1 and self.pinned is None and self.last_heard == "":
            # the very first word of a call sets the language, even if it's only "నమస్కారం"
            self.last_heard = self.reply = "te"
        return self.reply
