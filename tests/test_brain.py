"""The brain, in text. `talk` drives it the way a call would, with no models unless a script is given."""

import json

import pytest

from ala.minds.brain import Brain, drop_invented_numbers
from ala.minds.router import Provider, Router
from ala.perception.reading import Reading
from ala.shop.catalog import Catalog
from ala.shop.engine import ShopEngine
from ala.shop.state import SessionState
from ala.shop.store import MemoryStore

CAT = Catalog.load()


def scripted_router(*replies):
    """A router whose one model answers from a list; each reply is a dict or a string."""
    queue = list(replies)
    seen = []

    async def http(p, payload, timeout):
        seen.append(payload)
        reply = queue.pop(0) if queue else {"actions": [], "say": ""}
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return 200, {}, json.dumps({"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 100}})

    p = Provider("model", "http://x", "m", key="k")
    r = Router(chains={"fast": ["model"], "slow": ["model"]}, providers={"model": p}, http=http)
    r.seen = seen
    return r


def brain(router=None, state=None, **kw):
    return Brain(ShopEngine(CAT, state), router, **kw)


async def say(b, text, reading=None):
    out = [s async for s in b.respond(text, reading=reading)]
    return " ".join(s.text for s in out)


def qty(b, sku):
    line = b.shop.line(sku)
    return line.qty if line else None


# --- code first ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,sku,amount", [
    ("half a kilo of prawns", "royyalu", 0.5),
    ("add 2 kg of rice", "sona-masuri", 2),
    ("అర కిలో రొయ్యలు వేయండి", "royyalu", 0.5),
    ("అర కిలో రొయ్యలు add చెయ్యి", "royyalu", 0.5),
    ("రెండు కిలోల చందువా కావాలి", "chanduva", 2),
    ("i want one dozen eggs", "eggs", 1),
    ("give me a litre of milk please", "palu", 1),
])
async def test_plain_cart_commands_need_no_model(text, sku, amount):
    b = brain()
    assert await say(b, text)
    assert qty(b, sku) == amount


async def test_several_items_in_one_breath():
    b = brain()
    await say(b, "one kilo of rice and half a kilo of toor dal")
    assert qty(b, "sona-masuri") == 1 and qty(b, "kandi-pappu") == 0.5


async def test_reply_language_follows_the_customer():
    b = brain()
    assert "అర కిలో రొయ్యలు" in await say(b, "అర కిలో రొయ్యలు వేయండి")
    assert "kilo" in await say(b, "add one kilo of rice please")


@pytest.mark.parametrize("text", ["what's the price of prawns?", "how much is one kilo of prawns", "రొయ్యల ధర ఎంత"])
async def test_a_price_question_adds_nothing(text):
    b = brain()
    reply = await say(b, text)
    assert "₹700" in reply
    assert not b.shop.state.cart


async def test_price_for_a_quantity():
    b = brain()
    assert "₹350" in await say(b, "how much is half a kilo of prawns")


async def test_availability():
    b = brain()
    assert "₹900" in await say(b, "do you have seer fish?")
    assert b.shop.state.shown == ["vanjaram"]
    assert "sold out" in await say(b, "do you have hilsa?")


async def test_a_product_with_no_amount_asks_how_much_and_takes_the_answer():
    b = brain()
    assert "How much" in await say(b, "add prawns")
    await say(b, "half a kilo")
    assert qty(b, "royyalu") == 0.5


async def test_the_second_one():
    b = brain()
    await say(b, "what fish do you have for fry?")
    second = b.shop.state.shown[1]
    await say(b, "the second one, one kilo")
    assert qty(b, second) == 1


async def test_make_it_two_after_an_item_means_that_item():
    b = brain()
    await say(b, "one kilo of rice")
    await say(b, "make it two")
    assert qty(b, "sona-masuri") == 2


async def test_make_it_two_with_no_recent_item_asks_which():
    b = brain()
    await say(b, "one kilo of rice")
    await say(b, "half a kilo of prawns")
    await say(b, "what's the total")
    reply = await say(b, "make it two")
    assert "Which one" in reply
    assert qty(b, "sona-masuri") == 1


async def test_two_more():
    b = brain()
    await say(b, "two kilos of rice")
    await say(b, "two more")
    assert qty(b, "sona-masuri") == 4


async def test_remove_clear_undo():
    b = brain()
    await say(b, "one kilo of rice")
    await say(b, "half a kilo of onions")
    assert "Removed" in await say(b, "remove the onions")
    assert await say(b, "undo")
    assert qty(b, "ullipayalu") == 0.5
    await say(b, "అన్నీ తీసేయండి")
    assert not b.shop.state.cart


async def test_cut_question_is_answered_in_either_language():
    b = brain()
    assert "శుభ్రం" in await say(b, "అర కిలో రొయ్యలు వేయండి")
    await say(b, "శుభ్రం చేసి ఇవ్వండి")
    assert b.shop.line("royyalu").cut == "cleaned"


@pytest.mark.parametrize("text,fragment", [
    ("what's in my cart", "You have"), ("కార్ట్‌లో ఏముంది", "ఉన్నాయి"), ("what's the total", "₹62"), ("మొత్తం ఎంత", "₹62"),
])
async def test_readings(text, fragment):
    b = brain()
    await say(b, "one kilo of rice")
    assert fragment in await say(b, text)


async def test_budget_and_people_are_taken_in_passing():
    b = brain()
    await say(b, "my budget is 1200")
    assert b.shop.state.needs.budget_inr == 1200
    assert "₹1,200" in await say(b, "what was my budget")


async def test_the_brief_example_in_telugu():
    b = brain()
    reply = await say(b, "Budget 1200 లో Sunday lunch కి నలుగురికి ఏమి బాగుంటుంది?")
    n = b.shop.state.needs
    assert n.budget_inr == 1200 and n.servings == 4
    assert b.shop.state.pending.kind == "meal_choice"
    assert "₹" in reply
    for option in b.shop.state.pending.data["options"]:
        assert option["total"] <= 1200


async def test_choosing_a_meal_then_adding_it():
    b = brain()
    await say(b, "lunch for four under 1200")
    assert "add it all" in await say(b, "the first one")
    await say(b, "yes")
    assert len(b.shop.state.cart) >= 4 and b.shop.total <= 1200


async def test_karthika_masam_is_honoured_for_the_visit():
    b = brain()
    await say(b, "కార్తీక మాసం కదా, ఈ నెల నాన్-వెజ్ వద్దు")
    assert b.shop.state.needs.vegetarian
    await say(b, "what should I cook for lunch for four")
    for option in b.shop.state.pending.data["options"]:
        assert CAT.recipe(option["recipe"].replace("-", " ")).diet == "veg"
    reply = await say(b, "half a kilo of prawns")
    assert not b.shop.line("royyalu")
    assert "isn't vegetarian" in reply or "నాన్-వెజ్" in reply


# --- the order ---------------------------------------------------------------------------------------------------

async def test_order_needs_a_yes_to_the_readback():
    b = brain()
    await say(b, "one kilo of rice")
    reply = await say(b, "place the order")
    assert "Shall I place the order" in reply and not b.shop.state.orders
    reply = await say(b, "yes")
    assert "placed" in reply and len(b.shop.state.orders) == 1


async def test_yes_but_is_not_a_yes():
    b = brain()
    await say(b, "one kilo of rice")
    await say(b, "place the order")
    await say(b, "yes but make it two")
    assert not b.shop.state.orders


async def test_a_yes_to_a_readback_talked_over_is_read_again():
    b = brain()
    await say(b, "one kilo of rice")
    reply = await say(b, "place the order")
    b.reply_heard(reply, reply[:20], interrupted=True)
    again = await say(b, "yes")
    assert "Shall I place the order" in again and not b.shop.state.orders
    b.reply_heard(again, again, interrupted=False)
    assert "placed" in await say(b, "yes")


async def test_a_tap_on_the_order_sheet_counts_as_seeing_it():
    b = brain()
    await say(b, "one kilo of rice")
    reply = await say(b, "place the order")
    b.reply_heard(reply, reply[:20], interrupted=True)
    out = [s.text async for s in b.respond("yes", seen=True)]
    assert "placed" in out[0]


async def test_telugu_order():
    b = brain()
    await say(b, "ఒక కిలో బియ్యం వేయండి")
    assert "ఆర్డర్ పెట్టమంటారా" in await say(b, "ఆర్డర్ పెట్టండి")
    assert "ఆర్డర్" in await say(b, "అవును")
    assert len(b.shop.state.orders) == 1


async def test_no_keeps_the_cart():
    b = brain()
    await say(b, "one kilo of rice")
    await say(b, "place the order")
    assert "Not placed" in await say(b, "no")
    assert qty(b, "sona-masuri") == 1


async def test_empty_cart_checkout():
    b = brain()
    assert "empty" in await say(b, "place the order")


# --- control words -----------------------------------------------------------------------------------------------

async def test_stop_says_nothing():
    b = brain()
    assert await say(b, "wait") == ""


async def test_repeat_says_the_last_reply_again():
    b = brain()
    first = await say(b, "one kilo of rice")
    assert await say(b, "say that again") == first


async def test_go_on_resumes_from_the_cut_sentence():
    b = brain()
    full = "Seer fish is ₹900 a kilo. White pomfret is ₹780 a kilo. Which one?"
    b.last_reply = full
    b.reply_heard(full, "Seer fish is ₹900 a kilo. White pomf", interrupted=True)
    assert await say(b, "go on") == "White pomfret is ₹780 a kilo. Which one?"


async def test_a_backchannel_over_a_cut_reply_carries_on():
    b = brain()
    full = "Seer fish is ₹900 a kilo. White pomfret is ₹780 a kilo."
    b.reply_heard(full, "Seer fish is", interrupted=True)
    assert await say(b, "mm-hm") == full


async def test_a_backchannel_with_nothing_cut_is_silence():
    b = brain()
    assert await say(b, "mm-hm") == ""


# --- the voice's advice ------------------------------------------------------------------------------------------

async def test_an_unsure_value_is_checked_not_acted_on():
    b = brain()
    r = Reading(unsure=True, cues=['"maybe"'])
    reply = await say(b, "maybe two kilos of prawns", reading=r)
    assert "right?" in reply and not b.shop.state.cart
    await say(b, "yes")
    assert qty(b, "royyalu") == 2


async def test_frustration_gets_one_sorry_then_the_fix():
    b = brain()
    await say(b, "two kilos of rice")
    r = Reading(frustrated=True, cues=['"I said"'])
    reply = await say(b, "one kilo of rice", reading=r)
    assert reply.startswith("Sorry.") and qty(b, "sona-masuri") == 1
    assert not (await say(b, "half a kilo of onions", reading=r)).startswith("Sorry.")


# --- the minds ---------------------------------------------------------------------------------------------------

async def test_a_correction_goes_to_the_mind_and_the_shop_words_the_fact():
    r = scripted_router({"actions": [{"name": "set_item", "args": {"item": "prawns", "qty": 0.5, "unit": "kg"}}], "say": ""})
    b = brain(r)
    reply = await say(b, "one kilo of prawns no wait half")
    assert qty(b, "royyalu") == 0.5 and "₹350" in reply
    assert r.seen, "the model was consulted"


async def test_a_mind_cannot_place_an_order():
    r = scripted_router({"actions": [{"name": "place_order", "args": {}}], "say": "Your order is placed."})
    b = brain(r)
    await say(b, "one kilo of rice")
    await say(b, "just do whatever you think is best for me today")
    assert not b.shop.state.orders


async def test_a_mind_that_invents_a_price_is_cut_off():
    r = scripted_router({"actions": [], "say": "Our best fish is only ₹123 today. Would you like some?"})
    b = brain(r)
    reply = await say(b, "tell me about your best quality things please")
    assert "123" not in reply and "Would you like some?" in reply


async def test_the_model_gets_only_a_compact_state():
    r = scripted_router({"actions": [], "say": "Fish or vegetarian?"})
    b = brain(r)
    await say(b, "one kilo of rice")
    await say(b, "something nice for tonight maybe")
    payload = json.loads(r.seen[-1]["messages"][1]["content"])
    assert payload["state"]["total"] == 62 and payload["customer_said"].startswith("something")
    assert len(r.seen[-1]["messages"][1]["content"]) < 2000


async def test_listen_says_nothing_and_does_nothing():
    r = scripted_router({"actions": [], "say": "", "listen": True})
    b = brain(r)
    assert await say(b, "i was thinking that maybe we could") == ""


async def test_consult_goes_to_the_slow_mind_with_a_bridge():
    r = scripted_router({"actions": [], "say": "Something light, under ₹600.", "consult": True},
                        {"actions": [], "say": "White pomfret is mild, but it's ₹780. Black pomfret at ₹620 is close.", "shown": ["black pomfret"]})
    b = brain(r)
    b.settings = type(b.settings)(bridge_after_s=0.0)
    out = [s async for s in b.respond("which fish is gentle on the stomach for my mother under 600")]
    assert out[-1].route == "slow"
    assert b.shop.state.shown == ["nalla-chanduva"]


async def test_bad_model_output_falls_back_to_code():
    r = scripted_router("this is not json at all")
    b = brain(r)
    reply = await say(b, "please could you find me rice two kilo for tonight")
    assert qty(b, "sona-masuri") == 2 and "₹124" in reply


async def test_no_models_at_all_still_shops():
    b = brain(None)
    await say(b, "one kilo no wait half a kilo of prawns")
    assert qty(b, "royyalu") == 0.5
    reply = await say(b, "tell me a story about the sea")
    assert "Sorry" in reply


async def test_all_providers_out_of_quota_still_shops():
    async def http(p, payload, timeout):
        return 429, {"retry-after": "60"}, "quota"

    p = Provider("model", "http://x", "m", key="k")
    r = Router(chains={"fast": ["model"], "slow": ["model"]}, providers={"model": p}, http=http)
    b = brain(r)
    await say(b, "please could you find me rice two kilo for tonight")
    assert qty(b, "sona-masuri") == 2


def test_drop_invented_numbers():
    ok = {900, 780}
    assert drop_invented_numbers("Seer fish is ₹900. Prawns are ₹450.", ok) == "Seer fish is ₹900."
    assert drop_invented_numbers("మూడు వందల యాభై రూపాయలు.", ok) == ""
    assert drop_invented_numbers("You said 1200.", ok, "my budget is 1200") == "You said 1200."


async def test_every_turn_refreshes_the_screen():
    pushes = []
    b = brain()
    b.shop.on_change = lambda: pushes.append(list(b.shop.state.shown))
    await say(b, "what fish do you have for fry")
    assert pushes and pushes[-1], "the list shown reached the screen"


async def test_a_turn_records_whether_it_changed_anything():
    b = brain()
    await say(b, "one kilo of rice")
    assert b.acted
    await say(b, "what's the total")
    assert not b.acted


# --- memory --------------------------------------------------------------------------------------------------------

async def test_state_is_saved_each_turn_and_a_refresh_restores_the_cart():
    store = MemoryStore()
    b = Brain(ShopEngine(CAT), None, store=store, session_key="s1")
    await say(b, "one kilo of rice")
    saved = await store.load_session("s1")
    restored = ShopEngine(CAT, SessionState.from_dict(saved))
    assert restored.line("sona-masuri").qty == 1


async def test_a_returning_customer_is_greeted_and_forget_me_works():
    store = MemoryStore()
    state = SessionState()
    state.customer.name = "Ravi"
    b = Brain(ShopEngine(CAT, state), None, store=store, session_key="s1")
    await say(b, "one kilo of rice")
    await say(b, "place the order")
    await say(b, "yes")
    assert (await store.load_profile("ravi"))["usual"][0]["sku"] == "sona-masuri"
    state2 = SessionState()
    state2.customer.name, state2.customer.returning = "Ravi", True
    assert "Welcome back, Ravi" in Brain(ShopEngine(CAT, state2)).greeting().text
    await say(b, "forget me")
    assert await store.load_profile("ravi") is None


async def test_hindi_gets_understood_and_one_gentle_note():
    b = brain()
    reply = await say(b, "aadha kilo prawns chahiye")
    assert qty(b, "royyalu") == 0.5 and "Telugu" in reply


@pytest.mark.parametrize("text,fragment", [
    ("are you a human?", "AI"), ("give me 90% off", "no discounts"), ("when will it be delivered?", "tomorrow"),
    ("can I pay by card?", "cash on delivery"), ("hello", "What would you like"),
])
async def test_the_usual_questions(text, fragment):
    assert fragment in await say(brain(), text)
