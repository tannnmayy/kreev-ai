"""Everything the shop says about itself, in both languages.

Facts about the cart, prices and orders are worded here, by code, from the engine's own numbers. A model never states a
price or a total for these, so it can't invent one. Text keeps digits ("₹350") for the captions; the voice turns them
into words at the last moment (ala/language/speakable.py).

Telugu lines are drafts for native review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from ..language.numbers import say_quantity, show_money
from .catalog import Product


@dataclass(frozen=True)
class Say:
    """One thing to say, in both languages."""
    en: str = ""
    te: str = ""

    def text(self, lang: str) -> str:
        return (self.te if lang == "te" else self.en) or self.en or self.te

    def __bool__(self) -> bool:
        return bool(self.en or self.te)

    def __add__(self, other: "Say") -> "Say":
        return Say(" ".join(x for x in (self.en, other.en) if x), " ".join(x for x in (self.te, other.te) if x))


def _singular_en(name: str) -> str:
    if name.endswith("oes"):
        return name[:-2]
    return name[:-1] if name.endswith("s") else name


def _singular_te(name: str) -> str:
    return name[:-2] if name.endswith("లు") else name


def line_words(p: Product, qty: float, lang: str) -> str:
    """"half a kilo of prawns", "అర కిలో రొయ్యలు", "two lemons", "one dozen eggs"."""
    if p.unit == "piece":
        if lang == "te":
            from ..language.numbers import te_number
            return ("ఒక " + _singular_te(p.te)) if qty == 1 else "%s %s" % (te_number(int(qty)), p.te)
        from ..language.numbers import en_number
        return ("one " + _singular_en(p.en)) if qty == 1 else "%s %s" % (en_number(int(qty)), p.en)
    amount = say_quantity(qty, p.unit, lang)
    if lang == "te":
        return "%s %s" % (amount, p.te)
    if p.unit == "dozen":
        return "%s %s" % (amount, p.en)
    return "%s of %s" % (amount, p.en)


def join_list(parts: Sequence[str], lang: str) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    if lang == "te":
        return ", ".join(parts[:-1]) + ", " + parts[-1]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _capital(s: str) -> str:
    return s[:1].upper() + s[1:]


CUT_EN = {"whole": "whole", "cleaned": "cleaned", "curry cut": "curry cut", "steaks": "steaks", "boneless": "boneless"}
CUT_TE = {"whole": "మొత్తంగా", "cleaned": "శుభ్రం చేసి", "curry cut": "కూర ముక్కలుగా", "steaks": "ముక్కలుగా", "boneless": "బోన్‌లెస్"}


def item_set(p: Product, qty: float, price: float, cut: str = "") -> Say:
    money = show_money(price)
    en = "%s, %s." % (_capital(line_words(p, qty, "en")) + ((", " + CUT_EN.get(cut, cut)) if cut else ""), money)
    te = "%s%s, %s." % (line_words(p, qty, "te"), (" " + CUT_TE.get(cut, cut)) if cut else "", money)
    return Say(en, te)


def cut_set(p: Product, cut: str) -> Say:
    return Say("%s, %s." % (_capital(p.en), CUT_EN.get(cut, cut)), "%s %s ఇస్తాం." % (p.te, CUT_TE.get(cut, cut)))


def ask_cut(p: Product) -> Say:
    if "cleaned" in p.cuts and "whole" in p.cuts:
        return Say("Cleaned or whole?", "శుభ్రం చేసి ఇవ్వమంటారా, మొత్తంగానా?")
    options_en = " or ".join(CUT_EN.get(c, c) for c in p.cuts[:2])
    options_te = ", ".join(CUT_TE.get(c, c) for c in p.cuts[:2])
    return Say(_capital(options_en) + "?", options_te + "?")


def ask_how_much(p: Product) -> Say:
    return Say("%s How much would you like?" % unit_price(p).en, "%s ఎంత కావాలి?" % unit_price(p).te)


def ask_back(p: Product, qty: float) -> Say:
    """The customer sounded unsure of a value, so Ala checks it instead of acting on it."""
    return Say("%s, right?" % _capital(line_words(p, qty, "en")), "%s, కదా?" % line_words(p, qty, "te"))


def which_item(names_en: Sequence[str], names_te: Sequence[str]) -> Say:
    en = " or the ".join(names_en[:2])
    te = " లేదా ".join(names_te[:2])
    return Say("Which one, the %s?" % en, "%s, ఏది?" % te)


def removed(p: Product) -> Say:
    return Say("Removed the %s." % p.en, "%s తీసేశాను." % p.te)


def not_in_cart(p: Product) -> Say:
    return Say("There's no %s in your cart." % p.en, "మీ కార్ట్‌లో %s లేదు." % p.te)


def not_sold(name: str) -> Say:
    return Say("We don't sell %s here." % name if name else "We don't have that here.",
               "%s మా దగ్గర లేదు." % name if name else "అది మా దగ్గర లేదు.")


def cleared() -> Say:
    return Say("Your cart is empty now.", "కార్ట్ ఖాళీ చేశాను.")


def undone() -> Say:
    return Say("I've undone that.", "వెనక్కి తీసుకున్నాను.")


def nothing_to_undo() -> Say:
    return Say("There's nothing to undo.", "వెనక్కి తీసుకోవడానికి ఏమీ లేదు.")


def cart_empty() -> Say:
    return Say("Your cart is empty.", "మీ కార్ట్ ఖాళీగా ఉంది.")


def cart_read(lines_en: Sequence[str], lines_te: Sequence[str], total: float) -> Say:
    money = show_money(total)
    return Say("You have %s. The total is %s." % (join_list(lines_en, "en"), money),
               "మీ కార్ట్‌లో %s ఉన్నాయి. మొత్తం %s." % (join_list(lines_te, "te"), money))


def total_is(total: float) -> Say:
    money = show_money(total)
    return Say("The total is %s." % money, "మొత్తం %s." % money)


def budget_status(budget: int, total: float) -> Say:
    b, left = show_money(budget), show_money(abs(budget - total))
    if total > budget:
        return Say("Your budget is %s, and the cart is %s over." % (b, left), "మీ బడ్జెట్ %s, కార్ట్ %s ఎక్కువ అయింది." % (b, left))
    return Say("Your budget is %s, with %s left." % (b, left), "మీ బడ్జెట్ %s, ఇంకా %s మిగిలింది." % (b, left))


def no_budget() -> Say:
    return Say("You haven't given me a budget yet.", "మీరు ఇంకా బడ్జెట్ చెప్పలేదు.")


def budget_noted(budget: int, total: float) -> Say:
    b = show_money(budget)
    if total > budget:
        over = show_money(total - budget)
        return Say("Budget %s. The cart is %s over that." % (b, over), "బడ్జెట్ %s. కార్ట్ %s ఎక్కువగా ఉంది." % (b, over))
    return Say("Budget %s." % b, "బడ్జెట్ %s." % b)


def over_budget(over: float, budget: int) -> Say:
    return Say("That's %s over your %s budget." % (show_money(over), show_money(budget)),
               "మీ %s బడ్జెట్ కంటే %s ఎక్కువ అయింది." % (show_money(budget), show_money(over)))


def cheaper_swap(current: Product, swap: Product, saving: float) -> Say:
    return Say("Swap the %s for %s to save %s?" % (current.en, swap.en, show_money(saving)),
               "%s బదులు %s తీసుకుంటే %s తగ్గుతుంది. మార్చమంటారా?" % (current.te, swap.te, show_money(saving)))


def sold_out(p: Product) -> Say:
    return Say("%s is sold out today." % _capital(p.en), "ఈ రోజు %s అయిపోయింది." % p.te)


def offer_list(products: Iterable[Product]) -> Say:
    items = list(products)
    per = lambda p, lang: {"kg": " a kilo" if lang == "en" else " కిలో", "litre": " a litre" if lang == "en" else " లీటరు",
                           "dozen": " a dozen" if lang == "en" else " డజను"}.get(p.unit, "")
    en = ["%s at %s%s" % (p.en, show_money(p.price), per(p, "en")) for p in items]
    te = ["%s%s %s" % (p.te, per(p, "te"), show_money(p.price)) for p in items]
    return Say(_capital(join_list(en, "en")) + ".", join_list(te, "te") + ".")


def substitutes_offer(products: Sequence[Product]) -> Say:
    if not products:
        return Say("I don't have a close substitute today.", "ఈ రోజు దానికి దగ్గరగా ఏదీ లేదు.")
    which = Say("Which one would you like?", "ఏది కావాలి?") if len(products) > 1 else Say("Shall I add it?", "వేయమంటారా?")
    return Say("I can offer ", "బదులుగా ") + offer_list(products) + which


def short_stock(p: Product, available: float) -> Say:
    return Say("Only %s of %s left today. Shall I add that?" % (say_quantity(available, p.unit, "en"), p.en),
               "ఈ రోజు %s %s మాత్రమే ఉంది. అంత వేయమంటారా?" % (p.te, say_quantity(available, p.unit, "te")))


def implausible(p: Product, qty: float) -> Say:
    return Say("%s? That's a lot. Shall I add it?" % _capital(line_words(p, qty, "en")),
               "%s? చాలా ఎక్కువ. వేయమంటారా?" % line_words(p, qty, "te"))


def unit_mismatch(p: Product) -> Say:
    unit_en = {"kg": "by the kilo", "litre": "by the litre", "dozen": "by the dozen", "pack": "by the pack",
               "bunch": "by the bunch", "piece": "by the piece"}[p.unit]
    unit_te = {"kg": "కిలో లెక్కన", "litre": "లీటర్ లెక్కన", "dozen": "డజను లెక్కన", "pack": "ప్యాకెట్ లెక్కన",
               "bunch": "కట్ట లెక్కన", "piece": "ఒక్కొక్కటిగా"}[p.unit]
    return Say("We sell %s %s. How much would you like?" % (p.en, unit_en), "%s %s అమ్ముతాం. ఎంత కావాలి?" % (p.te, unit_te))


def rounded(p: Product, qty: float) -> Say:
    return Say("We sell it in steps, so I've made it %s." % say_quantity(qty, p.unit, "en"),
               "%s గా చేశాను." % say_quantity(qty, p.unit, "te"))


def diet_conflict(p: Product) -> Say:
    return Say("You said no fish or meat this visit, and %s isn't vegetarian. Add it anyway?" % p.en,
               "ఈ సారి నాన్-వెజ్ వద్దన్నారు, %s నాన్-వెజ్. అయినా వేయమంటారా?" % p.te)


def avoid_conflict(p: Product, why: str) -> Say:
    return Say("You wanted to avoid %s, and %s has them. Add it anyway?" % (why, p.en),
               "మీరు %s వద్దన్నారు, %s లో అవి ఉంటాయి. అయినా వేయమంటారా?" % (why, p.te))


def diet_noted() -> Say:
    return Say("No fish or meat for this visit.", "సరే, ఈ సారి నాన్-వెజ్ చెప్పను.")


def order_review(lines_en: List[str], lines_te: List[str], total: float, shop: dict) -> Say:
    money = show_money(total)
    return Say(
        "%s. Total %s, delivery %s, %s. Shall I place the order?" % (
            _capital(join_list(lines_en, "en")), money, shop["delivery_en"], shop["payment_en"]),
        "%s. మొత్తం %s. డెలివరీ %s, %s. ఆర్డర్ పెట్టమంటారా?" % (
            join_list(lines_te, "te"), money, shop["delivery_te"], shop["payment_te"]))


def order_empty() -> Say:
    return Say("Your cart is empty, so there's nothing to order yet.", "కార్ట్ ఖాళీగా ఉంది, ఇంకా ఆర్డర్ చేయడానికి ఏమీ లేదు.")


def order_placed(order_id: str, total: float, shop: dict) -> Say:
    money = show_money(total)
    return Say("Order %s is placed: %s, arriving %s." % (order_id, money, shop["delivery_en"]),
               "ఆర్డర్ %s పెట్టాను. మొత్తం %s, %s వస్తుంది." % (order_id, money, shop["delivery_te"]))


def order_needs_review() -> Say:
    return Say("Let me read it back first.", "ముందు ఒకసారి చదివి వినిపిస్తాను.")


def order_cancelled(order_id: str) -> Say:
    return Say("Order %s is cancelled." % order_id, "ఆర్డర్ %s రద్దు చేశాను." % order_id)


def no_order() -> Say:
    return Say("There's no order to cancel.", "రద్దు చేయడానికి ఆర్డర్ ఏమీ లేదు.")


def order_kept() -> Say:
    return Say("Not placed. What would you like to change?", "ఆర్డర్ పెట్టలేదు. ఏం మార్చమంటారు?")


def price_of(p: Product, qty: float, cost: float) -> Say:
    return Say("%s is %s." % (_capital(line_words(p, qty, "en")), show_money(cost)),
               "%s %s." % (line_words(p, qty, "te"), show_money(cost)))


def unit_price(p: Product) -> Say:
    per_en = {"kg": "a kilo", "litre": "a litre", "dozen": "a dozen", "pack": "a pack", "bunch": "a bunch", "piece": "each"}[p.unit]
    per_te = {"kg": "కిలో", "litre": "లీటరు", "dozen": "డజను", "pack": "ప్యాకెట్", "bunch": "కట్ట", "piece": "ఒక్కటి"}[p.unit]
    return Say("%s is %s %s." % (_capital(p.en), show_money(p.price), per_en), "%s %s %s." % (p.te, per_te, show_money(p.price)))


def in_stock(p: Product) -> Say:
    return unit_price(p) + Say(_capital(p.note_en) + ".", p.note_te + ".")


def compare(products: Sequence[Product]) -> Say:
    out = Say()
    for p in products:
        out = out + unit_price(p) + Say(_capital(p.note_en) + ".", p.note_te + ".")
    return out


def basket_offer(dish_en: str, dish_te: str, servings: int, lines_en: Sequence[str], lines_te: Sequence[str], total: float) -> Say:
    money = show_money(total)
    return Say("For %s for %d: %s. That's %s. Shall I add it all?" % (dish_en, servings, join_list(lines_en, "en"), money),
               "%d మందికి %s కోసం %s. మొత్తం %s. అన్నీ వేయమంటారా?" % (servings, dish_te, join_list(lines_te, "te"), money))


def basket_added(n: int, total: float) -> Say:
    money = show_money(total)
    return Say("Added %d items. The cart is %s." % (n, money), "%d వస్తువులు వేశాను. కార్ట్ మొత్తం %s." % (n, money))


def meal_options(options: Sequence[dict], servings: int) -> Say:
    en = ["%s with %s at %s" % (o["dish_en"], o["main_en"], show_money(o["total"])) if o.get("main_en") else
          "%s at %s" % (o["dish_en"], show_money(o["total"])) for o in options]
    te = ["%s %s" % (o["dish_te"], show_money(o["total"])) for o in options]
    return Say("For %d people: %s. Which one?" % (servings, join_list(en, "en")),
               "%d మందికి: %s. ఏది కావాలి?" % (servings, join_list(te, "te")))


# policy answers
def delivery_info(shop: dict) -> Say:
    fee = Say("Delivery is free.", "డెలివరీ ఉచితం.")
    return Say("Delivery is %s." % shop["delivery_en"], "డెలివరీ %s." % shop["delivery_te"]) + fee


def payment_info(shop: dict) -> Say:
    return Say("Payment is %s. I never take card details." % shop["payment_en"], "పేమెంట్ %s. కార్డ్ వివరాలు తీసుకోను." % shop["payment_te"])


def returns_info(shop: dict) -> Say:
    return Say(shop["returns_en"], shop["returns_te"])
