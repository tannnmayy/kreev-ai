"""Bugs the first real-audio run found, kept as tests."""
import time

import numpy as np
import pytest
from livekit import rtc

from ala.bench.deck import instant_coordinator, spoken_turn
from ala.coordinator import Coordinator
from ala.minds import rules
from ala.minds.brain import Brain
from ala.perception.features import Features, speech_level
from ala.shop.catalog import Catalog
from ala.shop.engine import ShopEngine
from ala.voice.listening import Perception

CAT = Catalog.load()


def perception():
    decisions = []
    stops = []
    p = Perception(Coordinator(), CAT, publish=lambda m: decisions.append(m), interrupt=lambda: stops.append(1),
                   trace=lambda *a, **k: None, notice_side_talk=lambda: None)
    return p, decisions, stops


def test_a_backchannel_is_still_one_after_barge_in_has_paused_ala():
    p, _, stops = perception()
    p.ala_speaking = True
    p.speech_started()
    p.ala_speaking = False         # the pause comes before the transcript does
    assert p.on_transcript("Mm hmm.", False) is False
    assert p.on_transcript("Mm hmm.", True) is False
    assert not stops


def test_overlap_lasts_only_for_the_words_it_began_with():
    p, _, _ = perception()
    p.ala_speaking = True
    p.speech_started()
    p.ala_speaking = False
    p.on_transcript("Mm hmm.", True)
    assert p.on_transcript("okay", True) is True


def test_mm_hmm_with_ala_silent_is_a_turn():
    p, _, _ = perception()
    p.speech_started()
    assert p.on_transcript("Mm hmm.", True) is True


def test_a_stop_word_over_a_paused_reply_really_stops_it():
    p, _, stops = perception()
    p.ala_speaking = True
    p.speech_started()
    p.ala_speaking = False
    assert p.on_transcript("Stop.", True) is True
    assert stops


@pytest.mark.parametrize("said,want", [
    (["what is in my cart"], "what is in my cart"),
    (["one kilo of rice", "no wait, two"], "one kilo of rice no wait, two"),
    (["what is in my cart", "Stop."], "Stop."),
    (["what is in my cart", "Stop.", "Place the order."], "Place the order."),
    (["add rice", "stop", "add dal", "no, add milk"], "add dal no, add milk"),
    ([], ""),
])
def test_a_stop_word_closes_what_came_before_it(said, want):
    assert rules.open_request(said) == want


@pytest.mark.parametrize("words", ["what is in my cart place the order", "what's my total, place the order"])
def test_asking_for_the_cart_and_the_order_together_reads_back_the_order(words):
    shop = ShopEngine(CAT)
    shop.set_item("sona-masuri", 1, "kg", None)
    p = rules.plan(words, shop)
    assert [s.name for s in p.steps] == ["review_order"]


def test_a_plain_cart_question_still_reads_the_cart():
    shop = ShopEngine(CAT)
    shop.set_item("sona-masuri", 1, "kg", None)
    assert rules.plan("what's in my cart", shop).route == "reading"


async def test_after_cutting_off_a_read_back_the_customer_can_still_order():
    brain = Brain(ShopEngine(CAT), None, instant_coordinator())
    await spoken_turn(brain, "one kilo of rice")
    await spoken_turn(brain, "place the order")
    brain.reply_heard(brain.last_reply, "One kilo", True)
    said = [s.text async for s in brain.respond("yes", epoch=_turn(brain, "yes"))]
    assert not brain.shop.state.orders and "?" in " ".join(said)
    brain.reply_heard(brain.last_reply, brain.last_reply, False)
    await spoken_turn(brain, "yes")
    assert len(brain.shop.state.orders) == 1


def _turn(brain, words):
    c = brain.coord
    c.user_started_speaking()
    c.heard(words)
    c.user_stopped_speaking()
    c.turn_committed()
    return c.epoch


class _Outcome:
    def __init__(self, ok):
        self.ok = ok


async def test_the_ledger_remembers_what_happened_and_not_what_was_refused():
    c = instant_coordinator()
    runs = []

    async def refuse():
        runs.append("no")
        return _Outcome(False)

    async def accept():
        runs.append("yes")
        return _Outcome(True)

    c.turn_committed()
    await c.execute("place_order", {"hash": "a"}, refuse, once=True)
    await c.execute("place_order", {"hash": "a"}, accept, once=True)
    repeat = await c.execute("place_order", {"hash": "a"}, accept, once=True)
    assert runs == ["no", "yes"] and repeat.outcome == "cached"


def _speech(amplitude, seconds=1.5, rate=16000):
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))).astype(np.float32)


def _feed(p, pcm, rate=16000):
    frame = rtc.AudioFrame((pcm * 32767).astype(np.int16).tobytes(), rate, 1, len(pcm))
    p.on_audio(frame)


def test_speech_level_agrees_with_the_slower_analysis():
    from ala.perception.features import analyse
    pcm = _speech(0.3)
    assert speech_level(pcm) == pytest.approx(analyse(pcm).loud_db)
    assert speech_level(np.zeros(16000, dtype=np.float32)) is None


def _heard_customer(p, level=-12.0):
    p.listener.baseline.add(Features(on=0, off=1, speech_s=1, loud_db=level))


def test_a_quiet_voice_over_ala_is_background_before_its_segment_has_been_measured():
    p, decisions, stops = perception()
    _heard_customer(p)
    p.ala_speaking = True
    p.speech_started()
    p._speech_from = time.time() - 0.1
    _feed(p, _speech(0.03))
    p.ala_speaking = False
    line = "the match will begin after the news at nine"
    assert p.on_transcript(line, False) is False
    assert p.on_transcript(line, True) is False
    assert not stops


def test_a_quiet_customer_asking_for_something_is_still_heard():
    p, _, _ = perception()
    _heard_customer(p)
    p.ala_speaking = True
    p.speech_started()
    p._speech_from = time.time() - 0.1
    _feed(p, _speech(0.03))
    assert p.on_transcript("half a kilo of prawns", True) is True


def test_a_voice_at_the_customers_level_still_interrupts():
    p, _, stops = perception()
    _heard_customer(p)
    p.ala_speaking = True
    p.speech_started()
    p._speech_from = time.time() - 0.1
    _feed(p, _speech(0.3))
    assert p.on_transcript("my father is diabetic so nothing sweet", True) is True
    assert stops
