import pytest

from ala.language.numbers import (en_number, find_money, find_quantities, say_money, say_quantity, show_money,
                                  show_quantity, te_number)


def first(text):
    q = find_quantities(text)
    assert q, "no quantity in %r" % text
    return q[0].in_base()


@pytest.mark.parametrize("text,value,unit", [
    ("half a kilo of prawns", 0.5, "kg"),
    ("half kilo prawns", 0.5, "kg"),
    ("a kilo of rice", 1, "kg"),
    ("one kilo", 1, "kg"),
    ("1 kg", 1, "kg"),
    ("2kg", 2, "kg"),
    ("2 kgs of onions", 2, "kg"),
    ("500 grams", 0.5, "kg"),
    ("500g", 0.5, "kg"),
    ("250 gm", 0.25, "kg"),
    ("quarter kilo", 0.25, "kg"),
    ("three quarters of a kilo", 0.75, "kg"),
    ("one and a half kilos", 1.5, "kg"),
    ("a kilo and a half", 1.5, "kg"),
    ("two and a half kilos", 2.5, "kg"),
    ("1.5 kg", 1.5, "kg"),
    ("a dozen eggs", 1, "dozen"),
    ("half a dozen eggs", 0.5, "dozen"),
    ("two dozen", 2, "dozen"),
    ("six pieces", 6, "piece"),
    ("two packets", 2, "pack"),
    ("a litre of milk", 1, "litre"),
    ("half a litre", 0.5, "litre"),
    ("500 ml", 0.5, "litre"),
    ("one bunch of coriander", 1, "bunch"),
    ("make it two", 2, None),
    # Telugu
    ("అర కిలో రొయ్యలు", 0.5, "kg"),
    ("పావు కిలో", 0.25, "kg"),
    ("ముప్పావు కిలో", 0.75, "kg"),
    ("ఒక కిలో", 1, "kg"),
    ("కిలో రొయ్యలు", 1, "kg"),
    ("రెండు కిలోలు", 2, "kg"),
    ("రెండు కిలోల చందువా", 2, "kg"),
    ("ఒకటిన్నర కిలో", 1.5, "kg"),
    ("రెండున్నర కిలోలు", 2.5, "kg"),
    ("ఐదు వందల గ్రాములు", 0.5, "kg"),
    ("అర డజను", 0.5, "dozen"),
    ("డజను గుడ్లు", 1, "dozen"),
    ("అర లీటరు పాలు", 0.5, "litre"),
    ("రెండు కట్టలు", 2, "bunch"),
    ("2 కిలోలు", 2, "kg"),
    # Telugu in Latin letters
    ("ara kilo royyalu", 0.5, "kg"),
    ("rendu kilolu", 2, "kg"),
    ("okatinnara kilo", 1.5, "kg"),
    # Hindi a Telugu speaker may borrow
    ("aadha kilo", 0.5, "kg"),
    ("do kilo", 2, "kg"),
    ("dedh kilo", 1.5, "kg"),
])
def test_quantities(text, value, unit):
    assert first(text) == (value, unit)


def test_several_quantities_in_one_sentence():
    qs = find_quantities("one kilo no wait half a kilo")
    assert [(q.value, q.unit) for q in qs] == [(1, "kg"), (0.5, "kg")]


def test_digits_are_not_continued_by_words():
    qs = find_quantities("my budget is 1200 one kilo of rice")
    assert [(q.value, q.unit) for q in qs] == [(1200, None), (1, "kg")]


@pytest.mark.parametrize("text,amount", [
    ("my budget is 1200", 1200),
    ("under ₹900", 900),
    ("nine hundred rupees", 900),
    ("twelve hundred", 1200),
    ("one thousand two hundred", 1200),
    ("three hundred and fifty rupees", 350),
    ("1,200 rupees", 1200),
    ("పన్నెండు వందలు", 1200),
    ("వెయ్యి రెండు వందలు", 1200),
    ("తొమ్మిది వందల రూపాయలు", 900),
    ("మూడు వందల యాభై రూపాయలు", 350),
    ("నూట యాభై రూపాయలు", 150),
    ("రెండు వేల ఐదు వందలు", 2500),
    ("Budget 1200 లో", 1200),
])
def test_money(text, amount):
    assert find_money(text) == amount


def test_a_weight_is_not_money():
    assert find_money("500 grams of prawns") is None


@pytest.mark.parametrize("n,words", [
    (5, "ఐదు"), (15, "పదిహేను"), (45, "నలభై ఐదు"), (100, "వంద"), (150, "నూట యాభై"), (300, "మూడు వందలు"),
    (350, "మూడు వందల యాభై"), (1000, "వెయ్యి"), (1130, "వెయ్యి నూట ముప్పై"), (1200, "వెయ్యి రెండు వందలు"),
    (2000, "రెండు వేలు"), (2500, "రెండు వేల ఐదు వందలు"),
])
def test_telugu_numbers(n, words):
    assert te_number(n) == words


def test_telugu_money_uses_the_form_before_a_noun():
    assert say_money(300, "te") == "మూడు వందల రూపాయలు"
    assert say_money(350, "te") == "మూడు వందల యాభై రూపాయలు"
    assert say_money(2000, "te") == "రెండు వేల రూపాయలు"
    assert say_money(100, "te") == "వంద రూపాయలు"
    assert say_money(1, "te") == "ఒక రూపాయి"


def test_english_numbers():
    assert en_number(350) == "three hundred and fifty"
    assert en_number(1130) == "one thousand one hundred and thirty"
    assert say_money(900, "en") == "nine hundred rupees"


@pytest.mark.parametrize("value,unit,lang,words", [
    (0.5, "kg", "te", "అర కిలో"), (0.25, "kg", "te", "పావు కిలో"), (0.75, "kg", "te", "ముప్పావు కిలో"),
    (1, "kg", "te", "ఒక కిలో"), (2, "kg", "te", "రెండు కిలోలు"), (1.5, "kg", "te", "ఒకటిన్నర కిలో"),
    (2.5, "kg", "te", "రెండున్నర కిలోలు"), (1, "dozen", "te", "ఒక డజను"), (2, "litre", "te", "రెండు లీటర్లు"),
    (0.5, "kg", "en", "half a kilo"), (0.25, "kg", "en", "a quarter kilo"), (1, "kg", "en", "one kilo"),
    (1.5, "kg", "en", "one and a half kilos"), (2, "kg", "en", "two kilos"), (1, "dozen", "en", "one dozen"),
])
def test_say_quantity(value, unit, lang, words):
    assert say_quantity(value, unit, lang) == words


def test_screen_forms():
    assert show_money(1130) == "₹1,130"
    assert show_money(120000) == "₹1,20,000"
    assert show_quantity(0.5, "kg") == "0.5 kg"
    assert show_quantity(2, "piece") == "2 pcs"
