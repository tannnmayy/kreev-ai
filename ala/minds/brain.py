"""The brain: one turn, from the customer's words to what Ala says.

Three minds, cheapest first:
  code   the rules in rules.py: control words, answers to Ala's own questions, readings, plain cart commands
  fast   one model call: understands the sentence and says what to do; the shop words the facts
  slow   a stronger model, for planning; the fast mind's one-line restatement covers the wait

The brain knows nothing about audio or LiveKit, so every behaviour here can be tested in text, offline. Actions pass through
the coordinator, so nothing changes while the customer is still speaking or correcting themselves.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Deque, Dict, List, Optional, Sequence, Tuple

from ..config import SETTINGS, Settings
from ..coordinator import Coordinator, Superseded
from ..language import cues
from ..language.detect import LanguageTracker
from ..language.numbers import find_quantities, normalize
from ..language.speakable import polish
from ..perception.reading import Reading
from ..shop import replies as R
from ..shop.engine import MODEL_ACTIONS, Result, ShopEngine
from ..shop.replies import Say
from ..shop.state import Pending
from ..shop.store import Store
from . import prompts, rules
from .router import AllBusy, Router, parse_json

log = logging.getLogger("ala.brain")

MISUNDERSTOOD = Say("Sorry, I didn't follow that. Could you say it another way?", "క్షమించండి, అర్థం కాలేదు. ఇంకోలా చెప్పగలరా?")
MINDS_DOWN = Say("Sorry, I'm having trouble thinking right now. Your cart is safe. Could you say that more simply?",
                 "క్షమించండి, ఇప్పుడు కొంచెం ఇబ్బందిగా ఉంది. మీ కార్ట్ సురక్షితంగా ఉంది. ఇంకొంచెం సులభంగా చెప్పగలరా?")
BOTH_LANGUAGES = Say("I can help in English or Telugu.", "నేను తెలుగు లేదా ఇంగ్లీష్‌లో మాట్లాడగలను.")
SORRY = Say("Sorry.", "క్షమించండి.")
FORGOTTEN = Say("Done, I've forgotten your details.", "మీ వివరాలు తీసేశాను.")
GREETING = Say("నమస్కారం, I'm Ala. Telugu or English, either is fine. What would you like today?",
               "నమస్కారం, నేను అల. తెలుగు లేదా ఇంగ్లీష్, ఏదైనా పర్వాలేదు. ఈ రోజు ఏం కావాలి?")


# Routes that move the conversation on from whatever Ala last asked
MOVES_ON = ("rule", "command", "meal", "browse")


@dataclass
class Speech:
    text: str
    lang: str
    route: str = "code"         # code | fast | slow | control
    kind: str = "reply"         # reply | bridge


@dataclass
class Decision:
    actions: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    say: str = ""
    listen: bool = False
    consult: bool = False
    shown: List[str] = field(default_factory=list)

    @classmethod
    def parse(cls, raw: Optional[Dict[str, Any]]) -> Optional["Decision"]:
        if not isinstance(raw, dict):
            return None
        actions = []
        for a in raw.get("actions") or []:
            if isinstance(a, dict) and isinstance(a.get("name"), str):
                args = a.get("args")
                actions.append((a["name"], args if isinstance(args, dict) else {}))
        return cls(actions, str(raw.get("say") or "").strip(), bool(raw.get("listen")), bool(raw.get("consult")),
                   [str(x) for x in (raw.get("shown") or []) if isinstance(x, (str, int))][:3])


def allowed_numbers(shop: ShopEngine, products: Sequence[Dict[str, Any]], meals: Sequence[Dict[str, Any]] = ()) -> set:
    """Every number a model may say: what it was given, and nothing else."""
    ok = {round(shop.total)}
    n = shop.state.needs
    for v in (n.budget_inr, n.servings):
        if v is not None:
            ok.add(v)
    if n.budget_inr is not None:
        ok.add(round(abs(n.budget_inr - shop.total)))
    for p in products:
        ok.add(p.get("price"))
    for m in meals:
        ok.add(m.get("total"))
    for x in shop.state.cart:
        ok.add(shop.catalog.by_sku[x.sku].price)
        ok.add(round(shop.line_price(x)))
    for s in shop.state.shown:
        if s in shop.catalog.by_sku:
            ok.add(shop.catalog.by_sku[s].price)
    return ok


def drop_invented_numbers(text: str, allowed: set, heard: str = "") -> str:
    """Remove any sentence with a price-sized number the model wasn't given. A wrong price said aloud is worse than a
    shorter reply. Numbers the customer said themselves are fine."""
    from_customer = {q.value for q in find_quantities(heard)}
    kept = []
    for sentence in re.split(r"(?<=[.?!।])\s+", text or ""):
        bad = [q for q in find_quantities(sentence) if q.value >= 20 and q.unit is None
               and q.value not in allowed and q.value not in from_customer]
        if not bad:
            kept.append(sentence)
    return " ".join(kept).strip()


class Brain:
    def __init__(self, shop: ShopEngine, router: Optional[Router] = None, coord: Optional[Coordinator] = None,
                 store: Optional[Store] = None, session_key: str = "", settings: Settings = SETTINGS,
                 on_event: Optional[Callable[..., None]] = None) -> None:
        self.shop = shop
        self.router = router
        self.coord = coord
        self.store = store
        self.session_key = session_key
        self.settings = settings
        self.on_event = on_event or (lambda kind, **data: None)
        self.language = LanguageTracker()
        self.recent: Deque[Tuple[str, str]] = collections.deque(maxlen=8)
        self.spoken: Deque[str] = collections.deque(maxlen=5)
        self.last_reply = ""
        self.cut_rest = ""             # what's left of a reply the customer cut off
        self.heard_note = ""           # for the next turn: how much of the last reply they heard
        self.recent_item: Optional[str] = None
        self.turns = 0
        self.pace = 1.0
        self.acted = False             # did this turn change anything?
        self._hindi_told = False
        self._sorry_turn = -10

    # --- what the voice layer tells the brain ------------------------------------------------------------------
    def reply_heard(self, said: str, heard: str, interrupted: bool) -> None:
        """A reply finished, or was cut off. `heard`: as far as the customer heard it."""
        if interrupted:
            self.cut_rest = rules.remainder(said, heard)
            self.heard_note = 'Your last reply was cut off. The customer heard only: "%s"' % heard if heard else \
                "The customer cut off your last reply before hearing any of it."
            self.shop.readback_cut_short()
        else:
            self.cut_rest = ""
            self.heard_note = ""
        if heard:
            self.recent.append(("ala", heard))

    def greeting(self) -> Speech:
        c = self.shop.state.customer
        if c.returning and c.name:
            say = Say("Welcome back, %s. Same as last time, or something new today?" % c.name,
                      "మళ్ళీ స్వాగతం %s గారు. పోయినసారి లాగే ఇవ్వమంటారా, కొత్తగా ఏమైనా కావాలా?" % c.name)
            return Speech(say.text("en"), "en")
        return Speech(GREETING.en, "te", kind="reply")

    # --- one turn ----------------------------------------------------------------------------------------------
    async def respond(self, text: str, *, reading: Optional[Reading] = None, epoch: Optional[int] = None,
                      seen: bool = False) -> AsyncIterator[Speech]:
        """Everything Ala says to these words, in order. Raises Superseded if the customer said more. `seen`: the customer
        typed or tapped, so they've read the order sheet whether or not they heard it to the end."""
        words = " ".join((text or "").split())
        pending = self.shop.state.pending
        if seen and pending is not None and pending.kind == "order_review":
            pending.heard_complete = True
        self.turns += 1
        self.acted = False
        lang = self.language.hear(words)
        pace = rules.speech_pace(words)
        if pace is not None:
            self.pace = pace
        if self.recent and self.recent[-1][0] == "customer" and words.startswith(self.recent[-1][1]):
            self.recent.pop()       # a turn cut short, read again with what followed
        self.recent.append(("customer", words))
        started = time.monotonic()
        plan = rules.plan(words, self.shop, last_reply=self.last_reply, cut_rest=self.cut_rest, recent_item=self.recent_item)
        if plan is not None and plan.route == "forget":
            await self._forget()
            yield self._said(FORGOTTEN.text(lang), lang, "code")
            return
        if plan is not None:
            async for speech in self._run_plan(plan, words, lang, reading, epoch):
                yield speech
        else:
            async for speech in self._think(words, lang, reading, epoch):
                yield speech
        self.on_event("turn", route=plan.route if plan is not None else "mind", seconds=round(time.monotonic() - started, 3))
        if self.shop.on_change is not None:
            self.shop.on_change()       # the list shown and the needs change without touching the cart
        await self._persist()

    def _said(self, text: str, lang: str, route: str, kind: str = "reply") -> Speech:
        self.last_reply = text
        self.spoken.append(text)
        return Speech(text, lang, route, kind)

    def _tone_prefix(self, reading: Optional[Reading], lang: str) -> str:
        """Say sorry once, in a few words, and not on every turn."""
        if reading is not None and reading.frustrated and self.turns - self._sorry_turn > 3:
            self._sorry_turn = self.turns
            return SORRY.text(lang) + " "
        return ""

    def _language_note(self, lang: str) -> str:
        if self.language.hindi_seen and not self._hindi_told:
            self._hindi_told = True
            return " " + BOTH_LANGUAGES.text(lang)
        return ""

    def _ask_back(self, name: str, args: Dict[str, Any], reading: Optional[Reading]) -> Optional[Say]:
        """Decision 5: a value the customer sounded unsure of is checked, not acted on."""
        if reading is None or not reading.unsure or name != "set_item":
            return None
        p = self.shop.product(args.get("item"))
        if p is None or args.get("qty") is None:
            return None
        try:
            qty = self.shop._to_product_unit(p, float(args["qty"]), args.get("unit"))
        except (TypeError, ValueError):
            return None
        if qty is None or qty <= 0:
            return None
        self.shop.state.pending = Pending("confirm_item", {"sku": p.sku, "qty": qty, "cut": args.get("cut")})
        return R.ask_back(p, qty)

    async def _gated(self, name: str, args: Dict[str, Any], hold: Optional[str], run: Callable[[], Result],
                     epoch: Optional[int]) -> Result:
        """Run an action through the commit gate. Reads don't wait."""
        if hold is not None:
            self.acted = True
        if self.coord is None or hold is None:
            return run()
        base = self.settings.order_hold_s if hold == "order" else self.settings.commit_hold_s

        async def call() -> Result:
            return run()

        record = await self.coord.execute(name, args, call, epoch=epoch, base_hold=base, once=name == "place_order")
        if record.outcome in ("ok", "cached") and isinstance(record.result, Result):
            return record.result
        log.warning("action %s failed: %s", name, record.error)
        return Result(False, "failed", MISUNDERSTOOD)

    def _note_item(self, results: Sequence[Result]) -> None:
        touched = [r.data.get("sku") for r in results if r.ok and r.data.get("sku")]
        self.recent_item = touched[-1] if touched else None

    async def _run_plan(self, plan: rules.Plan, words: str, lang: str, reading: Optional[Reading],
                        epoch: Optional[int]) -> AsyncIterator[Speech]:
        self.on_event("route", where="code", why=plan.route)
        if plan.silent:
            return
        if plan.route in MOVES_ON:
            self._let_question_lapse()
        if plan.resume:
            self.cut_rest = ""
        if plan.steps and len(plan.steps) >= 1:
            check = next((s for s in plan.steps if s.name == "set_item"), None)
            if check is not None:
                asked = self._ask_back(check.name, check.args, reading)
                if asked is not None:
                    self.on_event("tone_ask", text=asked.text(lang), cues=reading.cues if reading else [])
                    yield self._said(asked.text(lang), lang, "code")
                    return
        results: List[Result] = []
        for step in plan.steps:
            results.append(await self._gated(step.name, step.args, step.hold, step.run, epoch))
            self.on_event("tool", name=step.name, code=results[-1].code, ok=results[-1].ok)
        self._note_item(results)
        say = plan.say
        for r in results:
            say = say + r.say
        text = say.text(lang)
        if not text.strip():
            return
        if any(s.name == "place_order" and r.ok for s, r in zip(plan.steps, results)):
            await self._save_order()
        text = self._tone_prefix(reading, lang) + polish(text, self.spoken, max_sentences=4) + self._language_note(lang)
        yield self._said(text, lang, "control" if plan.route == "control" else "code")

    # --- the minds ---------------------------------------------------------------------------------------------
    def _candidates(self, words: str, limit: int = 5) -> List[Dict[str, Any]]:
        """The products a mind may need for this turn, so one call is usually enough."""
        shop = self.shop
        items = [m.product for m in shop.catalog.mentions(words)]
        extra = shop.catalog.search(text=words, stock=shop.stock, limit=limit, vegetarian=shop.state.needs.vegetarian)
        seen, out = set(), []
        for p in items + extra:
            if p.sku in seen:
                continue
            seen.add(p.sku)
            out.append({"item": p.en, "te": p.te, "price": p.price, "per": p.unit, "note": p.note_en,
                        "in_stock": shop.level(p.sku) > 0, "tags": [t for t in p.tags if t in ("veg", "non-veg", "bony", "few-bones")]})
            if len(out) >= limit:
                break
        return out

    def _note(self, reading: Optional[Reading]) -> str:
        parts = [self.heard_note]
        if reading is not None and reading.note():
            parts.append(reading.note())
        return " ".join(p for p in parts if p)

    async def _think(self, words: str, lang: str, reading: Optional[Reading], epoch: Optional[int]) -> AsyncIterator[Speech]:
        if self.router is None or not self.router.available:
            async for speech in self._without_models(words, lang, reading, epoch):
                yield speech
            return
        products = self._candidates(words)
        state = self.shop.snapshot()
        payload = prompts.turn_payload(words=words, reply_in=lang, state=state, products=products,
                                       recent=list(self.recent)[-5:-1], note=self._note(reading))
        try:
            completion = await self.router.complete("fast", prompts.messages(prompts.FAST_SYSTEM, payload), max_tokens=320,
                                                    temperature=0.2, timeout_s=self.settings.fast_timeout_s, est_tokens=1600)
        except AllBusy as exc:
            self.on_event("minds_busy", role="fast", why=str(exc)[:120])
            async for speech in self._without_models(words, lang, reading, epoch):
                yield speech
            return
        self.on_event("route", where="fast", why=completion.provider, seconds=round(completion.latency_s, 2))
        decision = Decision.parse(parse_json(completion.text))
        if decision is None:
            self.on_event("bad_json", provider=completion.provider, text=completion.text[:200])
            async for speech in self._without_models(words, lang, reading, epoch):
                yield speech
            return
        if decision.listen:
            self.on_event("listen", text=words)
            return
        if decision.consult:
            async for speech in self._consult(words, lang, reading, epoch, decision):
                yield speech
            return
        async for speech in self._act(decision, words, lang, reading, epoch, products, "fast"):
            yield speech

    def _let_question_lapse(self) -> None:
        """The customer moved on, so Ala's open question no longer stands: a later "yes" must not answer it. The read-back
        stays until the cart changes, because that's how an order is confirmed."""
        p = self.shop.state.pending
        if p is not None and p.kind != "order_review":
            self.shop.state.pending = None

    async def _act(self, decision: Decision, words: str, lang: str, reading: Optional[Reading], epoch: Optional[int],
                   products: Sequence[Dict[str, Any]], route: str, meals: Sequence[Dict[str, Any]] = ()) -> AsyncIterator[Speech]:
        results: List[Result] = []
        if decision.actions:
            self._let_question_lapse()
        for name, args in decision.actions:
            if name not in MODEL_ACTIONS:
                self.on_event("refused", name=name)
                continue
            asked = self._ask_back(name, args, reading)
            if asked is not None:
                yield self._said(asked.text(lang), lang, route)
                return
            hold = "order" if name == "cancel_order" else (
                None if name in ("show_products", "compare", "offer_substitutes", "plan_meal", "suggest_meals") else "edit")
            result = await self._gated(name, args, hold, lambda n=name, a=args: self.shop.run(n, a), epoch)
            results.append(result)
            self.on_event("tool", name=name, code=result.code, ok=result.ok)
        self._note_item(results)
        facts = Say()
        for r in results:
            facts = facts + r.say
        text = facts.text(lang)
        model_say = drop_invented_numbers(decision.say, allowed_numbers(self.shop, products, meals), words)
        failed = any(not r.ok for r in results)
        if not text:
            text = model_say
        elif model_say and not failed and model_say.rstrip().endswith("?") and not text.rstrip().endswith("?"):
            text = text + " " + model_say
        if not text.strip():
            if decision.actions:
                return
            return
        spoken = polish(text, self.spoken, max_sentences=3 if results else 2)
        yield self._said(self._tone_prefix(reading, lang) + spoken + self._language_note(lang), lang, route)

    async def _consult(self, words: str, lang: str, reading: Optional[Reading], epoch: Optional[int],
                       decision: Decision) -> AsyncIterator[Speech]:
        """The slow mind, covered by the fast mind's restatement if it carries information."""
        shop = self.shop
        n = shop.state.needs
        meals = []
        for r in shop.catalog.recipes:
            if n.vegetarian and r.diet != "veg":
                continue
            b = shop.build_basket(r, n.servings or 4, n.budget_inr)
            if b is not None:
                meals.append({"dish": b["dish_en"], "main": b["main"], "serves": b["servings"], "total": b["total"]})
        products = self._candidates(words, limit=12)
        payload = prompts.turn_payload(words=words, reply_in=lang, state=shop.snapshot(), products=products,
                                       recent=list(self.recent)[-7:-1], note=self._note(reading), meals=meals[:6])
        task = asyncio.ensure_future(self.router.complete("slow", prompts.messages(prompts.SLOW_SYSTEM, payload), max_tokens=500,
                                                          temperature=0.3, timeout_s=self.settings.slow_timeout_s, est_tokens=3500))
        try:
            bridge = polish(drop_invented_numbers(decision.say, allowed_numbers(shop, products, meals), words), self.spoken)
            done, _ = await asyncio.wait({task}, timeout=self.settings.bridge_after_s)
            if not done and bridge:
                yield Speech(bridge, lang, "slow", "bridge")
            try:
                completion = await task
            except AllBusy as exc:
                self.on_event("minds_busy", role="slow", why=str(exc)[:120])
                yield self._said(MINDS_DOWN.text(lang), lang, "slow")
                return
            self.on_event("route", where="slow", why=completion.provider, seconds=round(completion.latency_s, 2))
            answer = Decision.parse(parse_json(completion.text))
            if answer is None:
                self.on_event("bad_json", provider=completion.provider, text=completion.text[:200])
                yield self._said(MISUNDERSTOOD.text(lang), lang, "slow")
                return
            if answer.shown:
                found = [p for p in (shop.product(x) for x in answer.shown) if p is not None]
                if found:
                    shop.state.shown = [p.sku for p in found]
                    if shop.on_change is not None:
                        shop.on_change()
            async for speech in self._act(answer, words, lang, reading, epoch, products, "slow", meals):
                yield speech
        finally:
            if not task.done():
                task.cancel()

    async def _without_models(self, words: str, lang: str, reading: Optional[Reading], epoch: Optional[int]) -> AsyncIterator[Speech]:
        """Every model is out: take the rules' best reading of the sentence, and keep the shop running."""
        plan = rules.plan(words, self.shop, last_reply=self.last_reply, cut_rest=self.cut_rest, recent_item=self.recent_item,
                          lenient=True)
        if plan is None:
            yield self._said(MISUNDERSTOOD.text(lang), lang, "code")
            return
        async for speech in self._run_plan(plan, words, lang, reading, epoch):
            yield speech

    # --- memory ------------------------------------------------------------------------------------------------
    async def _persist(self) -> None:
        if self.store is None or not self.session_key:
            return
        try:
            await self.store.save_session(self.session_key, self.shop.state.to_dict())
        except Exception as exc:     # a lost save must never cost the conversation
            log.warning("could not save the session: %s", str(exc)[:120])

    async def _save_order(self) -> None:
        shop = self.shop
        if self.store is None or not shop.state.orders:
            return
        order = shop.state.orders[-1]
        try:
            await self.store.save_order(order.id + "-" + (self.session_key or "local"),
                                        {"id": order.id, "items": order.items, "total": order.total,
                                         "customer": shop.state.customer.name})
            for x in order.items:
                await self.store.set_stock(x["sku"], shop.level(x["sku"]))
            c = shop.state.customer
            if c.name:
                await self.store.save_profile(c.name, {"name": c.name, "usual": c.usual, "avoid": shop.state.needs.avoid,
                                                       "cut": c.cut})
        except Exception as exc:
            log.warning("could not save the order: %s", str(exc)[:120])

    async def _forget(self) -> None:
        c = self.shop.state.customer
        if self.store is not None and c.name:
            try:
                await self.store.forget_profile(c.name)
            except Exception as exc:
                log.warning("could not forget the profile: %s", str(exc)[:120])
        c.returning, c.usual, c.avoid, c.cut = False, [], [], {}
