import asyncio

import pytest

from ala.coordinator import Coordinator, Superseded, canonical


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(**kw):
    clock = Clock()
    return Coordinator(clock=clock, **kw), clock


def test_canonical_ignores_case_order_and_none():
    assert canonical("set", {"a": "Rice ", "b": 1.0, "c": None}) == canonical("set", {"b": 1, "a": "rice"})


def test_each_speech_advances_the_epoch():
    c, _ = make()
    c.user_started_speaking()
    c.user_stopped_speaking()
    c.user_started_speaking()
    assert c.epoch == 2


def test_late_words_after_a_closed_turn_void_the_plan():
    c, _ = make()
    c.user_started_speaking()
    c.user_stopped_speaking()
    c.heard("add one kilo of prawns")
    c.turn_committed()
    plan_epoch = c.epoch
    c.heard("no wait, half")
    assert c.overtaken(plan_epoch)


def test_noise_without_words_leaves_a_plan_standing():
    c, _ = make()
    c.user_started_speaking()
    c.heard("add rice")
    c.user_stopped_speaking()
    c.turn_committed()
    plan_epoch = c.epoch
    c.user_started_speaking()     # a cough
    c.user_stopped_speaking()
    assert not c.overtaken(plan_epoch)


@pytest.mark.parametrize("words,hold", [
    ("add rice", 0.8),
    ("add rice and", 2.2),
    ("half a kilo of", 2.2),
    ("అర కిలో", 2.2),
    ("one kilo no wait half", 1.8),
    ("రెండు కాదు కాదు మూడు", 1.8),
])
def test_words_stretch_the_hold(words, hold):
    c, _ = make()
    c.open_utterance = words
    assert c.hold() == hold


def test_an_order_waits_longer_and_words_can_stretch_it_further():
    c, _ = make()
    c.open_utterance = "yes"
    assert c.hold(1.4) == 1.4
    c.open_utterance = "yes and"
    assert c.hold(1.4) == 2.2


def test_the_voice_only_ever_adds():
    c, _ = make()
    c.open_utterance = "add rice"
    c.extra_hold = lambda: 0.1
    assert c.hold() == 0.8
    c.extra_hold = lambda: 4.0
    assert c.hold() == 4.0
    c.extra_hold = lambda: 1 / 0
    assert c.hold() == 0.8


async def test_nothing_runs_until_the_hold_has_passed():
    c, clock = make()
    c.user_started_speaking()
    c.heard("add rice")
    c.user_stopped_speaking()
    c.turn_committed()
    ran = []

    async def run():
        ran.append(1)
        return "ok"

    task = asyncio.ensure_future(c.execute("set_item", {"item": "rice"}, run))
    await asyncio.sleep(0.12)
    assert not ran
    clock.t += 0.9
    record = await asyncio.wait_for(task, 1)
    assert record.outcome == "ok" and ran == [1]


async def test_new_words_at_the_gate_drop_the_action():
    c, clock = make()
    c.user_started_speaking()
    c.heard("one kilo of prawns")
    c.user_stopped_speaking()
    c.turn_committed()
    epoch = c.epoch
    ran = []

    async def run():
        ran.append(1)

    task = asyncio.ensure_future(c.execute("set_item", {"qty": 1}, run, epoch=epoch))
    await asyncio.sleep(0.06)
    c.heard("no wait half")
    with pytest.raises(Superseded):
        await asyncio.wait_for(task, 1)
    assert not ran


async def test_the_ledger_refuses_a_repeat_when_asked_to():
    c, clock = make()
    clock.t += 10
    c.turn_committed()
    count = []

    async def run():
        count.append(1)
        return "placed"

    first = await c.execute("place_order", {"hash": "a1"}, run, once=True)
    second = await c.execute("place_order", {"hash": "a1"}, run, once=True)
    assert first.outcome == "ok" and second.outcome == "cached" and second.result == "placed"
    assert len(count) == 1


async def test_a_timed_out_change_is_unknown_and_not_resent():
    c, clock = make()
    clock.t += 10
    c.turn_committed()
    count = []

    async def run():
        count.append(1)
        await asyncio.sleep(1)

    record = await c.execute("set_item", {}, run, timeout_s=0.02)
    assert record.outcome == "unknown" and len(count) == 1


async def test_settle_waits_for_late_words():
    c, clock = make()
    c.user_started_speaking()
    c.user_stopped_speaking()
    c.turn_committed()
    epoch = c.epoch
    task = asyncio.ensure_future(c.settle(epoch, 1.0))
    await asyncio.sleep(0.05)
    assert not task.done()
    c.heard("add rice")     # the turn was already closed, so this advances the epoch
    with pytest.raises(Superseded):
        await asyncio.wait_for(task, 1)


def test_a_transcript_that_beat_the_end_of_speech_is_not_waited_for():
    now = [100.0]
    c = Coordinator(clock=lambda: now[0])
    c.user_started_speaking()
    now[0] += 1.0
    c.heard("half a kilo of prawns")
    now[0] += 0.12
    c.user_stopped_speaking()
    assert not c.transcript_due


def test_an_old_transcript_says_nothing_about_the_last_words():
    now = [100.0]
    c = Coordinator(clock=lambda: now[0])
    c.user_started_speaking()
    c.heard("one kilo of tomatoes")
    now[0] += 2.0
    c.user_stopped_speaking()
    assert c.transcript_due
