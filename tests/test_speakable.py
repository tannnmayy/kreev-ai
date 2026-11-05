import pytest

from ala.language.speakable import polish, speakable, violations


@pytest.mark.parametrize("text,lang,out", [
    ("Half a kilo of prawns, ₹350.", "en", "Half a kilo of prawns, three hundred and fifty rupees."),
    ("అర కిలో రొయ్యలు, ₹350.", "te", "అర కిలో రొయ్యలు, మూడు వందల యాభై రూపాయలు."),
    ("The total is ₹1,130.", "en", "The total is one thousand one hundred and thirty rupees."),
    ("Order 4821 is placed.", "en", "Order four thousand eight hundred and twenty one is placed."),
    ("between 7-9", "en", "between seven to nine"),
    ("**bold** text", "en", "bold text"),
])
def test_speakable(text, lang, out):
    assert speakable(text, lang) == out


@pytest.mark.parametrize("text,out", [
    ("Sure! Half a kilo of prawns.", "Half a kilo of prawns."),
    ("Great choice! That's ₹900.", "That's ₹900."),
    ("Absolutely, I can do that.", "I can do that."),
    ("Okay. Let me check. The total is ₹350.", "The total is ₹350."),
    ("That's ₹900! Anything else?", "That's ₹900."),
    ("సరే, అర కిలో వేశాను.", "అర కిలో వేశాను."),
    ("It's fresh. Is there anything else I can help with?", "It's fresh."),
])
def test_polish(text, out):
    assert polish(text) == out


def test_polish_keeps_the_question_when_it_must_cut():
    out = polish("We have seer fish. It's firm. It's from Bheemili. Which cut do you want?")
    assert out.endswith("Which cut do you want?")
    assert out.count(".") + out.count("?") == 2


def test_polish_drops_a_repeated_acknowledgement():
    assert polish("Noted. The total is ₹350.", recent=["Noted. Half a kilo."]) == "The total is ₹350."


def test_violations():
    assert "banned opener" in violations("Sure, here you go.")
    assert "exclamation mark" in violations("That's fresh!")
    assert "filler closer" in violations("That's ₹350. Let me know if you need more.")
    assert violations("Half a kilo of prawns, ₹350.") == []
