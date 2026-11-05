"""The catalog: what the shop sells, and how customers and speech recognisers name it.

Names are the hard part. The same fish can arrive as "వంజరం", "vanjaram", "seer fish" or, from a speech recogniser,
"vanjiram". So every product carries aliases, matching ignores Telugu case endings ("రొయ్యల" is "రొయ్యలు"), and a
fuzzy match catches near misses without ever guessing between two close products.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import yaml
from rapidfuzz import fuzz

from ..language.numbers import normalize

DATA = Path(__file__).resolve().parent.parent / "data" / "catalog.yaml"

_DEFAULT_STEP = {"kg": 0.25, "litre": 0.5, "dozen": 1.0, "piece": 1.0, "pack": 1.0, "bunch": 1.0}
_DEFAULT_MAX = {"kg": 25.0, "litre": 20.0, "dozen": 20.0, "piece": 50.0, "pack": 30.0, "bunch": 30.0}
# words that say nothing about which product is meant
_NOISE = {"the", "a", "an", "some", "of", "fresh", "good", "nice", "any", "my", "please", "fish", "చేప", "చేపలు"}
_FUZZY_MIN = 86


@dataclass(frozen=True)
class Product:
    sku: str
    en: str
    te: str
    cat: str
    unit: str
    price: int
    stock: float
    group: str
    seller: str
    tags: Tuple[str, ...] = ()
    cuts: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    note_en: str = ""
    note_te: str = ""
    step: float = 1.0
    max_qty: float = 25.0

    @property
    def vegetarian(self) -> bool:
        return "non-veg" not in self.tags and "egg" not in self.tags

    def name(self, lang: str) -> str:
        return self.te if lang == "te" else self.en

    def note(self, lang: str) -> str:
        return self.note_te if lang == "te" else self.note_en


@dataclass(frozen=True)
class Recipe:
    id: str
    en: str
    te: str
    diet: str
    serves: int
    aliases: Tuple[str, ...]
    main: Dict
    items: Tuple[Dict, ...]

    def name(self, lang: str) -> str:
        return self.te if lang == "te" else self.en


@dataclass(frozen=True)
class Mention:
    product: Product
    start: int      # character span in the normalized text
    end: int
    score: float    # 100 for an exact alias


def _strip_te_ending(word: str) -> str:
    """A rough Telugu stem: "రొయ్యలు", "రొయ్యల", "రొయ్యలని" all share "రొయ్య"."""
    for ending in ("లను", "లకు", "లని", "లో", "లు", "ల", "ను", "కి", "ని"):
        if word.endswith(ending) and len(word) > len(ending) + 1:
            return word[: -len(ending)]
    return word


def _is_telugu(text: str) -> bool:
    return any("ఀ" <= ch <= "౿" for ch in text)


class Catalog:
    def __init__(self, products: Sequence[Product], recipes: Sequence[Recipe], shop: Dict, sellers: Sequence[str]) -> None:
        self.products: List[Product] = list(products)
        self.by_sku: Dict[str, Product] = {p.sku: p for p in products}
        self.recipes: List[Recipe] = list(recipes)
        self.shop = shop
        self.sellers = list(sellers)
        self._aliases: List[Tuple[str, Product]] = []    # normalized alias -> product, longest first
        for p in products:
            names = {normalize(a) for a in (p.aliases + (p.en, p.te, p.sku.replace("-", " ")))}
            self._aliases += [(a, p) for a in names if a]
        self._aliases.sort(key=lambda x: -len(x[0]))
        self._exact: Dict[str, Product] = {}
        for alias, p in self._aliases:
            self._exact.setdefault(alias, p)

    @classmethod
    def load(cls, path: Path = DATA) -> "Catalog":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        products = []
        for r in raw["products"]:
            unit = r["unit"]
            products.append(Product(
                sku=r["sku"], en=r["en"], te=normalize(r["te"]), cat=r["cat"], unit=unit, price=int(r["price"]),
                stock=float(r["stock"]), group=r["group"], seller=r["seller"], tags=tuple(r.get("tags") or ()),
                cuts=tuple(r.get("cuts") or ()), aliases=tuple(str(a) for a in (r.get("aliases") or ())),
                note_en=r.get("note_en", ""), note_te=r.get("note_te", ""),
                step=float(r.get("step", _DEFAULT_STEP[unit])), max_qty=float(r.get("max", _DEFAULT_MAX[unit]))))
        recipes = [Recipe(id=r["id"], en=r["en"], te=normalize(r["te"]), diet=r["diet"], serves=int(r["for"]),
                          aliases=tuple(r.get("aliases") or ()), main=r["main"], items=tuple(r["items"]))
                   for r in raw.get("recipes", [])]
        return cls(products, recipes, raw.get("shop", {}), raw.get("sellers", []))

    # --- finding products by name ----------------------------------------------------------------------------
    def find(self, name: str) -> Optional[Product]:
        """The one product this name means, or None. Two close candidates mean None: never guess."""
        q = normalize(name)
        if not q:
            return None
        if q in self.by_sku:
            return self.by_sku[q]
        if q in self._exact:
            return self._exact[q]
        cleaned = " ".join(w for w in q.split() if w not in _NOISE)
        if cleaned in self._exact:
            return self._exact[cleaned]
        hits = self.mentions(q)
        if len({h.product.sku for h in hits}) == 1:
            return hits[0].product
        scored: Dict[str, Tuple[float, Product]] = {}
        for alias, p in self._aliases:
            score = fuzz.ratio(cleaned, alias)
            if score >= _FUZZY_MIN and score > scored.get(p.sku, (0.0, p))[0]:
                scored[p.sku] = (score, p)
        ranked = sorted(scored.values(), key=lambda x: -x[0])
        if not ranked or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 4):
            return None
        return ranked[0][1]

    def mentions(self, text: str) -> List[Mention]:
        """Every product named in the text, longest name first, without overlaps ("tiger prawns" is not also
        "prawns"). Telugu endings are forgiven; a near miss must be close and a whole word."""
        q = normalize(text)
        taken: List[Tuple[int, int]] = []
        found: List[Mention] = []

        def free(a: int, b: int) -> bool:
            return all(b <= x or a >= y for x, y in taken)

        for alias, p in self._aliases:
            for m in re.finditer(r"(?<![\wఀ-౿])" + re.escape(alias) + r"(?![\wఀ-౿])", q):
                if free(m.start(), m.end()):
                    taken.append((m.start(), m.end()))
                    found.append(Mention(p, m.start(), m.end(), 100.0))
        # Telugu case endings: match on stems, word by word
        for m in re.finditer(r"[ఀ-౿]+", q):
            if not free(m.start(), m.end()):
                continue
            stem = _strip_te_ending(m.group(0))
            for alias, p in self._aliases:
                if " " not in alias and _is_telugu(alias) and _strip_te_ending(alias) == stem:
                    taken.append((m.start(), m.end()))
                    found.append(Mention(p, m.start(), m.end(), 95.0))
                    break
        # near misses of single words: "vanjiram", "chanduwa"
        for m in re.finditer(r"[^\W\d_]{5,}", q):
            word = m.group(0)
            if not free(m.start(), m.end()) or word in _NOISE:
                continue
            best: Dict[str, Tuple[float, Product]] = {}
            for alias, p in self._aliases:
                if " " in alias or abs(len(alias) - len(word)) > 2:
                    continue
                score = fuzz.ratio(word, alias)
                if score >= _FUZZY_MIN and score > best.get(p.sku, (0.0, p))[0]:
                    best[p.sku] = (score, p)
            ranked = sorted(best.values(), key=lambda x: -x[0])
            if ranked and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 4):
                taken.append((m.start(), m.end()))
                found.append(Mention(ranked[0][1], m.start(), m.end(), ranked[0][0]))
        found.sort(key=lambda h: h.start)
        return found

    def recipe(self, name: str) -> Optional[Recipe]:
        q = normalize(name)
        best: Optional[Recipe] = None
        best_len = 0
        for r in self.recipes:
            for alias in r.aliases + (r.en, r.te, r.id.replace("-", " ")):
                a = normalize(alias)
                if a and a in q and len(a) > best_len:
                    best, best_len = r, len(a)
        return best

    # --- browsing --------------------------------------------------------------------------------------------
    def search(self, *, text: str = "", category: Optional[str] = None, tags: Iterable[str] = (),
               avoid_tags: Iterable[str] = (), max_price: Optional[float] = None, vegetarian: bool = False,
               in_stock: bool = True, stock: Optional[Dict[str, float]] = None, limit: int = 5) -> List[Product]:
        """Products that fit, best first: named products, then matching tags, then cheaper. `stock` holds today's
        live levels, which win over the catalog's."""
        want = set(tags)
        avoid = set(avoid_tags)
        named = {h.product.sku for h in self.mentions(text)} if text else set()
        out = []
        for p in self.products:
            level = (stock or {}).get(p.sku, p.stock)
            if in_stock and level <= 0:
                continue
            if category and p.cat != category:
                continue
            if vegetarian and not p.vegetarian:
                continue
            if avoid & set(p.tags):
                continue
            if want and not (want & set(p.tags)) and p.sku not in named:
                continue
            if max_price is not None and p.price > max_price:
                continue
            out.append(p)
        out.sort(key=lambda p: (p.sku not in named, -len(want & set(p.tags)), p.price))
        return out[:limit]

    def substitutes(self, sku: str, *, max_price: Optional[float] = None, stock: Optional[Dict[str, float]] = None,
                    limit: int = 3) -> List[Product]:
        """What to offer instead of a product: the same kind first, closest in price."""
        base = self.by_sku[sku]
        same = [p for p in self.products if p.group == base.group and p.sku != sku]
        related = [p for p in self.products if p.cat == base.cat and p.group != base.group and p.sku != sku]
        ranked = sorted(same, key=lambda p: abs(p.price - base.price)) + sorted(related, key=lambda p: abs(p.price - base.price))
        out = []
        for p in ranked:
            if (stock or {}).get(p.sku, p.stock) <= 0:
                continue
            if max_price is not None and p.price > max_price:
                continue
            out.append(p)
        return out[:limit]
