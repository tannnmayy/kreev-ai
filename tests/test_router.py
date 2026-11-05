import asyncio
import json

import pytest

from ala.minds.router import AllBusy, Limits, Provider, Router, parse_json, retry_after


def provider(name, **limits):
    return Provider(name, "http://x", name + "-model", key="k", limits=Limits(**limits))


def ok(text="hello", tokens=10):
    return 200, {}, json.dumps({"choices": [{"message": {"content": text}}], "usage": {"total_tokens": tokens}})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(script, *names, chain=None, **limits):
    """`script` maps a provider name to a list of replies; a callable reply is awaited."""
    clock = Clock()
    calls = []

    async def http(p, payload, timeout):
        calls.append((p.name, payload))
        reply = script[p.name].pop(0) if len(script[p.name]) > 1 else script[p.name][0]
        if callable(reply):
            return await reply()
        return reply

    providers = {n: provider(n, **limits) for n in names}
    router = Router(chains={"fast": list(chain or names)}, providers=providers, clock=clock, http=http)
    return router, clock, calls


MSG = [{"role": "user", "content": "hi"}]


async def test_first_healthy_provider_answers():
    r, _, calls = make({"a": [ok("from a")], "b": [ok("from b")]}, "a", "b")
    c = await r.complete("fast", MSG)
    assert c.text == "from a" and c.provider == "a"
    assert [n for n, _ in calls] == ["a"]


async def test_a_429_hands_over_and_rests_the_provider():
    r, clock, calls = make({"a": [(429, {"retry-after": "20"}, "slow down"), ok("a again")], "b": [ok("from b")]}, "a", "b")
    c = await r.complete("fast", MSG)
    assert c.provider == "b"
    assert not r.has_room("a", 100)
    clock.t += 21
    assert r.has_room("a", 100)
    assert (await r.complete("fast", MSG)).provider == "a"


async def test_a_daily_limit_rests_for_an_hour_at_least():
    r, clock, _ = make({"a": [(429, {}, "quota exceeded: requests per day")], "b": [ok()]}, "a", "b")
    await r.complete("fast", MSG)
    clock.t += 600
    assert not r.has_room("a", 100)


async def test_a_bad_key_is_never_tried_again():
    r, clock, calls = make({"a": [(401, {}, "bad key")], "b": [ok()]}, "a", "b")
    await r.complete("fast", MSG)
    clock.t += 100000
    assert not r.has_room("a", 100)


async def test_budgets_are_spent_before_the_api_says_no():
    r, clock, calls = make({"a": [ok()], "b": [ok("from b")]}, "a", "b", rpm=2)
    await r.complete("fast", MSG)
    await r.complete("fast", MSG)
    r.meters["b"].minute.clear()
    c = await r.complete("fast", MSG)
    assert c.provider == "b"
    clock.t += 61
    assert (await r.complete("fast", MSG)).provider == "a"


async def test_token_budget_counts():
    r, _, _ = make({"a": [ok(tokens=900)]}, "a", tpm=1000)
    await r.complete("fast", MSG, est_tokens=900)
    assert not r.has_room("a", 900)


async def test_a_hanging_provider_gets_a_hedged_backup(monkeypatch):
    monkeypatch.setitem(__import__("ala.minds.router", fromlist=["HEDGE_AFTER_S"]).HEDGE_AFTER_S, "fast", 0.02)

    async def hang():
        await asyncio.sleep(5)
        return ok("too late")

    r, _, calls = make({"a": [hang], "b": [ok("from b")]}, "a", "b")
    c = await asyncio.wait_for(r.complete("fast", MSG, timeout_s=3), 1)
    assert c.provider == "b"


async def test_all_busy_is_reported_not_hidden():
    r, _, _ = make({"a": [(500, {}, "boom")]}, "a")
    with pytest.raises(AllBusy):
        await r.complete("fast", MSG)


async def test_unsupported_settings_are_dropped_and_remembered():
    seen = []

    async def http(p, payload, timeout):
        seen.append(dict(payload))
        if "reasoning_effort" in payload:
            return 400, {}, "unknown field"
        return ok()

    p = Provider("a", "http://x", "m", key="k", extra={"reasoning_effort": "none"})
    r = Router(chains={"fast": ["a"]}, providers={"a": p}, http=http)
    await r.complete("fast", MSG)
    await r.complete("fast", MSG)
    assert "reasoning_effort" in seen[0] and "reasoning_effort" not in seen[1] and "reasoning_effort" not in seen[-1]


def test_no_key_means_not_usable():
    p = Provider("a", "http://x", "m", key_env="SOME_KEY_THAT_IS_NOT_SET")
    r = Router(chains={"fast": ["a"]}, providers={"a": p})
    assert not r.available and r.candidates("fast", 10) == []


def test_status_never_leaks_a_key():
    r, _, _ = make({"a": [ok()]}, "a")
    assert "k" not in json.dumps(r.status()["a"]["last_error"])
    assert "key" not in str(r.status()).replace("configured", "")


@pytest.mark.parametrize("headers,body,seconds", [
    ({"retry-after": "12"}, "", 12), ({}, "Please retry in 36.9s", 36.9), ({}, "'retryDelay': '36s'", 36),
    ({}, "Please try again in 1m30.5s", 90.5), ({}, "nothing useful", 30),
])
def test_retry_after(headers, body, seconds):
    assert retry_after(headers, body) == pytest.approx(seconds)


@pytest.mark.parametrize("text,out", [
    ('{"a": 1}', {"a": 1}), ('```json\n{"a": 1}\n```', {"a": 1}), ('<think>hmm {x}</think>{"a": 1}', {"a": 1}),
    ('Here you go: {"a": {"b": "}"}} thanks', {"a": {"b": "}"}}), ("no json here", None), ("[1,2]", None),
])
def test_parse_json(text, out):
    assert parse_json(text) == out
