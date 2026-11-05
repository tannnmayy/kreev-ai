import pytest

from ala.shop.catalog import Catalog


@pytest.fixture(scope="module")
def cat():
    return Catalog.load()


def test_catalog_loads_with_clean_rows(cat):
    assert len(cat.products) >= 80
    skus = [p.sku for p in cat.products]
    assert len(skus) == len(set(skus))
    for p in cat.products:
        assert p.price > 0 and p.en and p.te and p.note_en and p.note_te, p.sku
        assert p.step > 0
    for r in cat.recipes:
        for item in r.items:
            assert item["sku"] in cat.by_sku, (r.id, item)
        assert "group" in r.main or r.main["sku"] in cat.by_sku


@pytest.mark.parametrize("name,sku", [
    ("prawns", "royyalu"), ("Royyalu", "royyalu"), ("రొయ్యలు", "royyalu"), ("రొయ్యల", "royyalu"),
    ("tiger prawns", "tiger-royyalu"), ("seer fish", "vanjaram"), ("vanjiram", "vanjaram"), ("వంజరం", "vanjaram"),
    ("pomfret", "chanduva"), ("chanduva", "chanduva"), ("black pomfret", "nalla-chanduva"), ("చందువా", "chanduva"),
    ("rice", "sona-masuri"), ("బియ్యం", "sona-masuri"), ("toor dal", "kandi-pappu"), ("కందిపప్పు", "kandi-pappu"),
    ("ladies finger", "bendakaya"), ("ఉల్లిపాయలు", "ullipayalu"), ("eggs", "eggs"), ("కోడిగుడ్లు", "eggs"),
    ("the fresh prawns", "royyalu"), ("sesame oil", "nuvvula-nune"), ("ghee", "neyyi"),
])
def test_find(cat, name, sku):
    p = cat.find(name)
    assert p is not None and p.sku == sku


def test_find_refuses_nonsense(cat):
    assert cat.find("iphone") is None
    assert cat.find("salmon fillets") is None
    assert cat.find("") is None


@pytest.mark.parametrize("text,skus", [
    ("add half a kilo of prawns and one kilo of rice", ["royyalu", "sona-masuri"]),
    ("అర కిలో రొయ్యలు కిలో బియ్యం", ["royyalu", "sona-masuri"]),
    ("tiger prawns please", ["tiger-royyalu"]),
    ("రెండు కిలోల చందువా", ["chanduva"]),
    ("no onions", ["ullipayalu"]),
])
def test_mentions(cat, text, skus):
    assert [m.product.sku for m in cat.mentions(text)] == skus


def test_vegetarian_filter_keeps_fish_out(cat):
    assert all(p.vegetarian for p in cat.search(vegetarian=True, limit=200))


def test_search_prefers_named_then_cheap(cat):
    assert cat.search(text="prawns", tags=["fry"], limit=5)[0].sku == "royyalu"


def test_out_of_stock_is_hidden_and_live_stock_wins(cat):
    assert "pulasa" not in [p.sku for p in cat.search(category="fish", limit=50)]
    assert "royyalu" not in [p.sku for p in cat.search(category="fish", stock={"royyalu": 0}, limit=50)]


def test_substitutes_stay_close(cat):
    subs = cat.substitutes("pulasa")
    assert subs and all(p.group == "big-sea-fish" for p in subs)
    cheap = cat.substitutes("vanjaram", max_price=700)
    assert cheap and all(p.price <= 700 for p in cheap)


def test_recipes(cat):
    assert cat.recipe("I want to make chepala pulusu").id == "chepala-pulusu"
    assert cat.recipe("చేపల పులుసు చేయాలి").id == "chepala-pulusu"
    assert cat.recipe("gongura pappu for six").id == "gongura-pappu"
    assert cat.recipe("nothing here") is None
