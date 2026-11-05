import numpy as np
import pytest

from ala.perception import overlap
from ala.perception.features import Baseline, Features, analyse, level_db, median_pitch, pitch_track, raised, to_16k
from ala.perception.reading import Listener, hedged_values, read
from ala.perception.turn import SMART_TURN, assess
from ala.shop.catalog import Catalog

SR = 16000


def voiced(f0=150.0, seconds=1.0, amp=0.1, harmonics=5):
    t = np.arange(int(SR * seconds)) / SR
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, harmonics + 1))
    return (amp * x / np.max(np.abs(x))).astype(np.float32)


@pytest.mark.parametrize("f0", [90.0, 120.0, 180.0, 240.0, 320.0])
def test_yin_tracks_pitch(f0):
    assert abs(median_pitch(voiced(f0)) - f0) < 2.0


def test_yin_finds_nothing_in_silence():
    assert (pitch_track(np.zeros(SR)) == 0).all()


def test_yin_is_fast_enough_for_a_live_call():
    import time
    x = voiced(seconds=5.0)
    t = time.perf_counter()
    pitch_track(x)
    assert time.perf_counter() - t < 0.25


def test_analyse_measures_level_and_pauses():
    x = np.concatenate([voiced(seconds=0.6), np.zeros(int(SR * 0.5), dtype=np.float32), voiced(seconds=0.6)])
    f = analyse(x, t0=10.0)
    assert f is not None
    assert 0.45 <= f.longest_pause_s <= 0.55
    assert 1.1 <= f.speech_s <= 1.3
    assert f.on == pytest.approx(10.0, abs=0.05)
    assert abs(f.f0_hz - 150) < 3


def test_too_little_speech_is_not_measured():
    assert analyse(np.zeros(SR // 8, dtype=np.float32)) is None
    assert analyse(np.zeros(SR, dtype=np.float32)) is None


def test_resampling_keeps_duration():
    assert abs(len(to_16k(np.zeros(48000), 48000)) - 16000) <= 1


def seg(loud=-20.0, tilt=-20.0, f0=150.0, words=""):
    return Features(loud_db=loud, tilt_db=tilt, f0_hz=f0, words=words, worded=bool(words), speech_s=1.0)


def test_baseline_needs_two_segments_then_compares():
    b = Baseline()
    assert b.effort(seg()) == {}
    b.add(seg())
    b.add(seg())
    d = b.effort(seg(loud=-12, tilt=-10, f0=200))
    assert d["loud"] == pytest.approx(8) and d["tilt"] == pytest.approx(10)
    assert d["pitch"] == pytest.approx(12 * np.log2(200 / 150))


def test_raised_needs_two_cues_or_a_loud_one():
    assert raised({"tilt": 7, "pitch": 5}) and raised({"loud": 9})
    assert not raised({"tilt": 7}) and not raised({"loud": 5})


def listener_with_baseline():
    l = Listener()
    for _ in range(3):
        l.add(seg())
    return l


def test_a_shouted_stop_is_urgent_but_a_calm_one_is_not():
    l = listener_with_baseline()
    calm = l.add(seg())
    calm.words = "no wait"
    assert not read([calm], "no wait").urgent
    loud = l.add(seg(loud=-10, tilt=-8, f0=230))
    loud.words = "no wait"
    r = read([loud], "no wait")
    assert r.urgent and r.label == "urgent" and "short and calm" in r.note()


def test_a_loud_speaker_is_not_scolded():
    l = listener_with_baseline()
    loud = l.add(seg(loud=-10, tilt=-8, f0=230))
    loud.words = "add two kilos of rice"
    r = read([loud], "add two kilos of rice")
    assert r.raised_voice and not r.urgent and not r.frustrated and r.note() == ""


def test_frustration_in_the_words_calms_the_reply():
    r = read([seg(words="I said one kilo")], "I said one kilo")
    assert r.frustrated and "sorry once" in r.note().lower()
    r = read([seg(words="ఎన్నిసార్లు చెప్పాలి")], "ఎన్నిసార్లు చెప్పాలి")
    assert r.frustrated


@pytest.mark.parametrize("words,unsure", [
    ("around 500 grams of prawns", True), ("two kilos I think", True), ("రెండు కిలోలు అనుకుంటా", True), ("సుమారు అర కిలో", True),
    ("I think I want rice", False), ("two kilos of rice", False),
])
def test_a_hedge_must_sit_next_to_a_value(words, unsure):
    assert bool(hedged_values(words)) == unsure
    assert read([seg(words=words)], words).unsure == unsure


def test_a_long_pause_before_the_value_means_unsure():
    a = seg(words="half a")
    a.on, a.off = 0.0, 1.0
    b = seg(words="kilo of rice")
    b.on, b.off = 2.2, 3.0
    r = read([a, b], "half a kilo of rice")
    assert r.unsure and r.hesitant


def test_words_find_their_segment_in_order():
    l = Listener()
    a = l.expect(10.0)
    b = l.expect(10.5)
    l.add(seg(), into=a)
    l.add(seg(), into=b)
    l.heard("first", 10.3)
    l.heard("second", 11.0)
    assert a.words == "first" and b.words == "second"


def test_a_raised_voice_waits_for_its_words():
    l = listener_with_baseline()
    loud = l.expect(10.0)
    l.add(seg(loud=-10, tilt=-8, f0=230), into=loud)
    assert l.awaiting_words()
    l.heard("stop", 10.5)
    assert not l.awaiting_words()


# --- decision 1: has the customer finished? --------------------------------------------------------------------

HOLDS = dict(base_s=0.8, dangling_s=2.2, revising_s=1.8)


def test_a_plain_sentence_keeps_the_base_wait():
    assert assess("add rice", **HOLDS).hold_s == 0.8


def test_words_only_stretch_the_wait():
    assert assess("half a kilo of", **HOLDS).hold_s == 2.2
    assert assess("one kilo no wait half", **HOLDS).hold_s == 1.8
    assert assess("that's all", **HOLDS).hold_s == 0.8        # "finished" words never shorten it


def test_the_voice_can_stretch_the_wait():
    a = assess("add rice", p_end=0.1, **HOLDS)
    assert a.hold_s == 1.6 and "voice" in a.reasons[0]
    assert assess("add rice", p_end=0.9, **HOLDS).hold_s == 0.8


def test_a_short_answer_to_alas_question_needs_no_extra_wait():
    assert assess("yes", expecting_answer=True, p_end=0.1, **HOLDS).hold_s == 0.8
    assert assess("yes and", expecting_answer=True, **HOLDS).hold_s == 2.2


def test_a_raised_voice_without_words_holds_longest():
    assert assess("", raised_waiting=True, **HOLDS).hold_s == 4.0


def test_smart_turn_scores_audio_when_available():
    p = SMART_TURN.score(voiced(seconds=2.0))
    assert p is None or 0.0 <= p <= 1.0


# --- decisions 2 and 3: talked over ----------------------------------------------------------------------------

CAT = Catalog.load()


def over(words, **kw):
    kw.setdefault("ala_speaking", True)
    kw.setdefault("said_now", "Vanjaram is nine hundred rupees a kilo and it is very firm")
    return overlap.classify(words, catalog=CAT, **kw)


@pytest.mark.parametrize("words", ["mm-hm", "okay", "సరే", "ఆ", "ఊ", "right"])
def test_backchannels_let_ala_carry_on(words):
    v = over(words)
    assert v.kind == "backchannel" and not v.stop and not v.reply


@pytest.mark.parametrize("words", ["wait", "stop", "ఆగండి", "ఒక్క నిమిషం"])
def test_stop_words_stop_ala(words):
    v = over(words)
    assert v.kind == "interruption" and v.stop and v.reply


def test_a_shouted_okay_means_stop():
    assert over("OKAY OKAY", raised_voice=True).stop


def test_real_words_over_ala_are_an_interruption():
    v = over("the second one please")
    assert v.kind == "interruption" and v.stop and v.reply


def test_alas_own_voice_is_ignored():
    v = over("nine hundred rupees a kilo")
    assert v.kind == "echo" and not v.stop and not v.reply


def test_a_customer_repeating_a_different_thing_is_not_echo():
    assert over("add one kilo of rice").kind == "interruption"


@pytest.mark.parametrize("words", ["అమ్మా, చేపలు కావాలా", "ఏమండీ ఏం తెమ్మంటారు", "Mom do you want fish"])
def test_side_talk_gets_no_reply(words):
    v = over(words)
    assert v.kind == "side_talk" and not v.reply
    assert overlap.classify(words, ala_speaking=False, catalog=CAT).kind == "side_talk"


def test_quiet_talk_about_nothing_is_background():
    v = overlap.classify("the bus was late again today", ala_speaking=False, level_vs_usual=-12, catalog=CAT)
    assert v.kind == "background" and not v.reply


def test_quiet_shopping_words_still_count():
    v = overlap.classify("add one kilo of rice", ala_speaking=False, level_vs_usual=-12, catalog=CAT)
    assert v.kind == "speech" and v.reply


def test_no_words_is_noise():
    assert over("").kind == "noise"


def test_backchannel_words_when_ala_is_quiet_are_an_ordinary_turn():
    v = overlap.classify("okay", ala_speaking=False, catalog=CAT)
    assert v.reply
