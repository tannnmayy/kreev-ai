"""Every card in the real-life deck that needs no person or model is a regression test."""

import json

import pytest

from ala.bench import deck
from ala.minds.router import Provider, Router
from ala.shop.catalog import Catalog

CARDS = deck.load()
CATALOG = Catalog.load()
RUNNABLE = [c for c in CARDS if c["how"] != "voice" and not c.get("model")]


def test_the_deck_is_about_seventy_cards_in_nine_families():
    assert len(CARDS) >= 70
    assert len({c["family"] for c in CARDS}) == 9
    assert len({c["id"] for c in CARDS}) == len(CARDS)
    for c in CARDS:
        assert c["do"] and c["expect"] and c["how"] in ("voice", "text", "perception"), c["id"]


@pytest.mark.parametrize("card", RUNNABLE, ids=[c["id"] for c in RUNNABLE])
async def test_card(card):
    assert await deck.run_card(card, CATALOG, None) == []


async def test_quota_drill_with_every_provider_refusing():
    async def http(p, payload, timeout):
        return 429, {"retry-after": "60"}, "quota exceeded"

    p = Provider("model", "http://x", "m", key="k")
    router = Router(chains={"fast": ["model"], "slow": ["model"]}, providers={"model": p}, http=http)
    card = next(c for c in CARDS if c["id"] == "sys-quota")
    assert await deck.run_card(card, CATALOG, router) == []


def test_the_web_copy_of_the_deck_is_current():
    path = deck.ROOT / "web" / "cards.json"
    assert len(json.loads(path.read_text(encoding="utf-8"))) == len(CARDS)
