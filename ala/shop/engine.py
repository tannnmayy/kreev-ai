"""The shop engine: every change to the cart and every order goes through here, and no model runs here.

The engine is plain synchronous code over plain data, so each rule can be tested on its own:
- prices, stock, totals and budgets come only from the catalog and the cart;
- cart edits set values rather than adding, so a late correction converges;
- a budget is never exceeded silently, and an implausible quantity is asked about first;
- an order takes two steps, and only code can take the second: `place_order` is not an action a model can call.

Each tool returns a Result whose `say` is the true statement of what happened, in both languages.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..language.numbers import show_money, show_quantity
from . import replies as R
from .catalog import Catalog, Product, Recipe
from .replies import Say
from .state import CartLine, Order, Pending, SessionState

_FLOAT_EPS = 1e-6

# What the minds may ask for. place_order is absent on purpose.
MODEL_ACTIONS = ("set_item", "change_item", "remove_item", "set_cut", "clear_cart", "undo", "set_needs", "add_standing",
                 "show_products", "compare", "offer_substitutes", "plan_meal", "suggest_meals", "review_order",
                 "repeat_last_order", "cancel_order")


@dataclass
class Result:
    ok: bool
    code: str = "ok"
    say: Say = field(default_factory=Say)
    data: Dict[str, Any] = field(default_factory=dict)


class ShopEngine:
    def __init__(self, catalog: Catalog, state: Optional[SessionState] = None, stock: Optional[Dict[str, float]] = None,
                 on_change: Optional[Callable[[], None]] = None) -> None:
        self.catalog = catalog
        self.state = state or SessionState()
        self.stock: Dict[str, float] = dict(stock or {})
        self.on_change = on_change
        self._undo: List[List[CartLine]] = []

    # --- reading the state -------------------------------------------------------------------------------------
    def level(self, sku: str) -> float:
        return self.stock.get(sku, self.catalog.by_sku[sku].stock)

    def line(self, sku: str) -> Optional[CartLine]:
        return next((x for x in self.state.cart if x.sku == sku), None)

    def line_price(self, line: CartLine) -> float:
        return self.catalog.by_sku[line.sku].price * line.qty

    @property
    def total(self) -> float:
        return sum(self.line_price(x) for x in self.state.cart)

    def product(self, ref: Any) -> Optional[Product]:
        """A product from what a mind or the rules call it: a number in the list shown, a sku or a name."""
        if isinstance(ref, (int, float)) and not isinstance(ref, bool):
            return self._shown_at(int(ref))
        text = str(ref or "").strip()
        if text.isdigit():
            return self._shown_at(int(text))
        return self.catalog.find(text)

    def _shown_at(self, n: int) -> Optional[Product]:
        shown = self.state.shown
        if not shown:
            return None
        i = len(shown) - 1 if n == -1 else n - 1
        return self.catalog.by_sku.get(shown[i]) if 0 <= i < len(shown) else None

    def cheapest_shown(self, dearest: bool = False) -> Optional[Product]:
        items = [self.catalog.by_sku[s] for s in self.state.shown if s in self.catalog.by_sku]
        if not items:
            return None
        return (max if dearest else min)(items, key=lambda p: p.price)

    def _lines_words(self, lang: str) -> List[str]:
        out = []
        for x in self.state.cart:
            p = self.catalog.by_sku[x.sku]
            words = R.line_words(p, x.qty, lang)
            if x.cut:
                words += (", " + R.CUT_EN.get(x.cut, x.cut)) if lang == "en" else (" " + R.CUT_TE.get(x.cut, x.cut))
            out.append(words)
        return out

    def read_cart(self) -> Result:
        if not self.state.cart:
            return Result(True, "empty", R.cart_empty())
        return Result(True, say=R.cart_read(self._lines_words("en"), self._lines_words("te"), self.total))

    def read_total(self) -> Result:
        if not self.state.cart:
            return Result(True, "empty", R.cart_empty())
        return Result(True, say=R.total_is(self.total))

    def read_budget(self) -> Result:
        b = self.state.needs.budget_inr
        if b is None:
            return Result(True, "no_budget", R.no_budget())
        return Result(True, say=R.budget_status(b, self.total))

    def price_of(self, ref: Any, qty: Optional[float] = None) -> Result:
        p = self.product(ref)
        if p is None:
            return Result(False, "not_found", R.not_sold(str(ref)))
        if self.level(p.sku) <= 0:
            return self._sold_out(p)
        if qty:
            return Result(True, say=R.price_of(p, qty, p.price * qty))
        return Result(True, say=R.unit_price(p))

    # --- guards ------------------------------------------------------------------------------------------------
    def _changed(self) -> None:
        """The cart moved: a read-back that described the old cart is no longer an offer anyone can accept."""
        if self.state.pending is not None and self.state.pending.kind == "order_review":
            self.state.pending = None
        if self.on_change is not None:
            self.on_change()

    def _remember(self) -> None:
        self._undo.append(copy.deepcopy(self.state.cart))
        del self._undo[:-20]

    def _avoid_reason(self, p: Product) -> Optional[str]:
        for word in self.state.needs.avoid + self.state.customer.avoid:
            w = word.lower().strip()
            if not w:
                continue
            if w in p.tags or w.rstrip("s") in p.tags or (w in ("bones", "bony fish", "bony") and "bony" in p.tags):
                return "bones" if "bon" in w else w
            named = self.catalog.find(w)
            if named is not None and named.sku == p.sku:
                return p.en
        return None

    def _sold_out(self, p: Product, max_price: Optional[float] = None) -> Result:
        subs = self.catalog.substitutes(p.sku, max_price=max_price, stock=self.stock)
        self.state.shown = [s.sku for s in subs]
        self.state.pending = Pending("substitute", {"for": p.sku}) if subs else None
        return Result(False, "sold_out", R.sold_out(p) + R.substitutes_offer(subs), {"substitutes": [s.sku for s in subs]})

    @staticmethod
    def _to_product_unit(p: Product, qty: float, unit: Optional[str]) -> Optional[float]:
        """The quantity in the unit the product is sold in, or None when the two can't be compared."""
        if unit in (None, p.unit):
            return qty
        if unit == "g" and p.unit == "kg":
            return qty / 1000.0
        if unit == "ml" and p.unit == "litre":
            return qty / 1000.0
        if unit == "piece" and p.unit == "dozen":
            return qty / 12.0
        if unit == "dozen" and p.unit == "piece":
            return qty * 12.0
        return None

    @staticmethod
    def _round_to_step(p: Product, qty: float) -> float:
        steps = max(1, round(qty / p.step + _FLOAT_EPS))
        return round(steps * p.step, 3)

    # --- changing the cart -------------------------------------------------------------------------------------
    def set_item(self, item: Any, qty: float, unit: Optional[str] = None, cut: Optional[str] = None,
                 force: bool = False) -> Result:
        """Make the cart hold exactly this much of this product."""
        p = self.product(item)
        if p is None:
            return Result(False, "not_found", R.not_sold(str(item) if not str(item).isdigit() else ""))
        if self.level(p.sku) <= 0:
            return self._sold_out(p)
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            return Result(False, "bad_quantity", R.unit_mismatch(p))
        if qty <= 0:
            return self.remove_item(p.sku)
        wanted = self._to_product_unit(p, qty, unit)
        if wanted is None:
            return Result(False, "unit_mismatch", R.unit_mismatch(p))
        snapped = self._round_to_step(p, wanted)
        if not force:
            if self.state.needs.vegetarian and not p.vegetarian:
                self.state.pending = Pending("confirm_item", {"sku": p.sku, "qty": snapped, "cut": cut})
                return Result(False, "diet_conflict", R.diet_conflict(p))
            why = self._avoid_reason(p)
            if why:
                self.state.pending = Pending("confirm_item", {"sku": p.sku, "qty": snapped, "cut": cut})
                return Result(False, "avoid_conflict", R.avoid_conflict(p, why))
            if snapped > p.max_qty:
                self.state.pending = Pending("confirm_item", {"sku": p.sku, "qty": snapped, "cut": cut})
                return Result(False, "implausible", R.implausible(p, snapped))
        available = self.level(p.sku)
        if snapped > available + _FLOAT_EPS:
            self.state.pending = Pending("confirm_item", {"sku": p.sku, "qty": available, "cut": cut})
            return Result(False, "short_stock", R.short_stock(p, available), {"available": available})
        self._remember()
        line = self.line(p.sku)
        if line is None:
            line = CartLine(p.sku, snapped)
            self.state.cart.append(line)
        else:
            line.qty = snapped
        ask = Say()
        if p.cuts:
            ask = self._settle_cut(p, line, cut)
        elif cut:
            cut = None
        self._changed()
        say = R.item_set(p, snapped, p.price * snapped, line.cut or "")
        if abs(snapped - wanted) > _FLOAT_EPS:
            say = R.rounded(p, snapped) + say
        budget = self._budget_note(p)
        return Result(True, say=say + budget + (ask if not budget else Say()), data={"sku": p.sku, "qty": snapped})

    def _settle_cut(self, p: Product, line: CartLine, cut: Optional[str]) -> Say:
        s = self.state
        if cut in p.cuts:
            line.cut = cut
            s.cut_default = cut
            return Say()
        if line.cut:
            return Say()
        usual = s.customer.cut.get(p.group) or s.cut_default
        if usual in p.cuts:
            line.cut = usual
            return Say()
        if s.cut_asked or any("confirm" in x.lower() or "question" in x.lower() for x in s.standing):
            line.cut = "cleaned" if "cleaned" in p.cuts else p.cuts[0]
            return Say()
        s.cut_asked = True
        s.pending = Pending("cut", {"sku": p.sku})
        return R.ask_cut(p)

    def _budget_note(self, changed: Optional[Product] = None) -> Say:
        b = self.state.needs.budget_inr
        if b is None or self.total <= b:
            return Say()
        over = self.total - b
        note = R.over_budget(over, b)
        if self.state.needs.budget_flexible or changed is None:
            return note
        line = self.line(changed.sku)
        if line is None:
            return note
        # only the same kind of thing: nobody swaps rice for semolina to save money
        cheaper = [c for c in self.catalog.substitutes(changed.sku, stock=self.stock, limit=6)
                   if c.group == changed.group and c.unit == changed.unit and c.price < changed.price
                   and self._avoid_reason(c) is None]
        if self.state.needs.vegetarian:
            cheaper = [c for c in cheaper if c.vegetarian]
        for c in cheaper:
            saving = (changed.price - c.price) * line.qty
            if saving >= over:
                self.state.pending = Pending("swap", {"from": changed.sku, "to": c.sku})
                return note + R.cheaper_swap(changed, c, saving)
        if cheaper:
            c = cheaper[-1]
            self.state.pending = Pending("swap", {"from": changed.sku, "to": c.sku})
            return note + R.cheaper_swap(changed, c, (changed.price - c.price) * line.qty)
        return note

    def change_item(self, item: Any, delta: float, unit: Optional[str] = None) -> Result:
        """Add to or take from what's in the cart: "two more", "one kilo less"."""
        p = self.product(item)
        if p is None:
            return Result(False, "not_found", R.not_sold(str(item)))
        d = self._to_product_unit(p, float(delta), unit)
        if d is None:
            return Result(False, "unit_mismatch", R.unit_mismatch(p))
        line = self.line(p.sku)
        return self.set_item(p.sku, round((line.qty if line else 0.0) + d, 3))

    def remove_item(self, item: Any) -> Result:
        p = self.product(item)
        if p is None:
            return Result(False, "not_found", R.not_sold(str(item)))
        line = self.line(p.sku)
        if line is None:
            return Result(False, "not_in_cart", R.not_in_cart(p))
        self._remember()
        self.state.cart.remove(line)
        self._changed()
        return Result(True, say=R.removed(p), data={"sku": p.sku})

    def set_cut(self, item: Any, cut: str) -> Result:
        p = self.product(item)
        if p is None:
            return Result(False, "not_found", R.not_sold(str(item)))
        line = self.line(p.sku)
        if line is None:
            return Result(False, "not_in_cart", R.not_in_cart(p))
        if cut not in p.cuts:
            return Result(False, "bad_cut", R.ask_cut(p))
        self._remember()
        line.cut = cut
        self.state.cut_default = cut
        if self.state.pending is not None and self.state.pending.kind == "cut":
            self.state.pending = None
        self._changed()
        return Result(True, say=R.cut_set(p, cut), data={"sku": p.sku, "cut": cut})

    def ask_quantity(self, item: Any) -> Result:
        """A product was named with no amount: say its price and ask."""
        p = self.product(item)
        if p is None:
            return Result(False, "not_found", R.not_sold(str(item)))
        if self.level(p.sku) <= 0:
            return self._sold_out(p)
        self.state.shown = [p.sku]
        self.state.pending = Pending("quantity", {"sku": p.sku})
        return Result(True, "asked", R.ask_how_much(p))

    def clear_cart(self) -> Result:
        if not self.state.cart:
            return Result(True, "empty", R.cart_empty())
        self._remember()
        self.state.cart = []
        self._changed()
        return Result(True, say=R.cleared())

    def undo(self) -> Result:
        if not self._undo:
            return Result(False, "nothing", R.nothing_to_undo())
        self.state.cart = self._undo.pop()
        self._changed()
        return Result(True, say=R.undone() + self.read_cart().say)

    # --- what the customer told us -----------------------------------------------------------------------------
    def set_needs(self, budget_inr: Optional[float] = None, servings: Optional[int] = None, occasion: Optional[str] = None,
                  vegetarian: Optional[bool] = None, avoid: Optional[Sequence[str]] = None,
                  budget_flexible: Optional[bool] = None) -> Result:
        n = self.state.needs
        say = Say()
        if budget_inr is not None:
            n.budget_inr = int(budget_inr)
            say = say + R.budget_noted(n.budget_inr, self.total)
        if servings is not None:
            n.servings = int(servings)
        if occasion:
            n.occasion = str(occasion)[:80]
        if budget_flexible is not None:
            n.budget_flexible = bool(budget_flexible)
        if vegetarian is not None:
            if bool(vegetarian) and not n.vegetarian:
                say = say + R.diet_noted()
            n.vegetarian = bool(vegetarian)
        if avoid:
            for word in avoid:
                w = str(word).strip().lower()
                if w and w not in n.avoid:
                    n.avoid.append(w)
        return Result(True, say=say)

    def add_standing(self, text: str) -> Result:
        t = " ".join(str(text or "").split())[:120]
        if t and t not in self.state.standing:
            self.state.standing.append(t)
        return Result(True)

    # --- browsing ----------------------------------------------------------------------------------------------
    def _diet_filter(self) -> Dict[str, Any]:
        avoid_tags = [w for w in self.state.needs.avoid + self.state.customer.avoid if w in ("bony", "non-veg", "premium")]
        if any("bon" in w for w in self.state.needs.avoid + self.state.customer.avoid):
            avoid_tags.append("bony")
        return {"vegetarian": self.state.needs.vegetarian, "avoid_tags": avoid_tags}

    def show_products(self, query: str = "", category: Optional[str] = None, tags: Optional[Sequence[str]] = None,
                      max_price: Optional[float] = None, limit: int = 3) -> Result:
        """Find products and number them for the customer. Named products lead; a list stays short enough to say."""
        kwargs = self._diet_filter()
        found = self.catalog.search(text=query, category=category, tags=tags or (), max_price=max_price, stock=self.stock,
                                    limit=max(1, min(int(limit or 3), 3)), **kwargs)
        if not found and query:
            named = self.catalog.mentions(query)
            if named:
                p = named[0].product
                if self.level(p.sku) <= 0:
                    return self._sold_out(p)
        if not found:
            return Result(False, "nothing_found", R.not_sold(query))
        self.state.shown = [p.sku for p in found]
        return Result(True, say=R.offer_list(found) + (Say("Which one?", "ఏది కావాలి?") if len(found) > 1 else Say()),
                      data={"skus": self.state.shown})

    def compare(self, items: Sequence[Any]) -> Result:
        products = [p for p in (self.product(i) for i in items) if p is not None]
        if len(products) < 2:
            products = [self.catalog.by_sku[s] for s in self.state.shown[:3] if s in self.catalog.by_sku]
        if len(products) < 2:
            return Result(False, "need_two", Say("Which two shall I compare?", "ఏ రెండింటిని పోల్చమంటారు?"))
        self.state.shown = [p.sku for p in products[:3]]
        return Result(True, say=R.compare(products[:3]), data={"skus": self.state.shown})

    def offer_substitutes(self, item: Any, max_price: Optional[float] = None) -> Result:
        p = self.product(item)
        if p is None:
            return Result(False, "not_found", R.not_sold(str(item)))
        subs = self.catalog.substitutes(p.sku, max_price=max_price, stock=self.stock)
        if self.state.needs.vegetarian:
            subs = [s for s in subs if s.vegetarian]
        self.state.shown = [s.sku for s in subs]
        return Result(bool(subs), "ok" if subs else "none", R.substitutes_offer(subs), {"skus": self.state.shown})

    # --- meals -------------------------------------------------------------------------------------------------
    def _main_candidates(self, recipe: Recipe) -> List[Product]:
        main = recipe.main
        if "sku" in main:
            items = [self.catalog.by_sku[main["sku"]]]
        else:
            groups = list(main["group"])
            items = sorted((p for p in self.catalog.products if p.group in groups),
                           key=lambda p: (groups.index(p.group), p.price))
        return [p for p in items if self.level(p.sku) > 0 and self._avoid_reason(p) is None]

    def _scaled(self, sku: str, qty: float, scale: float) -> Tuple[Product, float]:
        p = self.catalog.by_sku[sku]
        if p.unit in ("kg", "litre"):
            return p, self._round_to_step(p, qty * scale)
        return p, float(max(1, math.ceil(qty * max(scale, 1.0) - _FLOAT_EPS)))

    def build_basket(self, recipe: Recipe, servings: int, budget: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """What a dish needs for this many people, priced. Without a budget the first fitting main is used; with one,
        the best main that keeps the whole basket inside it."""
        scale = servings / float(recipe.serves)
        mains = self._main_candidates(recipe)
        if not mains:
            return None
        extras = [self._scaled(i["sku"], i["qty"], scale) for i in recipe.items]
        extras = [(p, q) for p, q in extras if self.level(p.sku) >= q]
        extra_cost = sum(p.price * q for p, q in extras)
        choice = None
        order = sorted(mains, key=lambda p: -p.price) if budget is not None else mains
        for p in order:
            q = self._round_to_step(p, float(recipe.main["qty"]) * scale)
            if self.level(p.sku) < q:
                continue
            if budget is None or p.price * q + extra_cost <= budget:
                choice = (p, q)
                break
        if choice is None:
            if budget is not None:
                return None
            return None
        lines = [choice] + extras
        return {"recipe": recipe.id, "dish_en": recipe.en, "dish_te": recipe.te, "servings": servings,
                "main": choice[0].sku, "main_en": choice[0].en if "group" in recipe.main else "",
                "lines": [{"sku": p.sku, "qty": q} for p, q in lines],
                "total": int(round(sum(p.price * q for p, q in lines)))}

    def plan_meal(self, dish: str, servings: Optional[int] = None, budget: Optional[float] = None) -> Result:
        recipe = self.catalog.recipe(dish)
        if recipe is None:
            return Result(False, "unknown_dish", Say("I don't have a list for that dish yet.", "ఆ వంటకి లిస్ట్ ఇంకా లేదు."))
        if self.state.needs.vegetarian and recipe.diet != "veg":
            return Result(False, "diet_conflict", R.diet_noted())
        n = int(servings or self.state.needs.servings or recipe.serves)
        limit = budget if budget is not None else self.state.needs.budget_inr
        basket = self.build_basket(recipe, n, limit)
        if basket is None:
            return Result(False, "over_budget", Say("That won't fit the budget. Shall I suggest something cheaper?",
                                                    "అది బడ్జెట్‌లో రాదు. తక్కువలో ఏదైనా చెప్పమంటారా?"))
        lines_en, lines_te = [], []
        for x in basket["lines"]:
            p = self.catalog.by_sku[x["sku"]]
            lines_en.append(R.line_words(p, x["qty"], "en"))
            lines_te.append(R.line_words(p, x["qty"], "te"))
        self.state.shown = [basket["main"]]
        self.state.pending = Pending("basket", basket)
        say = R.basket_offer(recipe.en, recipe.te, n, lines_en[:4], lines_te[:4], basket["total"])
        return Result(True, say=say, data=basket)

    def suggest_meals(self, servings: Optional[int] = None, budget: Optional[float] = None,
                      vegetarian: Optional[bool] = None, limit: int = 2) -> Result:
        """Dishes that fit, with true totals, so nobody has to do arithmetic in their head or in a model."""
        n = int(servings or self.state.needs.servings or 4)
        cap = budget if budget is not None else self.state.needs.budget_inr
        veg = self.state.needs.vegetarian if vegetarian is None else bool(vegetarian)
        options = []
        for r in self.catalog.recipes:
            if veg and r.diet != "veg":
                continue
            basket = self.build_basket(r, n, cap)
            if basket is not None:
                options.append(basket)
        if not options:
            return Result(False, "nothing_fits", Say("Nothing fits that budget yet. Could you stretch it a little?",
                                                     "ఆ బడ్జెట్‌లో ఏదీ రావట్లేదు. కొంచెం పెంచగలరా?"))
        # the fish first (it's a fish market), then the rest, most filling first
        options.sort(key=lambda o: (self.catalog.by_sku[o["main"]].cat != "fish", -o["total"]))
        chosen = options[: max(1, min(int(limit or 2), 3))]
        self.state.pending = Pending("meal_choice", {"options": chosen})
        self.state.shown = [o["main"] for o in chosen]
        return Result(True, say=R.meal_options(chosen, n), data={"options": chosen})

    def add_basket(self, basket: Dict[str, Any]) -> Result:
        self._remember()
        added = 0
        for x in basket["lines"]:
            p = self.catalog.by_sku[x["sku"]]
            line = self.line(p.sku)
            if line is None:
                line = CartLine(p.sku, x["qty"])
                self.state.cart.append(line)
                if p.cuts:
                    line.cut = self.state.cut_default or ("cleaned" if "cleaned" in p.cuts else p.cuts[0])
            else:
                line.qty = max(line.qty, x["qty"])
            added += 1
        self.state.pending = None
        self._changed()
        return Result(True, say=R.basket_added(added, self.total) + self._budget_note(), data={"added": added})

    # --- the order, in two steps -------------------------------------------------------------------------------
    def _hash(self) -> str:
        lines = sorted((x.sku, round(x.qty, 3), x.cut or "", self.catalog.by_sku[x.sku].price) for x in self.state.cart)
        raw = json.dumps([lines, len(self.state.orders)], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]

    def review_order(self) -> Result:
        """Step one: read the order back. The answer has to be to this exact read-back."""
        if not self.state.cart:
            return Result(False, "empty", R.order_empty())
        short = [x for x in self.state.cart if self.level(x.sku) < x.qty - _FLOAT_EPS]
        if short:
            p = self.catalog.by_sku[short[0].sku]
            if self.level(p.sku) <= 0:
                self._remember()
                self.state.cart.remove(short[0])
                self._changed()
                return self._sold_out(p)
            return Result(False, "short_stock", R.short_stock(p, self.level(p.sku)))
        words = R.order_review(self._lines_words("en"), self._lines_words("te"), self.total, self.catalog.shop)
        self.state.pending = Pending("order_review", {"text_en": words.en, "text_te": words.te}, self._hash(), True)
        if self.on_change is not None:
            self.on_change()
        return Result(True, say=words, data={"hash": self.state.pending.summary_hash})

    def readback_cut_short(self) -> None:
        """The customer talked over the read-back, so a yes can't be to something they heard to the end."""
        if self.state.pending is not None and self.state.pending.kind == "order_review":
            self.state.pending.heard_complete = False

    def place_order(self) -> Result:
        """Step two. Only the code that heard an explicit yes calls this; no model can."""
        pending = self.state.pending
        if pending is None or pending.kind != "order_review":
            if not self.state.cart:
                return Result(False, "empty", R.order_empty())
            return Result(False, "no_review", R.order_needs_review() + self.review_order().say)
        if pending.summary_hash != self._hash():
            return Result(False, "stale_review", self.review_order().say)
        if not pending.heard_complete:
            return Result(False, "readback_cut", self.review_order().say)
        items = [{"sku": x.sku, "qty": x.qty, "cut": x.cut, "price": self.catalog.by_sku[x.sku].price} for x in self.state.cart]
        total = int(round(self.total))
        order = Order(id="%04d" % (int(pending.summary_hash, 16) % 9000 + 1000), items=items, total=total,
                      summary_hash=pending.summary_hash)
        for x in self.state.cart:
            self.stock[x.sku] = self.level(x.sku) - x.qty
        self.state.orders.append(order)
        self.state.customer.usual = [{"sku": x["sku"], "qty": x["qty"], "cut": x["cut"]} for x in items]
        self.state.cart = []
        self.state.pending = None
        self._undo.clear()
        if self.on_change is not None:
            self.on_change()
        return Result(True, say=R.order_placed(order.id, total, self.catalog.shop), data={"order": order.id, "total": total})

    def cancel_order(self) -> Result:
        live = [o for o in self.state.orders if o.status == "placed"]
        if not live:
            return Result(False, "no_order", R.no_order())
        order = live[-1]
        order.status = "cancelled"
        for x in order.items:
            self.stock[x["sku"]] = self.level(x["sku"]) + x["qty"]
        if self.on_change is not None:
            self.on_change()
        return Result(True, say=R.order_cancelled(order.id), data={"order": order.id})

    def repeat_last_order(self) -> Result:
        usual = self.state.customer.usual
        if not usual:
            return Result(False, "no_history", Say("I don't have an earlier order for you.", "మీ పాత ఆర్డర్ నా దగ్గర లేదు."))
        self._remember()
        for x in usual:
            if x["sku"] not in self.catalog.by_sku or self.level(x["sku"]) <= 0:
                continue
            line = self.line(x["sku"])
            if line is None:
                self.state.cart.append(CartLine(x["sku"], x["qty"], x.get("cut")))
            else:
                line.qty = x["qty"]
        self._changed()
        return Result(True, say=self.read_cart().say)

    # --- answering what Ala asked ------------------------------------------------------------------------------
    def accept_pending(self) -> Optional[Result]:
        """The customer said yes to Ala's open question. None when there's nothing a yes can settle here."""
        p = self.state.pending
        if p is None:
            return None
        if p.kind == "order_review":
            return self.place_order()
        self.state.pending = None
        if p.kind == "confirm_item":
            return self.set_item(p.data["sku"], p.data["qty"], cut=p.data.get("cut"), force=True)
        if p.kind == "basket":
            return self.add_basket(p.data)
        if p.kind == "swap":
            old = self.line(p.data["from"])
            if old is None:
                return None
            qty, cut = old.qty, old.cut
            self.remove_item(p.data["from"])
            result = self.set_item(p.data["to"], qty, cut=cut, force=True)
            return result
        if p.kind == "substitute" and len(self.state.shown) == 1:
            return self.set_item(self.state.shown[0], 1)
        self.state.pending = p
        return None

    def decline_pending(self) -> Optional[Result]:
        p = self.state.pending
        if p is None:
            return None
        self.state.pending = None
        if p.kind == "order_review":
            return Result(True, say=R.order_kept())
        return Result(True, say=Say("Okay, leaving it out.", "సరే, వేయట్లేదు.") if p.kind == "confirm_item" else Say())

    # --- running a mind's action -------------------------------------------------------------------------------
    def run(self, name: str, args: Optional[Dict[str, Any]] = None) -> Result:
        """Run an action a mind asked for. Unknown actions, place_order among them, are refused."""
        if name not in MODEL_ACTIONS:
            return Result(False, "refused", data={"action": name})
        fn = getattr(self, name)
        try:
            return fn(**(args or {}))
        except TypeError as exc:
            return Result(False, "bad_args", data={"action": name, "why": str(exc)[:120]})

    # --- views -------------------------------------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """The compact state a mind sees on every turn."""
        s, cat = self.state, self.catalog
        n = s.needs
        out: Dict[str, Any] = {}
        if s.customer.name:
            out["customer"] = {"name": s.customer.name, "returning": s.customer.returning}
            if s.customer.usual:
                out["customer"]["usual"] = [{"item": cat.by_sku[x["sku"]].en, "qty": x["qty"]} for x in s.customer.usual
                                            if x["sku"] in cat.by_sku]
        needs = {k: v for k, v in (("occasion", n.occasion), ("servings", n.servings), ("budget_inr", n.budget_inr),
                                   ("vegetarian", n.vegetarian or None), ("avoid", (n.avoid + s.customer.avoid) or None)) if v}
        if needs:
            out["needs"] = needs
        if s.shown:
            out["shown"] = [{"n": i + 1, "item": cat.by_sku[k].en, "price": cat.by_sku[k].price, "per": cat.by_sku[k].unit}
                            for i, k in enumerate(s.shown) if k in cat.by_sku]
        if s.cart:
            out["cart"] = [{"item": cat.by_sku[x.sku].en, "qty": x.qty, "unit": cat.by_sku[x.sku].unit, "cut": x.cut,
                            "cost": round(self.line_price(x))} for x in s.cart]
            out["total"] = round(self.total)
        if n.budget_inr is not None:
            out["budget_left"] = round(n.budget_inr - self.total)
        if s.standing:
            out["standing"] = s.standing
        if s.pending is not None:
            out["waiting_for_answer_to"] = s.pending.kind
        return out

    def view(self) -> Dict[str, Any]:
        """What the screen shows: the same data, shaped for display."""
        s, cat = self.state, self.catalog
        cart = []
        for x in s.cart:
            p = cat.by_sku[x.sku]
            cart.append({"sku": x.sku, "en": p.en, "te": p.te, "qty": x.qty, "qty_text": show_quantity(x.qty, p.unit),
                         "cut": x.cut, "price": p.price, "cost": round(self.line_price(x))})
        shown = []
        for i, k in enumerate(s.shown):
            p = cat.by_sku.get(k)
            if p is not None:
                shown.append({"n": i + 1, "sku": k, "en": p.en, "te": p.te, "price": p.price, "price_text": show_money(p.price),
                              "unit": p.unit, "note_en": p.note_en, "note_te": p.note_te, "seller": p.seller,
                              "sold_out": self.level(k) <= 0})
        review = None
        if s.pending is not None and s.pending.kind == "order_review":
            review = {"en": s.pending.data.get("text_en"), "te": s.pending.data.get("text_te")}
        return {"cart": cart, "total": round(self.total), "total_text": show_money(self.total), "budget": s.needs.budget_inr,
                "shown": shown, "review": review, "pending": s.pending.kind if s.pending else None,
                "orders": [{"id": o.id, "total": o.total, "status": o.status} for o in s.orders],
                "needs": {"servings": s.needs.servings, "occasion": s.needs.occasion, "vegetarian": s.needs.vegetarian,
                          "avoid": s.needs.avoid},
                "standing": s.standing}
