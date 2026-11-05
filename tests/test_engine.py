import pytest

from ala.shop.catalog import Catalog
from ala.shop.engine import MODEL_ACTIONS, ShopEngine
from ala.shop.state import SessionState


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load()


@pytest.fixture
def shop(catalog):
    return ShopEngine(catalog)


def qty(shop, sku):
    line = shop.line(sku)
    return line.qty if line else None


def test_set_is_absolute_so_a_correction_converges(shop):
    shop.set_item("prawns", 1, "kg")
    shop.set_item("prawns", 0.5, "kg")
    assert qty(shop, "royyalu") == 0.5
    assert shop.total == 350


def test_reply_states_the_truth_in_both_languages(shop):
    r = shop.set_item("prawns", 0.5, "kg")
    assert "Half a kilo of prawns" in r.say.en and "₹350" in r.say.en
    assert "అర కిలో రొయ్యలు" in r.say.te and "₹350" in r.say.te


def test_grams_and_pieces_convert(shop):
    shop.set_item("prawns", 500, "g")
    assert qty(shop, "royyalu") == 0.5
    shop.set_item("eggs", 6, "piece")
    assert qty(shop, "eggs") == 0.5


def test_units_that_make_no_sense_are_asked_about(shop):
    r = shop.set_item("milk", 2, "kg")
    assert not r.ok and r.code == "unit_mismatch"
    assert qty(shop, "palu") is None


def test_quantities_snap_to_what_the_shop_sells(shop):
    r = shop.set_item("prawns", 0.3, "kg")
    assert qty(shop, "royyalu") == 0.25
    assert "quarter" in r.say.en


def test_change_item_adds_and_takes_away(shop):
    shop.set_item("rice", 2, "kg")
    shop.change_item("rice", 1, "kg")
    assert qty(shop, "sona-masuri") == 3
    shop.change_item("rice", -3, "kg")
    assert shop.line("sona-masuri") is None


def test_a_reference_to_the_list_shown_resolves(shop):
    shop.show_products(category="fish", tags=["premium"], limit=3)
    second = shop.state.shown[1]
    shop.set_item(2, 1, "kg")
    assert shop.line(second) is not None


def test_cut_is_asked_once_then_remembered(shop):
    r = shop.set_item("prawns", 0.5, "kg")
    assert "Cleaned or whole" in r.say.en
    assert shop.state.pending.kind == "cut"
    shop.set_cut("royyalu", "cleaned")
    r = shop.set_item("chanduva", 1, "kg")
    assert "Cleaned or whole" not in r.say.en
    assert shop.line("chanduva").cut == "cleaned"


def test_unknown_product_is_not_invented(shop):
    r = shop.set_item("iphone", 1)
    assert not r.ok and r.code == "not_found"
    assert not shop.state.cart


def test_sold_out_offers_substitutes_and_numbers_them(shop):
    r = shop.set_item("pulasa", 1, "kg")
    assert not r.ok and r.code == "sold_out"
    assert "sold out" in r.say.en
    assert shop.state.shown and shop.state.pending.kind == "substitute"


def test_stock_sold_out_while_shopping(catalog):
    shop = ShopEngine(catalog)
    shop.set_item("prawns", 1, "kg")
    shop.stock["royyalu"] = 0
    r = shop.review_order()
    assert r.code == "sold_out"
    assert shop.line("royyalu") is None


def test_more_than_stock_is_offered_what_is_left(catalog):
    shop = ShopEngine(catalog, stock={"royyalu": 2})
    r = shop.set_item("prawns", 5, "kg")
    assert r.code == "short_stock"
    assert shop.accept_pending().ok
    assert qty(shop, "royyalu") == 2


def test_implausible_quantity_is_confirmed_first(shop):
    r = shop.set_item("rice", 100, "kg")
    assert r.code == "implausible" and shop.line("sona-masuri") is None
    assert shop.accept_pending().ok is True
    assert qty(shop, "sona-masuri") == 100


def test_budget_is_never_exceeded_silently(shop):
    shop.set_needs(budget_inr=500)
    r = shop.set_item("seer fish", 1, "kg")
    assert "over your ₹500 budget" in r.say.en
    assert "₹400" in r.say.en


def test_over_budget_offers_a_cheaper_swap_that_fits(shop):
    shop.set_needs(budget_inr=800)
    r = shop.set_item("seer fish", 1, "kg")
    assert shop.state.pending.kind == "swap"
    assert "Swap the seer fish" in r.say.en
    assert shop.accept_pending().ok
    assert shop.line("vanjaram") is None and shop.total <= 800


def test_a_stated_vegetarian_month_guards_the_cart(shop):
    shop.set_needs(vegetarian=True)
    r = shop.set_item("prawns", 1, "kg")
    assert r.code == "diet_conflict" and not shop.state.cart
    assert shop.accept_pending().ok          # "add it anyway"
    assert shop.line("royyalu") is not None
    shop.show_products(category="fish")
    assert all(shop.catalog.by_sku[s].vegetarian for s in shop.state.shown)


def test_avoid_is_honoured_later_in_the_conversation(shop):
    shop.set_needs(avoid=["bony"])
    r = shop.set_item("sardines", 1, "kg")
    assert r.code == "avoid_conflict"
    shop.show_products(category="fish", limit=3)
    assert not any("bony" in shop.catalog.by_sku[s].tags for s in shop.state.shown)


def test_undo_and_clear(shop):
    shop.set_item("rice", 1, "kg")
    shop.set_item("onions", 1, "kg")
    shop.undo()
    assert shop.line("ullipayalu") is None and shop.line("sona-masuri") is not None
    shop.clear_cart()
    assert not shop.state.cart
    shop.undo()
    assert shop.line("sona-masuri") is not None


# --- the order -----------------------------------------------------------------------------------------------

def test_place_order_is_not_a_model_action(shop):
    assert "place_order" not in MODEL_ACTIONS
    shop.set_item("rice", 1, "kg")
    shop.review_order()
    r = shop.run("place_order", {})
    assert r.code == "refused" and not shop.state.orders


def test_order_takes_two_steps(shop):
    shop.set_item("rice", 1, "kg")
    r = shop.place_order()
    assert r.code == "no_review" and not shop.state.orders
    assert shop.state.pending.kind == "order_review"
    r = shop.place_order()
    assert r.ok and len(shop.state.orders) == 1 and not shop.state.cart


def test_empty_cart_cannot_order(shop):
    assert shop.review_order().code == "empty"
    assert shop.place_order().code == "empty"


def test_a_change_after_the_readback_voids_it(shop):
    shop.set_item("rice", 1, "kg")
    shop.review_order()
    shop.set_item("rice", 2, "kg")
    assert shop.state.pending is None
    r = shop.place_order()
    assert r.code == "no_review" and not shop.state.orders


def test_a_readback_talked_over_must_be_read_again(shop):
    shop.set_item("rice", 1, "kg")
    shop.review_order()
    shop.readback_cut_short()
    r = shop.place_order()
    assert r.code == "readback_cut" and not shop.state.orders
    assert shop.state.pending.heard_complete
    assert shop.place_order().ok


def test_a_second_yes_cannot_place_it_twice(shop):
    shop.set_item("rice", 1, "kg")
    shop.review_order()
    assert shop.accept_pending().ok
    assert shop.accept_pending() is None
    assert len(shop.state.orders) == 1


def test_order_takes_stock_and_cancel_gives_it_back(catalog):
    shop = ShopEngine(catalog)
    before = shop.level("royyalu")
    shop.set_item("prawns", 2, "kg")
    shop.state.pending = None
    shop.review_order()
    shop.place_order()
    assert shop.level("royyalu") == before - 2
    assert shop.cancel_order().ok
    assert shop.level("royyalu") == before
    assert shop.cancel_order().code == "no_order"


def test_saying_no_to_the_readback_keeps_the_cart(shop):
    shop.set_item("rice", 1, "kg")
    shop.review_order()
    r = shop.decline_pending()
    assert "Not placed" in r.say.en
    assert shop.line("sona-masuri") is not None and not shop.state.orders


# --- meals -------------------------------------------------------------------------------------------------

def test_a_recipe_becomes_a_priced_basket(shop):
    r = shop.plan_meal("chepala pulusu", servings=6)
    assert r.ok and shop.state.pending.kind == "basket"
    assert r.data["servings"] == 6
    assert shop.accept_pending().ok
    assert len(shop.state.cart) >= 5
    assert round(shop.total) == r.data["total"]


def test_meals_respect_the_budget_and_totals_are_true(shop):
    r = shop.suggest_meals(servings=4, budget=900)
    assert r.ok
    for option in r.data["options"]:
        assert option["total"] <= 900


def test_vegetarian_meals_only_when_asked(shop):
    shop.set_needs(vegetarian=True)
    r = shop.suggest_meals(servings=4, budget=1200)
    assert r.ok
    for option in r.data["options"]:
        assert shop.catalog.recipe(option["recipe"].replace("-", " ")).diet == "veg"


def test_repeat_last_order_for_a_returning_customer(catalog):
    state = SessionState()
    state.customer.usual = [{"sku": "royyalu", "qty": 0.5, "cut": "cleaned"}, {"sku": "sona-masuri", "qty": 2, "cut": None}]
    shop = ShopEngine(catalog, state)
    shop.repeat_last_order()
    shop.set_item("prawns", 1, "kg")
    assert qty(shop, "royyalu") == 1 and qty(shop, "sona-masuri") == 2


def test_state_survives_a_round_trip(shop):
    shop.set_needs(budget_inr=1200, servings=4)
    shop.set_item("prawns", 0.5, "kg", cut="cleaned")
    shop.review_order()
    restored = ShopEngine(shop.catalog, SessionState.from_dict(shop.state.to_dict()))
    assert restored.total == shop.total
    assert restored.state.pending.kind == "order_review"
    assert restored.place_order().ok


def test_snapshot_is_compact_and_view_is_complete(shop):
    shop.set_needs(budget_inr=1200)
    shop.set_item("prawns", 0.5, "kg")
    snap = shop.snapshot()
    assert snap["total"] == 350 and snap["budget_left"] == 850
    view = shop.view()
    assert view["cart"][0]["te"] == "రొయ్యలు" and view["total_text"] == "₹350"


def test_compare_needs_two(shop):
    assert shop.compare(["prawns"]).code == "need_two"
    r = shop.compare(["vanjaram", "chanduva"])
    assert "₹900" in r.say.en and "₹780" in r.say.en
