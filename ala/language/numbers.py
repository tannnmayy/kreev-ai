"""Numbers, quantities and money, heard and said, in Telugu, English and a little Hindi.

Two jobs:
- hearing: turn "అర కిలో", "two and a half kilos", "500 grams" or "పన్నెండు వందలు" into values;
- saying: turn 350 into "మూడు వందల యాభై రూపాయలు", and 0.5 kg into "అర కిలో".

Prices and quantities are the words a shopping assistant can least afford to get wrong, so this is plain
code with tests, not a model. Telugu wording here is reviewed by a native speaker before submission.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import List, Optional, Tuple

# --- reading ------------------------------------------------------------------------------------------------

_SMALL = {
    # English
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    # Telugu
    "ఒకటి": 1, "ఒక": 1, "ఓ": 1, "రెండు": 2, "మూడు": 3, "నాలుగు": 4, "ఐదు": 5, "అయిదు": 5, "ఆరు": 6, "ఏడు": 7,
    "ఎనిమిది": 8, "తొమ్మిది": 9, "పది": 10, "పదకొండు": 11, "పన్నెండు": 12, "పదమూడు": 13, "పద్నాలుగు": 14,
    "పదిహేను": 15, "పదహారు": 16, "పదిహేడు": 17, "పద్దెనిమిది": 18, "పంతొమ్మిది": 19, "ఇరవై": 20, "ముప్పై": 30,
    "ముప్ఫై": 30, "నలభై": 40, "యాభై": 50, "అరవై": 60, "డెబ్బై": 70, "ఎనభై": 80, "తొంభై": 90,
    # Telugu in Latin letters, as speech recognisers sometimes write it
    "oka": 1, "okati": 1, "rendu": 2, "moodu": 3, "mudu": 3, "nalugu": 4, "naalugu": 4, "aidu": 5, "aidhu": 5,
    "aaru": 6, "edu": 7, "yedu": 7, "enimidi": 8, "tommidi": 9, "padi": 10, "iravai": 20, "muppai": 30,
    "nalabhai": 40, "yabhai": 50,
    # Hindi in Latin letters and in Devanagari, for the quantities a Telugu speaker may borrow
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8,
    "nau": 9, "das": 10, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
}
_HUNDRED = {"hundred", "hundreds", "వంద", "వందలు", "వందల", "నూరు", "నూట", "vanda", "vandalu", "vandala", "sau"}
_THOUSAND = {"thousand", "thousands", "వెయ్యి", "వేయి", "వేలు", "వేల", "veyyi", "velu", "vela", "hazaar", "hazar"}
_FRACTION = {
    "half": 0.5, "quarter": 0.25, "అర": 0.5, "సగం": 0.5, "పావు": 0.25, "ముప్పావు": 0.75, "ముక్కాలు": 0.75,
    "ara": 0.5, "sagam": 0.5, "pavu": 0.25, "paavu": 0.25, "muppavu": 0.75, "muppaavu": 0.75,
    "aadha": 0.5, "adha": 0.5, "aadhi": 0.5, "pav": 0.25, "paav": 0.25, "आधा": 0.5, "पाव": 0.25,
    "dedh": 1.5, "dhai": 2.5, "ढाई": 2.5, "डेढ़": 1.5,
}
# "ఒకటిన్నర", "రెండున్నర": n and a half, in one word
_AND_A_HALF = {
    "ఒకటిన్నర": 1.5, "రెండున్నర": 2.5, "మూడున్నర": 3.5, "నాలుగున్నర": 4.5, "ఐదున్నర": 5.5, "ఆరున్నర": 6.5,
    "okatinnara": 1.5, "rendunnara": 2.5, "moodunnara": 3.5, "mudunnara": 3.5, "nalugunnara": 4.5,
}
_UNITS = {
    "kg": {"kg", "kgs", "kilo", "kilos", "kilogram", "kilograms", "కిలో", "కిలోలు", "కిలోల", "కేజీ", "కేజీలు", "కేజీల",
           "kilolu", "kilola", "किलो"},
    "g": {"g", "gm", "gms", "gram", "grams", "గ్రాము", "గ్రాములు", "గ్రాముల", "gramulu"},
    "litre": {"l", "ltr", "litre", "litres", "liter", "liters", "లీటరు", "లీటర్", "లీటర్లు", "లీటర్ల", "leetaru"},
    "ml": {"ml", "మిల్లీ"},
    "dozen": {"dozen", "dozens", "డజను", "డజన్", "డజన్లు", "డజన్ల", "dazanu"},
    "piece": {"piece", "pieces", "pc", "pcs", "ముక్క", "ముక్కలు", "mukka", "mukkalu"},
    "pack": {"pack", "packs", "packet", "packets", "ప్యాకెట్", "ప్యాకెట్లు", "ప్యాకెట్ల", "paketlu"},
    "bunch": {"bunch", "bunches", "కట్ట", "కట్టలు", "katta", "kattalu"},
}
_UNIT_OF = {word: unit for unit, words in _UNITS.items() for word in words}
_ARTICLES = {"a", "an"}

_TOKEN = re.compile(r"[₹]|\d[\d,]*(?:\.\d+)?|[^\W\d_]+(?:[ఀ-౿ऀ-ॿ]*)", re.UNICODE)


def normalize(text: str) -> str:
    """One spelling for what looks the same: NFC, joiners removed, lower case, single spaces."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("‌", "").replace("‍", "")
    return " ".join(text.lower().split())


def tokens(text: str) -> List[Tuple[str, int, int]]:
    """(token, start, end), with a digit split from a unit glued to it ("500g" -> "500", "g")."""
    out = []
    for m in _TOKEN.finditer(normalize(text)):
        out.append((m.group(0), m.start(), m.end()))
    return out


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: Optional[str]    # kg, g, litre, ml, dozen, piece, pack, bunch; None: a bare number ("make it two")
    start: int             # token index where it starts
    end: int               # token index after it
    char_start: int = 0    # the same span in the normalized text
    char_end: int = 0

    def in_base(self) -> Tuple[float, Optional[str]]:
        """Grams become kilos and millilitres become litres."""
        if self.unit == "g":
            return self.value / 1000.0, "kg"
        if self.unit == "ml":
            return self.value / 1000.0, "litre"
        return self.value, self.unit


def _is_digits(tok: str) -> bool:
    return bool(re.fullmatch(r"\d[\d,]*(?:\.\d+)?", tok))


def _adds_to(current: float, value: int) -> bool:
    """Do these two number words make one number? "twenty five" and "two hundred fifty" do; "two three" doesn't."""
    if current % 100 == 0:
        return value < 100
    return current >= 20 and current % 10 == 0 and value < 10


def _read_number(words: List[str], i: int) -> Tuple[Optional[float], int]:
    """A whole number starting at words[i]: (value, index after it), or (None, i)."""
    n = len(words)
    total, current, seen, digits = 0.0, 0.0, False, False
    j = i
    while j < n:
        w = words[j]
        if _is_digits(w):
            if seen:
                break
            current, seen, digits = float(w.replace(",", "")), True, True
        elif w in _SMALL:
            # "1200 one kilo" is two numbers: digits are never continued by words
            if digits or (current and not _adds_to(current, _SMALL[w])):
                break
            current, seen = current + _SMALL[w], True
        elif w in _HUNDRED:
            if current >= 100:
                break
            # "పన్నెండు వందలు" is 1,200; "వెయ్యి రెండు వందలు" is 1,000 + 200; "నూట యాభై" is 150
            current, seen = (current or 1) * 100, True
        elif w in _THOUSAND:
            total, current, seen = total + (current or 1) * 1000, 0.0, True
        elif w == "and" and seen and j + 1 < n and (words[j + 1] in _SMALL) and (total or current >= 100):
            j += 1      # "three hundred and fifty"
            continue
        else:
            break
        j += 1
    if not seen:
        return None, i
    return total + current, j


def find_quantities(text: str) -> List[Quantity]:
    """Every quantity in the text, in order. Words like "half", "అర" and "dozen" count; a bare number counts too."""
    toks = tokens(text)
    words = [t for t, _, _ in toks]
    i, out = 0, []
    n = len(words)
    while i < n:
        w = words[i]
        # a fraction or "n and a half" in one word: "అర కిలో", "రెండున్నర", "half a dozen"
        if w in _AND_A_HALF or w in _FRACTION:
            value = _AND_A_HALF.get(w, _FRACTION.get(w, 0.0))
            j = i + 1
            if j < n and words[j] in ("of",):
                j += 1
            if j < n and words[j] in _ARTICLES:
                j += 1
            unit = _UNIT_OF.get(words[j]) if j < n else None
            if unit is not None:
                out.append(Quantity(value, unit, i, j + 1))
                i = j + 1
                continue
            if value >= 1:          # "రెండున్నర" alone
                out.append(Quantity(value, None, i, i + 1))
                i += 1
                continue
            out.append(Quantity(value, None, i, i + 1))
            i += 1
            continue
        # "three quarters of a kilo"
        if w == "three" and i + 1 < n and words[i + 1] in ("quarter", "quarters"):
            j = i + 2
            while j < n and words[j] in ("of", "a", "an"):
                j += 1
            unit = _UNIT_OF.get(words[j]) if j < n else None
            out.append(Quantity(0.75, unit, i, (j + 1) if unit else i + 2))
            i = (j + 1) if unit else i + 2
            continue
        # "a kilo", "an extra"? only a unit counts after an article
        if w in _ARTICLES and i + 1 < n and words[i + 1] in _UNIT_OF:
            j = i + 2
            value = 1.0
            # "a kilo and a half"
            if j + 2 < n and words[j] == "and" and words[j + 1] in _ARTICLES and words[j + 2] == "half":
                value, j = 1.5, j + 3
            out.append(Quantity(value, _UNIT_OF[words[i + 1]], i, j))
            i = j
            continue
        value, j = _read_number(words, i)
        if value is not None:
            # "one and a half", "two and half"
            if j + 1 < n and words[j] == "and" and words[j + 1] == "half":
                value, j = value + 0.5, j + 2
            elif j + 2 < n and words[j] == "and" and words[j + 1] in _ARTICLES and words[j + 2] == "half":
                value, j = value + 0.5, j + 3
            unit = _UNIT_OF.get(words[j]) if j < n else None
            if unit is not None:
                j += 1
                if j + 2 < n and words[j] == "and" and words[j + 1] in _ARTICLES and words[j + 2] == "half":
                    value, j = value + 0.5, j + 3
            out.append(Quantity(value, unit, i, j))
            i = j
            continue
        # a unit alone: "కిలో రొయ్యలు" is one kilo of prawns
        if w in _UNIT_OF and w not in ("g", "l", "ml"):
            out.append(Quantity(1.0, _UNIT_OF[w], i, i + 1))
        i += 1
    return [replace(q, char_start=toks[q.start][1], char_end=toks[q.end - 1][2]) for q in out]


_MONEY_MARK = {"₹", "rs", "rupee", "rupees", "రూపాయలు", "రూపాయల", "రూపాయి", "rupayalu", "rupaayalu", "inr"}


def find_money(text: str) -> Optional[int]:
    """The first amount of money in the text: marked ("₹900", "900 rupees", "తొమ్మిది వందల రూపాయలు") if any is,
    otherwise the first bare number of at least 50 with no unit ("under 1200", "budget 900")."""
    words = [t for t, _, _ in tokens(text)]
    bare: Optional[int] = None
    i = 0
    while i < len(words):
        value, j = _read_number(words, i)
        if value is None:
            i += 1
            continue
        before = words[i - 1] if i > 0 else ""
        after = words[j] if j < len(words) else ""
        if before in _MONEY_MARK or after in _MONEY_MARK:
            return int(value)
        if bare is None and value >= 50 and after not in _UNIT_OF:
            bare = int(value)
        i = j
    return bare


# --- saying -------------------------------------------------------------------------------------------------

_TE_ONES = ["", "ఒకటి", "రెండు", "మూడు", "నాలుగు", "ఐదు", "ఆరు", "ఏడు", "ఎనిమిది", "తొమ్మిది"]
_TE_TEENS = ["పది", "పదకొండు", "పన్నెండు", "పదమూడు", "పద్నాలుగు", "పదిహేను", "పదహారు", "పదిహేడు", "పద్దెనిమిది",
             "పంతొమ్మిది"]
_TE_TENS = ["", "", "ఇరవై", "ముప్పై", "నలభై", "యాభై", "అరవై", "డెబ్బై", "ఎనభై", "తొంభై"]
_EN_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
            "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _te_below_100(n: int) -> str:
    if n < 10:
        return _TE_ONES[n]
    if n < 20:
        return _TE_TEENS[n - 10]
    return " ".join(x for x in (_TE_TENS[n // 10], _TE_ONES[n % 10]) if x)


def te_number(n: int, before_noun: bool = False) -> str:
    """Telugu number words. `before_noun`: the form used before a noun, "మూడు వందల రూపాయలు", not "వందలు"."""
    n = int(n)
    if n == 0:
        return "సున్నా"
    parts: List[str] = []
    thousands, rest = divmod(n, 1000)
    hundreds, below = divmod(rest, 100)
    if thousands:
        if thousands == 1:
            parts.append("వెయ్యి")
        else:
            last = not rest
            parts.append(te_number(thousands) + (" వేలు" if last and not before_noun else " వేల"))
    if hundreds:
        if hundreds == 1:
            parts.append("నూట" if below else "వంద")
        else:
            last = not below
            parts.append(_TE_ONES[hundreds] + (" వందలు" if last and not before_noun else " వందల"))
    if below:
        parts.append(_te_below_100(below))
    return " ".join(parts)


def en_number(n: int) -> str:
    n = int(n)
    if n == 0:
        return "zero"
    parts: List[str] = []
    thousands, rest = divmod(n, 1000)
    hundreds, below = divmod(rest, 100)
    if thousands:
        parts.append(en_number(thousands) + " thousand")
    if hundreds:
        parts.append(_EN_ONES[hundreds] + " hundred")
    if below:
        words = _EN_ONES[below] if below < 20 else " ".join(x for x in (_EN_TENS[below // 10], _EN_ONES[below % 10]) if x)
        parts.append(("and " if parts else "") + words)
    return " ".join(parts)


def say_money(amount: float, lang: str) -> str:
    """Rupees in words, for the voice."""
    n = int(round(amount))
    if lang == "te":
        if n == 1:
            return "ఒక రూపాయి"
        return te_number(n, before_noun=True) + " రూపాయలు"
    return en_number(n) + (" rupee" if n == 1 else " rupees")


def show_money(amount: float) -> str:
    """Rupees in digits, for the screen, with Indian digit grouping: ₹1,20,000."""
    n = int(round(amount))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return ("-" if n < 0 else "") + "₹" + s


_TE_HALF_FORMS = {1: "ఒకటిన్నర", 2: "రెండున్నర", 3: "మూడున్నర", 4: "నాలుగున్నర", 5: "ఐదున్నర", 6: "ఆరున్నర",
                  7: "ఏడున్నర", 8: "ఎనిమిదిన్నర", 9: "తొమ్మిదిన్నర", 10: "పదిన్నర"}
_TE_UNIT = {"kg": ("కిలో", "కిలోలు"), "litre": ("లీటరు", "లీటర్లు"), "dozen": ("డజను", "డజన్లు"),
            "pack": ("ప్యాకెట్", "ప్యాకెట్లు"), "bunch": ("కట్ట", "కట్టలు"), "piece": ("", "")}
_EN_UNIT = {"kg": ("kilo", "kilos"), "litre": ("litre", "litres"), "dozen": ("dozen", "dozen"),
            "pack": ("pack", "packs"), "bunch": ("bunch", "bunches"), "piece": ("piece", "pieces")}


def _whole(value: float) -> bool:
    return abs(value - round(value)) < 1e-6


def say_quantity(value: float, unit: str, lang: str) -> str:
    """A quantity in words. Fractions are said the way people say them: "అర కిలో", "half a kilo"."""
    frac = round(value - int(value), 3)
    whole = int(value)
    if lang == "te":
        one, many = _TE_UNIT.get(unit, ("", ""))
        if whole == 0 and frac in (0.25, 0.5, 0.75):
            word = {0.25: "పావు", 0.5: "అర", 0.75: "ముప్పావు"}[frac]
            return (word + " " + one).strip()
        if frac == 0:
            if whole == 1:
                return ("ఒక " + one).strip() if one else "ఒకటి"
            return (te_number(whole) + " " + many).strip()
        if frac == 0.5 and whole in _TE_HALF_FORMS:
            return (_TE_HALF_FORMS[whole] + " " + (many if whole > 1 else one)).strip()
        if frac in (0.25, 0.75):
            word = {0.25: "పావు", 0.75: "ముప్పావు"}[frac]
            return "%s %s %s" % (te_number(whole), one if whole == 1 else many, word)
        return "%s %s" % (("%g" % value), many or one)
    one, many = _EN_UNIT.get(unit, ("", ""))
    if whole == 0 and frac == 0.5:
        return "half a " + one
    if whole == 0 and frac == 0.25:
        return "a quarter " + one
    if whole == 0 and frac == 0.75:
        return "three quarters of a " + one
    if frac == 0:
        return "%s %s" % (en_number(whole), one if whole == 1 else many)
    if frac == 0.5:
        return "%s and a half %s" % (en_number(whole), many)
    return "%g %s" % (value, many)


def show_quantity(value: float, unit: str) -> str:
    """A quantity for the screen: "0.5 kg", "1 dozen", "2 pcs"."""
    label = {"kg": "kg", "litre": "L", "dozen": "dozen", "pack": "pack", "bunch": "bunch", "piece": "pc"}.get(unit, unit)
    if unit == "pack" and value != 1:
        label = "packs"
    if unit == "bunch" and value != 1:
        label = "bunches"
    if unit == "piece" and value != 1:
        label = "pcs"
    return "%g %s" % (value, label)
