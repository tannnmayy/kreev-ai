import json

import pytest

from ala.bench import bakeoff, simulate
from ala.minds.router import Provider, Router
from ala.shop.catalog import Catalog

CAT = Catalog.load()


def test_there_are_forty_scenarios_in_three_languages():
    s = simulate.load()
    assert len(s) >= 40
    assert {x["lang"] for x in s} == {"en", "te", "mixed"}
    assert len({x["id"] for x in s}) == len(s)
    for x in s:
        assert x["hidden"] and x["opening"] and x["check"], x["id"]
        for sku in (x["check"].get("cart") or {}):
            assert sku in CAT.by_sku, (x["id"], sku)


def script(*replies):
    queue = list(replies)

    async def http(p, payload, timeout):
        text = json.dumps(queue.pop(0) if queue else {"say": "", "done": True}, ensure_ascii=False)
        return 200, {}, json.dumps({"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 50}})

    p = Provider("m", "http://x", "m", key="k")
    return Router(chains={"sim": ["m"], "fast": ["m"], "slow": ["m"]}, providers={"m": p}, http=http)


async def test_a_simulated_customer_is_checked_against_the_database():
    scenario = {"id": "x", "lang": "en", "opening": "one kilo of rice", "hidden": "Then add eggs.", "check": {"cart": {"sona-masuri": 1, "eggs": 1}}}
    run = await simulate.run_once(scenario, CAT, Router(chains={"fast": [], "slow": []}, providers={}), script({"say": "a dozen eggs please"}))
    assert run.passed, run.problems
    assert len(run.turns) == 4


async def test_a_failed_goal_is_reported():
    scenario = {"id": "x", "lang": "en", "opening": "one kilo of rice", "hidden": "h", "check": {"orders": 1}}
    run = await simulate.run_once(scenario, CAT, Router(chains={"fast": [], "slow": []}, providers={}), script())
    assert not run.passed and run.problems


@pytest.mark.parametrize("scenario", simulate.load(), ids=lambda s: s["id"])
async def test_every_scenario_runs_end_to_end_without_a_model(scenario):
    """Quota is scarce: a typo in a check or a crash must never surface halfway through a paid-for-in-patience run."""
    run = await simulate.run_once(scenario, CAT, Router(chains={"fast": [], "slow": []}, providers={}), script())
    assert run.turns and run.turns[0][1] == scenario["opening"]


def test_a_cancelled_order_does_not_count_as_live():
    from ala.bench.deck import check_state
    from ala.minds.brain import Brain
    from ala.shop.engine import ShopEngine
    from ala.shop.state import Order
    brain = Brain(ShopEngine(CAT), None)
    brain.shop.state.orders.append(Order("A1", [], 100, "h", status="cancelled"))
    assert check_state({"check": {"orders": 1, "orders_live": 0}}, brain, "") == []
    assert check_state({"check": {"orders_live": 1}}, brain, "")


def test_pass_k_needs_every_run():
    runs = [simulate.Run("a", True, [], []), simulate.Run("a", True, [], []), simulate.Run("b", True, [], []), simulate.Run("b", False, ["x"], [])]
    s = simulate.summarise(runs, 2)
    assert s["pass_rate"] == 0.75 and s["pass_all_k"] == 0.5


@pytest.mark.parametrize("ref,heard,score", [("half a kilo", "half a kilo", 0), ("half a kilo", "half kilo", 1 / 3), ("అర కిలో రొయ్యలు", "అర కిలో", 1 / 3)])
def test_word_error(ref, heard, score):
    assert bakeoff.wer(ref, heard) == pytest.approx(score)


def test_items_are_scored_apart_from_words():
    assert bakeoff.items_right(["రొయ్యలు", "అర కిలో"], "అర కిలో రొయ్యలు వేయండి") == (2, 2)
    assert bakeoff.items_right(["రొయ్యలు"], "అర కిలో రోయలు") == (0, 1)


def test_the_turn_ablation_reports_each_layer():
    rows = [("unfinished", 0.1, "half a kilo of"), ("unfinished", 0.9, "add rice and"), ("finished", 0.9, "add rice"), ("finished", 0.2, "add dal")]
    by = {s.layer: s for s in bakeoff.score_turns(rows)}
    assert by["silence only"].false_cutoffs == 1.0 and by["silence only"].false_waits == 0
    assert by["voice"].false_cutoffs == 0.5
    assert by["voice + words"].false_cutoffs == 0.0 and by["voice + words"].false_waits == 0.5


async def test_the_key_check_makes_a_real_request_and_says_what_failed():
    good = script({"ok": True})
    assert (await bakeoff.check_model(good, "m")).startswith("works")

    async def refused(p, payload, timeout):
        return 401, {}, "bad key"

    bad = Router(chains={"fast": ["m"]}, providers={"m": Provider("m", "http://x", "m", key="k")}, http=refused)
    assert (await bakeoff.check_model(bad, "m")).startswith("FAILED")
    absent = Router(chains={"fast": ["m"]}, providers={"m": Provider("m", "http://x", "m", key_env="ALA_NO_SUCH_KEY")}, http=refused)
    assert await bakeoff.check_model(absent, "m") == "no key"
