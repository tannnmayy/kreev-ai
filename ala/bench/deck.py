"""The real-life deck, run in code.

    uv run python -m ala.bench.deck              # run every card that needs no call, print a table
    uv run python -m ala.bench.deck --export     # write web/cards.json for the "Test card" button

Cards with `how: voice` need a person and a real call; they stay in the deck for the daily run. The rest are checked here, with
no models unless a card says it needs one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..config import ROOT
from ..coordinator import Coordinator
from ..minds.brain import Brain
from ..minds.router import Router
from ..perception import overlap
from ..shop.catalog import Catalog
from ..shop.engine import ShopEngine

DECK = ROOT / "scenarios" / "deck.yaml"
ECHO_LINE = "Seer fish is nine hundred rupees a kilo and it is very firm"


def load() -> List[Dict[str, Any]]:
    return yaml.safe_load(DECK.read_text(encoding="utf-8"))["cards"]


def check_state(card: Dict[str, Any], brain: Brain, reply: str) -> List[str]:
    """Every way the final state differs from what the card expects."""
    want = card.get("check") or {}
    shop = brain.shop
    bad: List[str] = []
    cart = {x.sku: x.qty for x in shop.state.cart}
    for sku, qty in (want.get("cart") or {}).items():
        if cart.get(sku) != qty:
            bad.append("cart %s is %s, wanted %s" % (sku, cart.get(sku), qty))
    if "cart" in want and set(cart) - set(want["cart"]):
        bad.append("extra in cart: %s" % sorted(set(cart) - set(want["cart"])))
    for sku in want.get("cart_has") or []:
        if sku not in cart:
            bad.append("%s missing from the cart" % sku)
    if want.get("cart_empty") and cart:
        bad.append("cart should be empty, has %s" % sorted(cart))
    if "cart_count" in want and len(cart) != want["cart_count"]:
        bad.append("cart has %d lines, wanted %d" % (len(cart), want["cart_count"]))
    if "orders" in want and len(shop.state.orders) != want["orders"]:
        bad.append("%d orders, wanted %d" % (len(shop.state.orders), want["orders"]))
    if "orders_live" in want:
        live = sum(1 for o in shop.state.orders if o.status == "placed")
        if live != want["orders_live"]:
            bad.append("%d live orders, wanted %d" % (live, want["orders_live"]))
    if "pending" in want:
        got = shop.state.pending.kind if shop.state.pending else None
        if got != want["pending"]:
            bad.append("pending is %s, wanted %s" % (got, want["pending"]))
    for key, value in (want.get("needs") or {}).items():
        if getattr(shop.state.needs, key) != value:
            bad.append("needs.%s is %s, wanted %s" % (key, getattr(shop.state.needs, key), value))
    for fragment in want.get("says") or []:
        if fragment not in reply:
            bad.append("reply lacks %r: %r" % (fragment, reply))
    for fragment in want.get("not_says") or []:
        if fragment in reply:
            bad.append("reply has %r: %r" % (fragment, reply))
    if want.get("silent") and reply:
        bad.append("should be silent, said %r" % reply)
    if want.get("says_any") and not reply:
        bad.append("said nothing")
    if want.get("question") and "?" not in reply:
        bad.append("expected a question: %r" % reply)
    if want.get("shown_some") and not shop.state.shown:
        bad.append("nothing shown")
    if want.get("vegetarian_options"):
        for option in shop.state.pending.data.get("options", []) if shop.state.pending else []:
            recipe = shop.catalog.recipe(option["recipe"].replace("-", " "))
            if recipe.diet != "veg":
                bad.append("offered %s" % recipe.id)
    return bad


def instant_coordinator() -> Coordinator:
    """The real gate and ledger with the waiting taken out, so a text run takes the same path as a call."""
    return Coordinator(commit_hold_s=0, order_hold_s=0, revising_hold_s=0, dangling_hold_s=0, words_wait_s=0)


async def spoken_turn(brain: Brain, words: str) -> List[str]:
    """One turn as a call has it: words arrive, the turn closes, the reply is heard to the end."""
    coord = brain.coord
    coord.user_started_speaking()
    coord.heard(words)
    coord.user_stopped_speaking()
    coord.turn_committed()
    said = [s.text async for s in brain.respond(words, epoch=coord.epoch)]
    coord.agent_acted()
    if said:
        brain.reply_heard(brain.last_reply, brain.last_reply, False)
    return said


async def run_card(card: Dict[str, Any], catalog: Catalog, router: Optional[Router]) -> List[str]:
    """Problems with this card, or an empty list."""
    bad: List[str] = []
    for words, speaking, kind in card.get("overlap") or []:
        verdict = overlap.classify(words, ala_speaking=speaking, said_now=ECHO_LINE, catalog=catalog)
        if verdict.kind != kind:
            bad.append("%r called %s, wanted %s" % (words, verdict.kind, kind))
    script = card.get("script")
    if script:
        brain = Brain(ShopEngine(catalog), router, instant_coordinator())
        reply = ""
        for line in script:
            reply = " ".join(await spoken_turn(brain, line))
        bad += check_state(card, brain, reply)
    return bad


async def run_all(router: Optional[Router] = None) -> Tuple[int, int, Dict[str, str], int]:
    catalog = Catalog.load()
    cards = load()
    results: Dict[str, str] = {}
    skipped = 0
    for card in cards:
        if card["how"] == "voice" or (card.get("model") and router is None):
            skipped += 1
            continue
        problems = await run_card(card, catalog, router)
        results[card["id"]] = "; ".join(problems)
    passed = sum(1 for v in results.values() if not v)
    return passed, len(results), results, skipped


def export() -> Path:
    cards = [{k: c[k] for k in ("id", "family", "title", "do", "expect")} for c in load()]
    out = ROOT / "web" / "cards.json"
    out.write_text(json.dumps(cards, ensure_ascii=False, indent=0), encoding="utf-8")
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--models", action="store_true", help="also run cards that need a model")
    args = parser.parse_args()
    if args.export:
        print("wrote", export())
        return
    router = Router() if args.models else None
    passed, total, results, skipped = asyncio.run(run_all(router))
    for card_id, problem in results.items():
        if problem:
            print("FAIL %-26s %s" % (card_id, problem))
    print("%d of %d cards passed; %d need a person, a call or a model." % (passed, total, skipped))


if __name__ == "__main__":
    main()
