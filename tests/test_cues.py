import pytest

from ala.language import cues
from ala.language.detect import LanguageTracker, classify


@pytest.mark.parametrize("text,kind", [
    ("stop", "stop"), ("Wait.", "stop"), ("wait wait", "stop"), ("hold on", "stop"), ("ఆగండి", "stop"), ("ఒక్క నిమిషం", "stop"),
    ("go on", "resume"), ("చెప్పండి", "resume"), ("say that again", "repeat"), ("what?", "repeat"), ("మళ్ళీ చెప్పండి", "repeat"),
    ("ఏమన్నారు", "repeat"), ("mm-hm", "backchannel"), ("Mm-hm, okay.", "backchannel"), ("okay", "backchannel"),
    ("సరే", "backchannel"), ("ఆ", "backchannel"), ("ఊ", "backchannel"), ("హా", "backchannel"), ("right", "backchannel"),
])
def test_control(text, kind):
    assert cues.control(text) == kind


@pytest.mark.parametrize("text", ["okay add the rice", "wait make it two", "stop adding prawns", "సరే రొయ్యలు వేయండి", "what is the price of prawns"])
def test_control_needs_the_whole_utterance(text):
    assert cues.control(text) is None


@pytest.mark.parametrize("text", ["yes", "Yes.", "yeah", "ok", "okay", "place it", "yes place the order", "go ahead", "అవును", "సరే", "పెట్టండి", "ఆర్డర్ పెట్టండి", "avunu"])
def test_plain_yes(text):
    assert cues.is_yes(text)


@pytest.mark.parametrize("text", ["yes but make it two", "ok add rice", "yes and also onions", "అవును కానీ రెండు కిలోలు", "no", "వద్దు"])
def test_not_a_plain_yes(text):
    assert not cues.is_yes(text)


@pytest.mark.parametrize("text", ["no", "No.", "not yet", "cancel", "వద్దు", "కాదు", "vaddu", "no thanks"])
def test_plain_no(text):
    assert cues.is_no(text)


@pytest.mark.parametrize("text", [
    "half a kilo of", "add rice and", "i want", "um", "అర కిలో", "రొయ్యలు ఇంకా", "add prawns,", "let me think", "రెండు కిలోలు",
])
def test_unfinished(text):
    assert cues.unfinished(text)


@pytest.mark.parametrize("text", ["half a kilo of prawns", "add rice", "that's all", "అర కిలో రొయ్యలు వేయండి"])
def test_finished_sentences_are_not_unfinished(text):
    assert not cues.unfinished(text)


@pytest.mark.parametrize("text", ["one kilo no wait half", "make it two", "actually the pomfret", "రెండు కాదు కాదు మూడు", "I mean rice"])
def test_revising(text):
    assert cues.revising(text)


@pytest.mark.parametrize("text", ["that's all", "అంతే", "చాలు", "nothing else"])
def test_finished_marker(text):
    assert cues.finished_marker(text)


@pytest.mark.parametrize("text", ["two kilos I think", "maybe one kilo", "around 500 grams", "రెండు కిలోలు అనుకుంటా", "సుమారు అర కిలో"])
def test_hedges(text):
    assert cues.hedge(text)


@pytest.mark.parametrize("text", ["how much is prawns", "what do you have", "రొయ్యలు ఉన్నాయా", "ధర ఎంత", "is it fresh", "పులస ఉందా"])
def test_questions(text):
    assert cues.is_question(text)


@pytest.mark.parametrize("text", ["add prawns", "half a kilo of rice", "రొయ్యలు వేయండి"])
def test_not_questions(text):
    assert not cues.is_question(text)


@pytest.mark.parametrize("text", ["అమ్మా, చేపలు కావాలా", "ఏమండీ ఏం తెమ్మంటారు", "Mom do you want fish", "hey dear what should I get"])
def test_side_talk(text):
    assert cues.addressed_elsewhere(text)


def test_talking_to_ala_is_not_side_talk():
    assert not cues.addressed_elsewhere("add one kilo of prawns")


@pytest.mark.parametrize("text,fn", [
    ("what's in my cart", cues.asks_cart), ("కార్ట్‌లో ఏముంది", cues.asks_cart), ("what's the total", cues.asks_total),
    ("మొత్తం ఎంత", cues.asks_total), ("what was my budget", cues.asks_budget), ("ఎంత మిగిలింది", cues.asks_budget),
    ("undo", cues.wants_undo), ("clear my cart", cues.wants_clear), ("అన్నీ తీసేయండి", cues.wants_clear),
    ("place the order", cues.wants_checkout), ("ఆర్డర్ పెట్టండి", cues.wants_checkout), ("remove the prawns", cues.wants_remove),
    ("రొయ్యలు తీసేయండి", cues.wants_remove), ("add two kilos", cues.wants_add), ("రొయ్యలు వేయండి", cues.wants_add),
    ("అర కిలో రొయ్యలు add చెయ్యి", cues.wants_add), ("two more", cues.wants_more), ("ఇంకో కిలో", cues.wants_more),
    ("make it two", cues.wants_set),
])
def test_intents(text, fn):
    assert fn(text)


@pytest.mark.parametrize("text,cut", [
    ("cleaned please", "cleaned"), ("శుభ్రం చేసి ఇవ్వండి", "cleaned"), ("whole", "whole"), ("మొత్తంగా", "whole"), ("curry cut", "curry cut"),
    ("కూర ముక్కలు", "curry cut"), ("steaks", "steaks"),
])
def test_cuts(text, cut):
    assert cues.cut_in(text) == cut


@pytest.mark.parametrize("text,n", [("the second one", 2), ("first", 1), ("రెండోది", 2), ("మొదటిది", 1), ("the last one", -1)])
def test_ordinals(text, n):
    assert cues.ordinal_in(text) == n


def test_cheapest():
    assert cues.wants_cheapest("the cheaper one") and cues.wants_cheapest("తక్కువ ధరది")


@pytest.mark.parametrize("text,lang", [
    ("add half a kilo of prawns", "en"), ("అర కిలో రొయ్యలు వేయండి", "te"), ("అర కిలో రొయ్యలు add చెయ్యి", "te"), ("cart lo add cheyyi", "te"),
    ("Budget 1200 లో Sunday lunch కి నలుగురికి ఏమి బాగుంటుంది", "te"), ("aadha kilo chahiye", "hi"), ("मुझे आधा किलो चाहिए", "hi"), ("", ""),
])
def test_classify(text, lang):
    assert classify(text) == lang


def test_one_english_word_does_not_flip_the_reply():
    t = LanguageTracker(reply="te", last_heard="te")
    assert t.hear("okay") == "te"
    assert t.hear("cart") == "te"


def test_a_full_sentence_switches():
    t = LanguageTracker()
    assert t.hear("అర కిలో రొయ్యలు వేయండి") == "te"
    assert t.hear("what is the total price") == "en"
    assert t.hear("మొత్తం ఎంత అయింది") == "te"


def test_pinning_holds_until_released():
    t = LanguageTracker()
    assert t.hear("speak in Telugu") == "te"
    assert t.hear("add one kilo of rice") == "te"
    assert t.hear("speak English now") == "en"
    assert t.hear("రొయ్యలు వేయండి") == "en"


def test_hindi_is_noted_and_does_not_flip_the_reply():
    t = LanguageTracker(reply="te", last_heard="te")
    assert t.hear("aadha kilo chahiye") == "te"
    assert t.hindi_seen
