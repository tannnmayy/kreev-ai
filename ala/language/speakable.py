"""Text as the voice should read it, and the style contract.

Captions keep digits ("₹350"). The voice gets words ("three hundred and fifty rupees"), so an Indian voice never reads a
symbol aloud or guesses at a number. `polish` enforces KREEV's brief in code: no "Sure!", no repeated acknowledgement, no
exclamation marks, at most two short sentences. Models add these however often they're told not to.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence

from .numbers import en_number, say_money, te_number

_MONEY = re.compile(r"₹\s?([\d,]+(?:\.\d+)?)")
_RANGE = re.compile(r"(?<=\d)\s?[-–]\s?(?=\d)")
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?![\w.])")


def speakable(text: str, lang: str) -> str:
    """Replace symbols and digits with words in the reply's language."""
    def money(m: "re.Match[str]") -> str:
        return say_money(float(m.group(1).replace(",", "")), lang)

    out = _MONEY.sub(money, text or "")
    out = _RANGE.sub(" to " if lang != "te" else " నుంచి ", out)

    def number(m: "re.Match[str]") -> str:
        n = int(m.group(0).replace(",", ""))
        return te_number(n) if lang == "te" else en_number(n)

    out = _NUM.sub(number, out)
    out = re.sub(r"(?i)\bkgs?\b", "kilos" if lang != "te" else "కిలోలు", out)
    out = re.sub(r"[*#`_]+", " ", out)
    return " ".join(out.split())


# --- the style contract -------------------------------------------------------------------------------------------

_OPENERS = re.compile(
    r"^\s*(?:(?:sure|sure thing|great|great choice|great question|good choice|good question|nice choice|excellent|awesome|"
    r"perfect|wonderful|fantastic|absolutely|certainly|definitely|of course|no problem|okay|ok|alright|all right|got it|"
    r"understood|right|i see|thanks|thank you|let me check|let me see|one moment|hmm+|"
    r"తప్పకుండా|ఖచ్చితంగా|అద్భుతం|చాలా బాగుంది|బాగుంది|సరే|ఓకే|ధన్యవాదాలు)(?:\s+for that|\s+so)?\s*[,.!:;\-–—]+\s*)+",
    re.I)
_CLOSERS = re.compile(
    r"(?:is there anything else|anything else|let me know|feel free|hope that helps|happy to help|how else can i help|"
    r"ఇంకా ఏమైనా|మీకు సహాయం చేయడానికి సంతోషం)", re.I)
_SENTENCE = re.compile(r"(?<=[.?!।])\s+")


def _acknowledgement(sentence: str) -> str:
    s = sentence.strip().lower().rstrip(".!?")
    return s if len(s.split()) <= 3 else ""


def polish(text: str, recent: Sequence[str] = (), max_sentences: int = 2) -> str:
    """The reply as it will be spoken: banned openers and closers gone, no exclamation marks, short, and no opening
    that repeats one of the last few replies."""
    t = " ".join((text or "").split())
    t = _OPENERS.sub("", t)
    t = t.replace("!", ".").replace("..", ".")
    parts = [p for p in _SENTENCE.split(t) if p.strip()]
    while len(parts) > 1 and _CLOSERS.search(parts[-1]):
        parts.pop()
    if len(parts) > 1 and _acknowledgement(parts[0]) and any(
            _acknowledgement(parts[0]) == _acknowledgement(x) for r in recent for x in _SENTENCE.split(r)[:1]):
        parts = parts[1:]
    if len(parts) > max_sentences:
        # the question matters most in a short reply: keep it if it would be cut
        tail = [p for p in parts[max_sentences:] if p.rstrip().endswith("?")]
        parts = parts[:max_sentences - 1] + (tail[-1:] if tail else parts[max_sentences - 1:max_sentences])
    out = " ".join(p.strip() for p in parts).strip()
    return out[:1].upper() + out[1:] if out else ""


def violations(text: str, recent: Sequence[str] = ()) -> List[str]:
    """What breaks the style contract in a reply: used by the test suites."""
    found = []
    if _OPENERS.match(text or ""):
        found.append("banned opener")
    if "!" in (text or ""):
        found.append("exclamation mark")
    if _CLOSERS.search(text or ""):
        found.append("filler closer")
    first = _acknowledgement(_SENTENCE.split((text or "").strip())[0]) if text else ""
    if first and any(first == _acknowledgement(_SENTENCE.split(r.strip())[0]) for r in recent):
        found.append("repeated acknowledgement")
    return found
