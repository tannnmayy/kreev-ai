"""Code first: the turns that need no model.

"Stop", a yes to the read-back, "what's my total", "half a kilo of prawns". These are answered here in microseconds, at no
quota, and they can't hallucinate. This is the first mind. It is also what keeps Ala shopping when every model is out of
quota: `lenient` lets it take its best reading of a sentence instead of handing it on.

The rule is conservative. A turn is handled here only when every word of it is explained: a product, a quantity, a known
command word. "No prawns", "two kilos, but not today" and anything with a question in it go to a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..language import cues
from ..language.numbers import Quantity, find_money, find_quantities, normalize, tokens
from ..shop import replies as R
from ..shop.engine import Result, ShopEngine
from ..shop.replies import Say


@dataclass
class Step:
    """One action, and how long the gate should hold it: "edit", "order", or None for a read."""
    name: str
    run: Callable[[], Result]
    args: Dict[str, Any] = field(default_factory=dict)
    hold: Optional[str] = "edit"


@dataclass
class Plan:
    route: str
    steps: List[Step] = field(default_factory=list)
    say: Say = field(default_factory=Say)
    silent: bool = False
    resume: bool = False


# Words that add nothing to what a cart command means
_GLUE = {
    "and", "of", "please", "also", "a", "an", "the", "some", "me", "i", "want", "need", "add", "put", "give", "get", "to",
    "my", "cart", "it", "in", "into", "for", "with", "just", "only", "fresh", "good", "nice", "today", "now", "too", "ok", "okay",
    "yes", "sir", "take", "have", "ll", "i'll", "d", "like", "would", "could", "can", "you", "more", "another", "extra",
    "make", "that", "change", "set", "instead", "there", "kilo", "kilos", "kg",
    "మరియు", "ఇంకా", "ఇంకో", "ఒక", "వేయి", "వెయ్యి", "వేయండి", "వెయ్యండి", "వేసేయ్", "వేసేయండి", "పెట్టు", "పెట్టండి", "ఇవ్వు", "ఇవ్వండి", "కావాలి",
    "తీసుకుంటాను", "చెయ్యి", "చేయి", "చేయండి", "చెయ్యండి", "కార్ట్లో", "కార్ట్", "లో", "కి", "నాకు", "అండి", "దయచేసి", "సరే", "ఓకే", "తాజా",
    "naku", "andi", "lo", "cheyyi", "veyyi", "pettu", "ivvu", "kavali", "kaavali",
}

# Words a plain "what have you got" or "what's for lunch" is made of. Anything else in the sentence, like "gentle on the
# stomach", carries meaning code can't weigh, so it goes to a model.
_ASK_WORDS = {
    "what", "what's", "which", "do", "does", "you", "have", "got", "is", "are", "any", "there", "show", "me", "for", "the", "a",
    "an", "some", "good", "fresh", "today", "tonight", "tomorrow", "lunch", "dinner", "breakfast", "should", "can", "could", "i",
    "we", "make", "cook", "buy", "get", "suggest", "recommend", "something", "people", "persons", "of", "us", "under", "within",
    "below", "budget", "rupees", "family", "my", "our", "to", "in", "on", "and", "or", "with", "available", "ideas", "idea",
    "please", "s", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "weekend", "guests", "rs",
    "ఏమి", "ఏం", "ఏ", "ఏమున్నాయి", "ఉన్నాయి", "ఉంది", "బాగుంటుంది", "వండాలి", "చేయాలి", "భోజనం", "లంచ్", "డిన్నర్", "బడ్జెట్", "లో", "కి", "కోసం", "లోపు",
    "సూచించండి", "చెప్పండి", "చూపించండి", "ఈ", "రోజు", "రేపు", "ఆదివారం", "సోమవారం", "మా", "మాకు", "నాకు", "రూపాయలు", "చేపలు", "చేప", "కూరగాయలు", "మందికి",
}

_ITEM_NEAR_BEFORE = 16      # how far a quantity may sit before its product, in characters
_ITEM_NEAR_AFTER = 12

_GREETING = re.compile(r"^\W*(hi|hello|hey|namaste|namaskaram|నమస్కారం|నమస్తే|హలో|హాయ్|good (?:morning|afternoon|evening))\W*(ala|అల)?\W*$", re.I)
_THANKS = re.compile(r"^\W*(thanks?|thank you|thank you so much|ధన్యవాదాలు|థాంక్స్|థాంక్యూ)\W*$", re.I)
_BYE = re.compile(r"^\W*(bye|goodbye|that'?s all thanks|nothing else thanks|వెళ్తాను|ఉంటాను|ఉంటానండి|బై)\W*$", re.I)
_WHO = re.compile(r"(are you (?:a )?(?:human|real|robot|bot|ai|person)|who are you|what are you|your name|నువ్వు ఎవరు|మీరు ఎవరు|మీ పేరు|మనిషా|రోబోనా)", re.I)
_DISCOUNT = re.compile(r"(discount|offer|coupon|\d+\s*%|percent off|cheaper price|reduce the price|bargain|డిస్కౌంట్|తగ్గింపు|ధర తగ్గించ|ఆఫర్)", re.I)
_DELIVERY = re.compile(r"(deliver|when will it (?:come|arrive)|delivery|డెలివరీ|ఎప్పుడు వస్తుంది|ఎప్పుడు వస్తాయి)", re.I)
_PAYMENT = re.compile(r"(pay|payment|upi|card|cash|gpay|phonepe|పేమెంట్|డబ్బులు|ఎలా కట్టాలి|క్యాష్|కార్డ్)", re.I)
_RETURNS = re.compile(r"(return|refund|replace|not fresh|spoiled|రిటర్న్|రీఫండ్|మార్చి ఇస్తారా|పాడైతే)", re.I)
_PRICE_Q = re.compile(r"(how much|price|cost|rate|ధర|ఎంత|రేటు|rate entha)", re.I)
_HAVE_Q = re.compile(r"(do you have|have you got|is there|are there|any |available|ఉందా|ఉన్నాయా|దొరుకుతుందా|దొరుకుతాయా|unda|unnaya)", re.I)
_MEAL_Q = re.compile(
    r"(lunch|dinner|breakfast|what should i (?:make|cook|buy)|what can i (?:make|cook)|what'?s good|what is good|suggest|recommend|ideas?|"
    r"ఏమి బాగుంటుంది|ఏం బాగుంటుంది|ఏం వండాలి|ఏమి వండాలి|ఏం చేయాలి|భోజనం|లంచ్|డిన్నర్|సూచించండి|చెప్పండి ఏం)", re.I)
_COOK = re.compile(r"(make|cook|prepare|ingredients|recipe|everything for|వండాలి|చేయాలి|చేయడానికి|కావాల్సినవి|కావలసినవి)", re.I)
_COMPARE = re.compile(r"(compare|which is|which one is|difference|better|cheaper|పోల్చ|ఏది మంచిది|ఏది బాగుంటుంది|తేడా)", re.I)
_INSTEAD = re.compile(r"(instead of|substitute|alternative|replace|బదులు|బదులుగా)", re.I)
_BROWSE = re.compile(
    r"(what (?:do you have|have you got|is fresh|'?s fresh)|show me|what fish|which fish|any fish|what'?s available|"
    r"ఏం ఉన్నాయి|ఏమి ఉన్నాయి|ఏమున్నాయి|ఏం చేపలు|ఏ చేపలు|చూపించండి)", re.I)
_BUDGET_CUE = re.compile(r"(budget|under|within|below|less than|up ?to|maximum|not more than|బడ్జెట్|లోపు|మించకుండా)", re.I)
_VEGETARIAN = re.compile(
    r"(vegetarian|veg only|pure veg|no non.?veg|no fish|no meat|not eating (?:fish|meat|non.?veg)|శాకాహారం|నాన్.?వెజ్ వద్దు|నాన్.?వెజ్ తినట్లేదు|నాన్.?వెజ్ తినం|"
    r"non.?veg vaddu|కార్తీక|karthika|kartika)", re.I)
_NOT_BONY = re.compile(r"(no bones|few bones|less bones|avoid bon|without bones|bony|ముల్లు వద్దు|ముళ్ళు వద్దు|ముల్లు తక్కువ|ముళ్ళు తక్కువ|ముళ్లు వద్దు)", re.I)
_STANDING = re.compile(r"(don'?t (?:confirm|ask|repeat)|stop (?:confirming|asking|repeating)|no need to (?:confirm|ask)|అడగొద్దు|అడగకండి)", re.I)
_SLOWER = re.compile(r"(speak slower|slow down|too fast|slowly please|నెమ్మదిగా|మెల్లగా)", re.I)
_FASTER = re.compile(r"(speak faster|speed up|too slow|faster please|త్వరగా మాట్లాడ|వేగంగా మాట్లాడ)", re.I)
_CANCEL = re.compile(r"(cancel (?:my |the |that )?order|ఆర్డర్ రద్దు|ఆర్డర్ క్యాన్సిల్|order cancel)", re.I)
_SAME_AGAIN = re.compile(r"(same as last time|same as before|my usual|the usual|repeat (?:my |the )?(?:last )?order|పోయినసారి లాగే|పోయిన సారి లాగే|ఎప్పటిలాగే)", re.I)
_BUT = re.compile(r"(?<![\wఀ-౿])(but|except|కానీ|kani)(?![\wఀ-౿])", re.I)
_FORGET = re.compile(r"(forget me|delete my (?:data|details)|నన్ను మర్చిపో|నా వివరాలు తీసేయ)", re.I)

_CATEGORY_WORDS: List[Tuple["re.Pattern[str]", Dict[str, Any]]] = [
    (re.compile(r"(fish|seafood|చేపలు|చేప|chepalu)", re.I), {"category": "fish"}),
    (re.compile(r"(vegetables?|veggies|కూరగాయలు|kuragayalu)", re.I), {"category": "vegetable"}),
    (re.compile(r"(dry fish|dried fish|ఎండు చేపలు)", re.I), {"category": "dry-fish"}),
    (re.compile(r"(pickles?|పచ్చళ్ళు|పచ్చడి|ఊరగాయ)", re.I), {"category": "pickle"}),
    (re.compile(r"(deepavali|diwali|festival|pooja|puja|karthika|దీపావళి|పండుగ|పూజ|కార్తీక)", re.I), {"category": "festival"}),
    (re.compile(r"(fruit|fruits|పండ్లు)", re.I), {"category": "fruit"}),
]
_TAG_WORDS: List[Tuple["re.Pattern[str]", str]] = [
    (re.compile(r"(fry|fried|వేపుడు|vepudu)", re.I), "fry"),
    (re.compile(r"(curry|pulusu|కూర|పులుసు)", re.I), "curry"),
    (re.compile(r"(cheap|budget|low price|తక్కువ ధర|చవక)", re.I), "budget"),
    (re.compile(r"(few bones|less bones|no bones|ముల్లు తక్కువ|ముళ్ళు తక్కువ)", re.I), "few-bones"),
    (re.compile(r"(premium|best|expensive|మంచి రకం)", re.I), "premium"),
]
_TE_PEOPLE = {"ఒకరికి": 1, "ఇద్దరికి": 2, "ముగ్గురికి": 3, "నలుగురికి": 4, "ఐదుగురికి": 5, "అయిదుగురికి": 5, "ఆరుగురికి": 6, "ఏడుగురికి": 7, "ఎనిమిది మందికి": 8,
              "పది మందికి": 10, "పదిమందికి": 10}
_PEOPLE_EN = re.compile(r"(?:for|family of|feed|serves?)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)(?:\s+(?:people|persons|of us|guests|members))?|"
                        r"(\d+|two|three|four|five|six|seven|eight|nine|ten)\s+(?:people|persons|guests|members)", re.I)
_SMALL_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def remainder(full: str, heard: str) -> str:
    """What's left of a reply cut off after `heard`, from the sentence it was cut in."""
    full, heard = " ".join((full or "").split()), " ".join((heard or "").split())
    if not full or not heard:
        return ""
    parts = re.split(r"(?<=[.?!।])\s+", full)
    pos = 0
    for i, p in enumerate(parts):
        pos += len(p) + 1
        if pos > len(heard) + 1:
            return " ".join(parts[i:])
    return ""


def extract_needs(text: str) -> Dict[str, Any]:
    """Budget, people and diet, stated in passing. Safe to take in code: they're facts about the customer, not actions."""
    t = normalize(text)
    needs: Dict[str, Any] = {}
    if _BUDGET_CUE.search(t):
        amount = find_money(t)
        if amount is not None:
            needs["budget_inr"] = amount
    for word, n in _TE_PEOPLE.items():
        if word in t:
            needs["servings"] = n
    m = _PEOPLE_EN.search(t)
    if m:
        raw = (m.group(1) or m.group(2) or "").lower()
        n = int(raw) if raw.isdigit() else _SMALL_WORDS.get(raw)
        if n and n <= 40:
            needs["servings"] = n
    if _VEGETARIAN.search(t):
        needs["vegetarian"] = True
    if _NOT_BONY.search(t):
        needs["avoid"] = ["bony"]
    return needs


def _span_tokens(text: str, spans: Sequence[Tuple[int, int]]) -> List[str]:
    """The words of the text outside these spans."""
    return [w for w, s, e in tokens(text) if not any(a <= s and e <= b for a, b in spans)]


def _pairs(text: str, shop: ShopEngine) -> Tuple[List[Tuple[Any, Optional[Quantity]]], List[Tuple[int, int]]]:
    """Each product named, with the quantity that belongs to it, and the spans those words cover."""
    t = normalize(text)
    mentions = shop.catalog.mentions(t)
    quantities = [q for q in find_quantities(t) if not any(m.start <= q.char_start and q.char_end <= m.end for m in mentions)]
    used: set = set()
    out = []
    spans: List[Tuple[int, int]] = []
    for m in mentions:
        spans.append((m.start, m.end))
        before = [q for q in quantities if id(q) not in used and q.char_end <= m.start and m.start - q.char_end <= _ITEM_NEAR_BEFORE]
        after = [q for q in quantities if id(q) not in used and q.char_start >= m.end and q.char_start - m.end <= _ITEM_NEAR_AFTER]
        q = before[-1] if before else (after[0] if after else None)
        if q is not None:
            used.add(id(q))
            spans.append((q.char_start, q.char_end))
        out.append((m.product, q))
    return out, spans


def _explained(text: str, spans: Sequence[Tuple[int, int]]) -> bool:
    t = normalize(text)
    left = [w for w in _span_tokens(t, spans) if w not in _GLUE and not w.isdigit()]
    return not left


def _set_step(shop: ShopEngine, product, q: Optional[Quantity], cut: Optional[str], more: bool = False, less: bool = False) -> Step:
    value, unit = q.in_base() if q is not None else (1.0, None)
    if more or less:
        delta = value if more else -value
        return Step("change_item", lambda: shop.change_item(product.sku, delta, unit), {"item": product.sku, "delta": delta, "unit": unit})
    return Step("set_item", lambda: shop.set_item(product.sku, value, unit, cut),
                {"item": product.sku, "qty": value, "unit": unit, "cut": cut})


def _answer_pending(t: str, shop: ShopEngine) -> Optional[Plan]:
    p = shop.state.pending
    if p is None:
        return None
    if p.kind != "order_review" and cues.wants_checkout(t):
        return None     # "place the order" is a yes to the order, not to whatever else was open
    if cues.is_yes(t):
        if p.kind == "order_review":
            return Plan("order", [Step("place_order", shop.place_order, {"hash": p.summary_hash}, hold="order")])
        if p.kind in ("confirm_item", "basket", "swap") or (p.kind == "substitute" and len(shop.state.shown) == 1):
            return Plan("pending", [Step("accept", shop.accept_pending, {"kind": p.kind})])
    if cues.is_no(t) and p.kind != "quantity":
        return Plan("pending", [Step("decline", shop.decline_pending, {"kind": p.kind}, hold=None)])
    if p.kind == "cut":
        cut = cues.cut_in(t)
        sku = p.data.get("sku")
        # "మొత్తం" is both "whole" and "total", so a question is never an answer about the cut
        if cut and len(t.split()) <= 5 and not cues.is_question(t) and not cues.asks_total(t):
            return Plan("pending", [Step("set_cut", lambda: shop.set_cut(sku, cut), {"item": sku, "cut": cut})])
    if p.kind == "quantity" and not shop.catalog.mentions(t):
        qs = find_quantities(t)
        if qs and not cues.is_question(t):
            value, unit = qs[-1].in_base()
            sku = p.data["sku"]
            cut = cues.cut_in(t)
            return Plan("pending", [Step("set_item", lambda: shop.set_item(sku, value, unit, cut),
                                         {"item": sku, "qty": value, "unit": unit})])
    if p.kind == "meal_choice":
        options = p.data.get("options", [])
        n = cues.ordinal_in(t)
        choice = None
        if n is not None and options:
            choice = options[n - 1] if 0 < n <= len(options) else (options[-1] if n == -1 else None)
        if choice is None:
            r = shop.catalog.recipe(t)
            if r is not None:
                choice = next((o for o in options if o["recipe"] == r.id), None)
        if choice is not None:
            dish, servings = choice["recipe"].replace("-", " "), choice["servings"]
            return Plan("pending", [Step("plan_meal", lambda: shop.plan_meal(dish, servings), {"dish": dish}, hold=None)])
    return None


def open_request(said: Sequence[str]) -> str:
    """What the customer has said since the last answered request, as one request: a turn cut short is read again with what
    followed, so a correction always sees what it corrects. A stop word ends all of that, so nothing before it comes back."""
    for i in range(len(said) - 1, -1, -1):
        if cues.control(said[i]) == "stop":
            said = said[i + 1:] or said[i:]
            break
    return " ".join(said).strip()


def plan(text: str, shop: ShopEngine, *, last_reply: str = "", cut_rest: str = "", recent_item: Optional[str] = None,
         lenient: bool = False) -> Optional[Plan]:
    """What code can do with these words, or None when they need a model."""
    t = normalize(text)
    if not t:
        return Plan("empty", silent=True)

    answered = _answer_pending(t, shop)
    if answered is not None:
        return answered

    kind = cues.control(t)
    if kind == "stop":
        return Plan("control", silent=True)
    if kind == "resume":
        return Plan("control", say=Say(cut_rest, cut_rest) if cut_rest else Say("Go ahead.", "చెప్పండి."), resume=True)
    if kind == "repeat":
        return Plan("control", say=Say(last_reply, last_reply) if last_reply else Say("I haven't said anything yet.", "నేను ఇంకా ఏమీ చెప్పలేదు."))
    if kind == "backchannel":
        if cut_rest:
            return Plan("control", say=Say(cut_rest, cut_rest), resume=True)
        return Plan("control", silent=True)

    if shop.state.pending is None and cues.is_yes(t) and not cues.wants_checkout(t):
        return Plan("smalltalk", say=Say("What would you like next?", "ఇంకా ఏం కావాలి?"))
    if shop.state.pending is None and cues.is_no(t):
        return Plan("smalltalk", say=Say("All right. What would you like instead?", "సరే అండి. ఇంకేం కావాలి?"))

    if _GREETING.match(t):
        return Plan("smalltalk", say=Say("Hello. What would you like today?", "నమస్కారం. ఈ రోజు ఏం కావాలి?"))
    if _THANKS.match(t):
        return Plan("smalltalk", say=Say("You're welcome.", "పర్వాలేదండి."))
    if _BYE.match(t):
        return Plan("smalltalk", say=Say("Goodbye. Take care.", "సరే అండి, ఉంటాను."))
    if _WHO.search(t):
        return Plan("smalltalk", say=Say("I'm Ala, an AI shopping assistant for this market. What would you like today?",
                                         "నేను అల, ఈ మార్కెట్ AI షాపింగ్ అసిస్టెంట్‌ని. ఈ రోజు ఏం కావాలి?"))
    if _FORGET.search(t):
        return Plan("forget")

    # readings, answered from the state
    if cues.wants_checkout(t) and (cues.asks_cart(t) or cues.asks_total(t)) and not shop.catalog.mentions(t):
        return Plan("command", [Step("review_order", shop.review_order)])     # the read-back answers both
    if cues.asks_cart(t):
        return Plan("reading", [Step("read_cart", shop.read_cart, hold=None)])
    if cues.asks_budget(t):
        return Plan("reading", [Step("read_budget", shop.read_budget, hold=None)])
    mentions = shop.catalog.mentions(t)
    if cues.asks_total(t) and not mentions:
        return Plan("reading", [Step("read_total", shop.read_total, hold=None)])
    if _DISCOUNT.search(t) and not mentions:
        return Plan("policy", say=Say("Prices are fixed today, and there are no discounts.", "ఈ రోజు ధరలు ఫిక్స్‌డ్, డిస్కౌంట్లు లేవు."))
    shop_facts = shop.catalog.shop
    if not mentions and cues.is_question(t):
        if _DELIVERY.search(t):
            return Plan("policy", say=R.delivery_info(shop_facts))
        if _PAYMENT.search(t):
            return Plan("policy", say=R.payment_info(shop_facts))
        if _RETURNS.search(t):
            return Plan("policy", say=R.returns_info(shop_facts))

    # commands
    if _CANCEL.search(t):
        return Plan("command", [Step("cancel_order", shop.cancel_order, hold="order")])
    if _SAME_AGAIN.search(t) and not _BUT.search(t):
        return Plan("command", [Step("repeat_last_order", shop.repeat_last_order)])
    if cues.wants_undo(t):
        return Plan("command", [Step("undo", shop.undo)])
    if cues.wants_clear(t):
        return Plan("command", [Step("clear_cart", shop.clear_cart)])
    if cues.wants_checkout(t) and not mentions and not cues.is_question(t):
        return Plan("command", [Step("review_order", shop.review_order)])

    steps: List[Step] = []
    needs = extract_needs(t)
    if needs:
        steps.append(Step("set_needs", lambda: shop.set_needs(**needs), needs))
    if _STANDING.search(t):
        steps.append(Step("add_standing", lambda: shop.add_standing(text), {"text": text}))

    # meals and browsing
    recipe = shop.catalog.recipe(t)
    if recipe is not None and (_COOK.search(t) or "servings" in needs or lenient):
        servings = needs.get("servings")
        steps.append(Step("plan_meal", lambda: shop.plan_meal(recipe.id.replace("-", " "), servings, needs.get("budget_inr")),
                          {"dish": recipe.id}, hold=None))
        return Plan("meal", steps)
    if _MEAL_Q.search(t) and not mentions and (lenient or _plain_ask(t)):
        steps.append(Step("suggest_meals", lambda: shop.suggest_meals(needs.get("servings"), needs.get("budget_inr"), needs.get("vegetarian")),
                          {}, hold=None))
        return Plan("meal", steps)
    if mentions and _INSTEAD.search(t) and cues.is_question(t):
        steps.append(Step("offer_substitutes", lambda: shop.offer_substitutes(mentions[0].product.sku), {}, hold=None))
        return Plan("browse", steps)
    if len(mentions) >= 2 and _COMPARE.search(t):
        skus = [m.product.sku for m in mentions]
        steps.append(Step("compare", lambda: shop.compare(skus), {"items": skus}, hold=None))
        return Plan("browse", steps)
    if (_BROWSE.search(t) or (cues.is_question(t) and not mentions and any(p.search(t) for p, _ in _CATEGORY_WORDS)))             and (lenient or _plain_ask(t)):
        kwargs: Dict[str, Any] = {}
        for pattern, args in _CATEGORY_WORDS:
            if pattern.search(t):
                kwargs.update(args)
                break
        tags = [tag for pattern, tag in _TAG_WORDS if pattern.search(t)]
        if tags:
            kwargs["tags"] = tags
        if kwargs or _BROWSE.search(t):
            steps.append(Step("show_products", lambda: shop.show_products(**kwargs), kwargs, hold=None))
            return Plan("browse", steps)

    # one product, asked about
    if cues.is_question(t) and len(mentions) == 1:
        product = mentions[0].product
        qs = [q for q in find_quantities(t) if q.unit]
        if _PRICE_Q.search(t):
            value = qs[0].in_base()[0] if qs else None
            steps.append(Step("price_of", lambda: shop.price_of(product.sku, value), {"item": product.sku}, hold=None))
            return Plan("reading", steps)
        if _HAVE_Q.search(t):
            def have() -> Result:
                if shop.level(product.sku) <= 0:
                    return shop._sold_out(product)
                shop.state.shown = [product.sku]
                return Result(True, say=R.in_stock(product))
            steps.append(Step("in_stock", have, {"item": product.sku}, hold=None))
            return Plan("reading", steps)
        if not lenient:
            return None

    if cues.is_question(t) and not lenient:
        return Plan("needs", steps) if steps and _explained(t, []) else None

    # removing
    if mentions and cues.wants_remove(t) and all(shop.line(m.product.sku) for m in mentions):
        for m in mentions:
            sku = m.product.sku
            steps.append(Step("remove_item", lambda sku=sku: shop.remove_item(sku), {"item": sku}))
        return Plan("command", steps)

    revising = cues.revising(t)
    more, less = cues.wants_more(t), cues.wants_less(t)
    cut = cues.cut_in(t)

    # adding named products
    if mentions:
        pairs, spans = _pairs(t, shop)
        if (revising or not _explained_with_cut(t, spans, cut)) and not lenient:
            return None
        if revising and lenient:
            qs = find_quantities(t)
            pairs = [(pairs[-1][0], qs[-1] if qs else None)]
        for product, q in pairs:
            if q is None and not (more or less):
                steps.append(Step("ask_quantity", lambda p=product: shop.ask_quantity(p.sku), {"item": product.sku}, hold=None))
            else:
                steps.append(_set_step(shop, product, q, cut, more and q is not None, less and q is not None))
        return Plan("rule", steps)

    # a choice from the list just shown
    ordinal = cues.ordinal_in(t)
    choice = None
    if shop.state.shown:
        if ordinal is not None:
            choice = shop.product(ordinal)
        elif cues.wants_cheapest(t):
            choice = shop.cheapest_shown()
        elif cues.wants_dearest(t):
            choice = shop.cheapest_shown(dearest=True)
        elif cues.means_that_one(t) and len(shop.state.shown) == 1:
            choice = shop.product(1)
    qs = find_quantities(t)
    if ordinal is not None:
        qs = [q for q in qs if q.unit]     # "the second one": "one" isn't an amount
    if choice is not None:
        if qs:
            steps.append(_set_step(shop, choice, qs[-1], cut))
        else:
            steps.append(Step("ask_quantity", lambda: shop.ask_quantity(choice.sku), {"item": choice.sku}, hold=None))
        return Plan("rule", steps)

    # an amount with no product: it means the item just discussed
    if qs and (cues.wants_set(t) or more or less or len(t.split()) <= 4):
        spans = [(q.char_start, q.char_end) for q in qs]
        if not _explained(t, spans) and not lenient:
            return None if not steps else Plan("needs", steps)
        target = recent_item or (shop.state.cart[0].sku if len(shop.state.cart) == 1 else None)
        if target is None and len(shop.state.cart) >= 2:
            names = [shop.catalog.by_sku[x.sku] for x in shop.state.cart]
            return Plan("clarify", say=R.which_item([p.en for p in names], [p.te for p in names]))
        if target is not None:
            steps.append(_set_step(shop, shop.catalog.by_sku[target], qs[-1], cut, more, less))
            return Plan("rule", steps)

    if cut and recent_item and len(t.split()) <= 5:
        steps.append(Step("set_cut", lambda: shop.set_cut(recent_item, cut), {"item": recent_item, "cut": cut}))
        return Plan("rule", steps)

    if steps:
        # a statement of needs, and nothing else to do
        leftovers = _explained_needs(t)
        if leftovers or lenient:
            return Plan("needs", steps)
    return None


def _plain_ask(t: str) -> bool:
    """Is this question made of plain shopping words? At most one word may be unknown."""
    spans = [(q.char_start, q.char_end) for q in find_quantities(t)]
    left = [w for w in _span_tokens(t, spans) if w not in _ASK_WORDS and not w.isdigit() and w not in _TE_PEOPLE
            and not any(p.search(w) for p, _ in _CATEGORY_WORDS) and not any(p.search(w) for p, _ in _TAG_WORDS)]
    return len(left) <= 1


def _explained_with_cut(t: str, spans: Sequence[Tuple[int, int]], cut: Optional[str]) -> bool:
    left = [w for w in _span_tokens(t, spans) if w not in _GLUE and not w.isdigit()]
    if cut and left:
        left = [w for w in left if cues.cut_in(w) is None and w not in ("చేసి", "cut", "curry", "పెట్టి", "గా")]
    return not left


def _explained_needs(t: str) -> bool:
    """A sentence that only states a budget, a head count or a diet."""
    return not (cues.is_question(t) or cues.wants_add(t) or cues.wants_remove(t)) or len(t.split()) <= 6


def speech_pace(text: str) -> Optional[float]:
    """"Speak slower" and "faster": the voice's pace for the rest of the call."""
    t = normalize(text)
    if _SLOWER.search(t):
        return 0.85
    if _FASTER.search(t):
        return 1.15
    return None
