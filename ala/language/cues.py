"""The words that carry turn-taking meaning, in Telugu, English and Latin-letter Telugu.

These are the semantic half of the perception layer. Each list answers one question about what was just said: is it a
stop, a backchannel, a yes, an unfinished sentence, a correction, a hedge, something said to someone else in the room?

Whole-utterance patterns (control words, yes and no) must match everything said, so "yes, but make it two" is not a yes
and "okay, add the rice" is not a backchannel. Telugu entries are drafts for native review.

Python's `\\w` doesn't count Telugu vowel signs as word characters, so patterns here use `_WB`-style lookarounds over the
Telugu block rather than `\\b`.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .numbers import normalize

_L = r"(?<![\wఀ-౿])"      # the start of a word, Telugu included
_R = r"(?![\wఀ-౿])"       # the end of a word, Telugu included
_EDGE = r"[\s\.,!\?…'\"“”\-–—:;]*"  # what may surround a whole-utterance match


def _alts(words: str) -> str:
    """"a|b c" with spaces tolerant of extra punctuation between words."""
    return words.replace("‌", "").replace("‍", "").replace(" ", r"[\s,]+")


def _whole(words: str) -> "re.Pattern[str]":
    return re.compile("^" + _EDGE + "(?:" + _alts(words) + ")" + _EDGE + "$", re.I)


def _anywhere(words: str) -> "re.Pattern[str]":
    return re.compile(_L + "(?:" + _alts(words) + ")" + _R, re.I)


# --- control words: the whole utterance is one of these -------------------------------------------------------
_STOP = _whole(
    "stop|stop it|stop talking|wait|wait wait|wait a (?:sec|second|minute|moment)|hold on|hang on|one (?:sec|second|minute|moment)|"
    "just a (?:sec|second|minute|moment)|quiet|enough|shut up|please stop|"
    "ఆగండి|ఆగు|ఆగండి ఆగండి|ఆపండి|ఆపు|ఆపేయండి|ఒక్క నిమిషం|ఒక నిమిషం|ఉండండి|ఉండు|చాలు ఆపండి|"
    "aagandi|agandi|aagu|apandi|aapandi|okka nimisham|undandi|ruko|ruk")
_RESUME = _whole(
    "go on|continue|carry on|keep going|please continue|you were saying|what were you saying|yes go on|go ahead and finish|"
    "చెప్పండి|చెప్పు|కానివ్వండి|ఆ తర్వాత|తర్వాత|ఆ తర్వాత చెప్పండి|ఏం చెప్తున్నారు|cheppandi|kanivvandi|aa tarvata")
_REPEAT = _whole(
    "what|what\\?|sorry|sorry what|huh|pardon|pardon me|come again|say that again|say it again|repeat|repeat that|repeat it|"
    "can you repeat(?: that| it)?|could you repeat(?: that| it)?|what did you (?:just )?say|i didn'?t (?:get|catch) that|once more|one more time|"
    "మళ్ళీ చెప్పండి|మళ్లీ చెప్పండి|మళ్ళీ చెప్పు|మళ్లీ చెప్పు|ఏమన్నారు|ఏంటి|ఏమిటి|అర్థం కాలేదు|వినిపించలేదు|"
    "malli cheppandi|emannaru|enti|artham kaledu")
# "Yes" and "sure" are left out: they can say yes to a question. Only state decides that.
_BACKCHANNEL = _whole(
    "(?:(?:m+-?h?m+|mhm+|hmm+|uh-?huh|okay|ok|right|yeah|yep|i see|got it|alright|cool|fine|"
    "ఆ+|ఊ+|హా+|హ్మ్+|హూ+|ఓకే|అలాగే|సరే|ఓహో|అవునా|aa+|oo+|haa+|sare|alage)[\\s,\\.]*){1,3}")


def control(text: str) -> Optional[str]:
    """"stop", "resume", "repeat" or "backchannel" when the whole utterance is one of those, else None."""
    t = normalize(text)
    if not t:
        return None
    if _STOP.match(t):
        return "stop"
    if _RESUME.match(t):
        return "resume"
    if _REPEAT.match(t):
        return "repeat"
    if _BACKCHANNEL.match(t):
        return "backchannel"
    return None


# --- yes and no: only a plain one counts ----------------------------------------------------------------------
_YES = _whole(
    "(?:yes|yeah|yep|yup|yes please|sure|ok|okay|okay yes|correct|right|that'?s right|confirm|confirmed|go ahead|do it|"
    "place it|place the order|yes place it|yes place the order|yes go ahead|please do|absolutely|of course|fine|alright|"
    "అవును|అవునండి|ఆ అవును|సరే|సరేనండి|ఓకే|అలాగే|కానివ్వండి|పెట్టండి|పెట్టు|ఆర్డర్ పెట్టండి|ఆర్డర్ పెట్టు|చేయండి|చెయ్యండి|వేయండి|వెయ్యండి|"
    "ఆ|హా|అవును పెట్టండి|సరే పెట్టండి|avunu|sare|ok sare|alage|pettandi|order pettandi|haan|ha|ji haan|theek hai)")
_NO = _whole(
    "(?:no|nope|nah|no thanks|no thank you|not now|not yet|don'?t|do not|cancel|cancel it|never mind|nevermind|leave it|"
    "వద్దు|వద్దండి|కాదు|కాదండి|లేదు|లేదండి|వద్దు వద్దు|కాదు కాదు|ఇప్పుడు వద్దు|vaddu|vaddandi|kaadu|ledu|nahi|nahin)")


def is_yes(text: str) -> bool:
    return bool(_YES.match(normalize(text)))


def is_no(text: str) -> bool:
    return bool(_NO.match(normalize(text)))


# --- is the sentence finished? --------------------------------------------------------------------------------
# Words that leave a sentence open. These only ever make Ala wait longer.
_DANGLING_TAIL = re.compile(
    _L + r"(?:and|or|but|so|then|also|because|with|to|for|of|the|a|an|um+|uh+|er+|erm|hmm+|like|let me (?:think|see|check)|"
    r"i mean|i want|i need|give me|maybe|"
    r"మరియు|ఇంకా|కానీ|అంటే|ఏంటంటే|తర్వాత|ఆ\.\.\.|అది|అదీ|ఆ+|ఊ+|ఏమో|లేదా|లేకపోతే|"
    r"inka|kani|ante|mariyu|leda)" + _EDGE + "$", re.I)
_TRAILING_MARK = re.compile(r"(?:,|\.\.\.|…|-|–)\s*$")
# A quantity with no item after it: "అర కిలో...", "half a kilo of"
_QUANTITY_TAIL = re.compile(
    _L + r"(?:kilo|kilos|kg|kgs|grams|gm|litre|litres|dozen|pieces|packets?|కిలో|కిలోలు|కిలోల|గ్రాములు|లీటరు|డజను|ప్యాకెట్)" + _EDGE + "$", re.I)
_REVISING = _anywhere(
    "no wait|wait no|no no|actually|i mean|scratch that|changed? my mind|on second thought|instead|make (?:it|that)|rather|"
    "correction|sorry i meant|sorry|not that|"
    "కాదు కాదు|కాదు|అంటే|అది కాదు|వద్దు వద్దు|సారీ|మార్చు|మార్చండి|బదులు|"
    "kaadu kaadu|kaadu|adi kaadu|badulu")
_FINISHED = _anywhere(
    "that'?s all|that is all|that'?s it|that will do|nothing else|done|thats all|"
    "అంతే|చాలు|అంతే చాలు|ఇక చాలు|ఇంకేమీ వద్దు|ఇంకేం వద్దు|anthe|chalu")
_FILLER = _anywhere("uh+|um+|er+|erm|hmm+|uhm|ఆ+|అదీ|ఏంటంటే|ఏమంటే")
_HEDGE = _anywhere(
    "i think|i guess|i suppose|maybe|probably|perhaps|not sure|i'?m not sure|or so|kind of|sort of|i don'?t know|roughly|around|"
    "i believe|about|"
    "అనుకుంటా|అనుకుంటాను|ఏమో|సుమారు|దాదాపు|తెలియదు|బహుశా|anukunta|emo|sumaru|dadapu")
_QUESTION = _anywhere(
    "what|which|how|when|where|why|who|is|are|do|does|did|can|could|will|would|should|"
    "ఏంటి|ఏమిటి|ఎంత|ఎన్ని|ఎప్పుడు|ఎక్కడ|ఎందుకు|ఎలా|ఏది|ఎవరు|ఉందా|ఉన్నాయా|ఇస్తారా|వస్తుందా|అవుతుందా|"
    "enta|entha|ela|eppudu|ekkada|enduku|unda|unnaya")
_QUESTION_TAIL = re.compile(r"[ఀ-౿]+(?:దా|నా|లా)\?*$|\?\s*$")


def unfinished(text: str) -> bool:
    """Do the last words leave the sentence open?"""
    t = normalize(text)
    if not t:
        return False
    return bool(_DANGLING_TAIL.search(t) or _TRAILING_MARK.search(t) or _QUANTITY_TAIL.search(t))


def revising(text: str) -> bool:
    """Is the speaker correcting themselves?"""
    return bool(_REVISING.search(normalize(text)))


def finished_marker(text: str) -> bool:
    """"That's all", "అంతే". Noted, but it never shortens a wait on its own."""
    return bool(_FINISHED.search(normalize(text)))


def filler(text: str) -> Optional[str]:
    m = _FILLER.search(normalize(text))
    return m.group(0) if m else None


def hedge(text: str) -> Optional[str]:
    m = _HEDGE.search(normalize(text))
    return m.group(0) if m else None


def is_question(text: str) -> bool:
    t = normalize(text)
    if "?" in t:
        return True
    first = t.split(" ", 1)[0] if t else ""
    return bool(_QUESTION.fullmatch(first)) or bool(_QUESTION_TAIL.search(t)) or bool(
        re.search(_L + r"(?:ఎంత|ఏంటి|ఎన్ని|ఎప్పుడు|ఎలా|ఉందా|ఉన్నాయా|enta|entha|how much|how many)" + _R, t))


# --- was it said to Ala? --------------------------------------------------------------------------------------
_CALLING_OTHERS = re.compile(
    "^" + _EDGE + r"(?:అమ్మా|నాన్నా|ఏమండీ|ఒరేయ్|ఓయ్|బాబూ|బాబు|అక్కా|అన్నా|తమ్ముడూ|చెల్లీ|ammaa?|nannaa?|emandi|orey|oye|babu|akka|anna|"
    r"mom|mum|dad|honey|dear|hey (?:mom|mum|dad|honey|dear|beta))" + _R, re.I)


def addressed_elsewhere(text: str) -> bool:
    """Does the utterance open by calling someone other than Ala?"""
    return bool(_CALLING_OTHERS.search(normalize(text)))


# --- how they sound, from the words ---------------------------------------------------------------------------
_FRUSTRATED = _anywhere(
    "i said|i told you|i already said|not again|still wrong|that'?s wrong|you got it wrong|wrong|listen|useless|stupid|"
    "how many times|come on|seriously|are you (?:deaf|listening)|"
    "ఎన్నిసార్లు చెప్పాలి|ఎన్ని సార్లు చెప్పాలి|చెప్పాను కదా|అన్నాను కదా|తప్పు|వినండి|వినట్లేదా|మళ్ళీనా|మళ్లీనా")
_URGENT_WORDS = _anywhere(
    "no|nope|stop|wait|don'?t|do not|cancel|hold on|enough|leave it|never ?mind|ఆగండి|ఆపండి|వద్దు|కాదు|ఆగు|ఆపు")


def frustrated(text: str) -> Optional[str]:
    m = _FRUSTRATED.search(normalize(text))
    return m.group(0) if m else None


def urgent_words(text: str) -> Optional[str]:
    m = _URGENT_WORDS.search(normalize(text))
    return m.group(0) if m else None


# --- shopping intents worth catching in code ------------------------------------------------------------------
_CHECKOUT = _anywhere(
    "check ?out|place (?:the |my |an )?order|place it|order (?:it|this|these|now)|that'?s (?:all|it) place|finish (?:the )?order|buy (?:it|this|these)|"
    "ఆర్డర్ పెట్టు|ఆర్డర్ పెట్టండి|ఆర్డర్ చేయండి|ఆర్డర్ చెయ్యండి|ఆర్డర్ చేయి|order pettu|order pettandi|order cheyyandi")
_CART_QUERY = _anywhere(
    "what'?s in (?:my|the) cart|what is in (?:my|the) cart|read (?:my|the) cart|show (?:my|the) cart|what (?:do|did) i (?:have|add|order)|"
    "కార్ట్‌లో ఏముంది|కార్ట్ లో ఏముంది|కార్ట్‌లో ఏమున్నాయి|కార్ట్ లో ఏమున్నాయి|కార్ట్ చెప్పండి|ఏం వేశాను|cart lo emundi|cart lo emi")
_TOTAL_QUERY = _anywhere(
    "what'?s (?:my|the) total|what is (?:my|the) total|the total|my total|how much (?:is it|in total|so far|altogether)|total (?:amount|price|bill)|"
    "మొత్తం ఎంత|ఎంత అయింది|ఎంత అయ్యింది|బిల్లు ఎంత|mottam entha|mottham entha|entha ayindi")
_BUDGET_QUERY = _anywhere(
    "what was my budget|what'?s my budget|what is my budget|my budget again|how much (?:is )?left|how much can i (?:still )?spend|budget left|"
    "నా బడ్జెట్ ఎంత|బడ్జెట్ ఎంత|ఎంత మిగిలింది|ఇంకా ఎంత మిగిలింది|budget entha")
_UNDO = _whole("undo|undo that|take that back|go back|put it back|వెనక్కి|వెనక్కి తీసుకో|వెనక్కి తీసుకోండి|ముందులా చేయండి")
_CLEAR = _anywhere(
    "clear (?:the |my )?cart|empty (?:the |my )?cart|remove everything|start over|start again|delete everything|"
    "కార్ట్ ఖాళీ చేయండి|కార్ట్ ఖాళీ చెయ్యండి|అన్నీ తీసేయండి|మొత్తం తీసేయండి|మళ్ళీ మొదలు పెట్టండి|cart khali")
_REMOVE = _anywhere(
    "remove|delete|take out|take off|drop|get rid of|without|తీసేయండి|తీసేయి|తీసెయ్యి|తీసేయ్|తీయండి|తీసివేయండి|వద్దు|teeseyi|teesey|teeseyandi|vaddu")
_ADD = _anywhere(
    "add|put|i want|i need|i'?ll take|i'?ll have|give me|get me|include|want|take|order|"
    "వేయి|వెయ్యి|వేయండి|వెయ్యండి|వేసేయ్|వేసేయండి|పెట్టు|పెట్టండి|ఇవ్వు|ఇవ్వండి|కావాలి|తీసుకుంటాను|add చెయ్యి|add చేయి|add చేయండి|add చెయ్యండి|"
    "veyyi|veyi|veyandi|pettu|pettandi|ivvu|ivvandi|kavali|kaavali")
_MORE = _anywhere("more|another|extra|ఇంకా|ఇంకో|ఇంకొక|మరో|మరొక|inko|inka|marinni")
_LESS = _anywhere("less|fewer|reduce|తగ్గించు|తగ్గించండి|తక్కువ చేయండి")
_MAKE_IT = _anywhere("make (?:it|that)|change (?:it |that )?to|set (?:it |that )?to|instead|చేయండి|చెయ్యండి|చేయి|మార్చండి|మార్చు")


def wants_checkout(text: str) -> bool:
    return bool(_CHECKOUT.search(normalize(text)))


_JUST_CART = _whole("(?:my |the )?cart|కార్ట్|నా కార్ట్")
_JUST_TOTAL = _whole("(?:the |my )?total|టోటల్|మొత్తం")


def asks_cart(text: str) -> bool:
    t = normalize(text)
    return bool(_CART_QUERY.search(t) or _JUST_CART.match(t))


def asks_total(text: str) -> bool:
    t = normalize(text)
    return bool(_TOTAL_QUERY.search(t) or _JUST_TOTAL.match(t))


def asks_budget(text: str) -> bool:
    return bool(_BUDGET_QUERY.search(normalize(text)))


def wants_undo(text: str) -> bool:
    return bool(_UNDO.match(normalize(text)))


def wants_clear(text: str) -> bool:
    return bool(_CLEAR.search(normalize(text)))


def wants_remove(text: str) -> bool:
    return bool(_REMOVE.search(normalize(text)))


def wants_add(text: str) -> bool:
    return bool(_ADD.search(normalize(text)))


def wants_more(text: str) -> bool:
    return bool(_MORE.search(normalize(text)))


def wants_less(text: str) -> bool:
    return bool(_LESS.search(normalize(text)))


def wants_set(text: str) -> bool:
    return bool(_MAKE_IT.search(normalize(text)))


# --- cuts -----------------------------------------------------------------------------------------------------
_CUTS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("curry cut", _anywhere("curry cut|curry pieces|కూర ముక్కలు|కూర ముక్కలుగా|kura mukkalu")),
    ("steaks", _anywhere("steaks?|slices?|ముక్కలు|ముక్కలుగా|mukkalu")),
    ("boneless", _anywhere("boneless|without bones?|బోన్‌లెస్|బోన్ లెస్|ఎముకలు లేకుండా")),
    ("cleaned", _anywhere("cleaned|clean|clean it|shelled|peeled|gutted|శుభ్రం|శుభ్రంగా|శుభ్రం చేసి|క్లీన్|క్లీన్ చేసి|clean cheyyi|subhram")),
    ("whole", _anywhere("whole|uncut|as it is|as is|మొత్తం|మొత్తంగా|పూర్తిగా|అలాగే ఇవ్వండి|mottam|mottham")),
]


def cut_in(text: str) -> Optional[str]:
    t = normalize(text)
    for cut, pattern in _CUTS:
        if pattern.search(t):
            return cut
    return None


# --- which of the things shown ---------------------------------------------------------------------------------
_ORDINALS: List[Tuple[int, "re.Pattern[str]"]] = [
    (1, _anywhere("first|first one|the first|number one|option one|మొదటిది|మొదటి|ఒకటోది|ఫస్ట్|modatidi|first di")),
    (2, _anywhere("second|second one|the second|number two|option two|రెండోది|రెండవది|రెండో|సెకండ్|rendodi|second di")),
    (3, _anywhere("third|third one|the third|number three|option three|మూడోది|మూడవది|మూడో|థర్డ్|moododi")),
    (-1, _anywhere("last one|the last|చివరిది|లాస్ట్")),
]
_CHEAPEST = _anywhere("cheaper one|cheapest|the cheaper|less expensive|తక్కువ ధరది|తక్కువ ధర ఉన్నది|చవకది|చవకైనది|cheap one")
_DEAREST = _anywhere("expensive one|dearer one|costlier one|the costlier|most expensive|ఎక్కువ ధరది|ఖరీదైనది")
_THAT_ONE = _anywhere("that one|this one|the same one|same|అదే|ఇదే|అది|adi|ade")


def ordinal_in(text: str) -> Optional[int]:
    t = normalize(text)
    for n, pattern in _ORDINALS:
        if pattern.search(t):
            return n
    return None


def wants_cheapest(text: str) -> bool:
    return bool(_CHEAPEST.search(normalize(text)))


def wants_dearest(text: str) -> bool:
    return bool(_DEAREST.search(normalize(text)))


def means_that_one(text: str) -> bool:
    return bool(_THAT_ONE.search(normalize(text)))
