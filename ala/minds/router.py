"""The quota-aware router: which free model answers this turn?

At ₹0 the scarce things are requests per day and tokens per minute. Each provider has a budget here. The router takes the
first one in the chain that is healthy and has room, and it moves on by itself:
- a 429 rests that provider for as long as the API asks;
- a daily limit rests it until the next day;
- a request that hangs gets a hedged backup on the next provider, and whichever answers first wins. Duet saw 26 of about
  350 free-tier requests hang with no answer at all.

Every provider speaks the OpenAI chat format, so Gemini, Groq and a local llama.cpp server are the same code. One account per
service: limits follow the account, so extra keys wouldn't help and could get keys revoked mid-evaluation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

import aiohttp

log = logging.getLogger("ala.router")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GROQ_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class Limits:
    rpm: Optional[int] = None
    rpd: Optional[int] = None
    tpm: Optional[int] = None
    tpd: Optional[int] = None


@dataclass
class Provider:
    name: str
    base_url: str
    model: str
    key_env: str = ""
    limits: Limits = field(default_factory=Limits)
    extra: Dict[str, Any] = field(default_factory=dict)     # extra request fields, dropped if the API refuses them
    json_mode: bool = True
    key: Optional[str] = None

    def api_key(self) -> str:
        return self.key if self.key is not None else os.environ.get(self.key_env, "")

    @property
    def usable(self) -> bool:
        return bool(self.api_key()) or not self.key_env


# Free-tier figures, as reported on 5 Nov 2026. Google no longer publishes per-model limits, so its numbers are what users
# report and the real ones are on the project's AI Studio page. A wrong guess only means an early 429, which the router handles.
PROVIDERS: Dict[str, Provider] = {p.name: p for p in [
    Provider("gemini-3.5-flash-lite", GEMINI_URL, "gemini-3.5-flash-lite", "GOOGLE_API_KEY", Limits(rpm=15, rpd=500, tpm=250_000),
             {"reasoning_effort": "none"}),
    Provider("gemini-3.1-flash-lite", GEMINI_URL, "gemini-3.1-flash-lite", "GOOGLE_API_KEY", Limits(rpm=15, rpd=500, tpm=250_000),
             {"reasoning_effort": "none"}),
    Provider("gemma-4-26b", GEMINI_URL, "gemma-4-26b-a4b-it", "GOOGLE_API_KEY", Limits(rpm=15, tpm=15_000)),
    Provider("gemini-3.6-flash", GEMINI_URL, "gemini-3.6-flash", "GOOGLE_API_KEY", Limits(rpm=10, rpd=100), {"reasoning_effort": "low"}),
    Provider("groq-llama-70b", GROQ_URL, "llama-3.3-70b-versatile", "GROQ_API_KEY", Limits(rpm=30, rpd=1000, tpm=12_000, tpd=100_000)),
    Provider("groq-llama-8b", GROQ_URL, "llama-3.1-8b-instant", "GROQ_API_KEY", Limits(rpm=30, rpd=14_400, tpm=6_000, tpd=500_000)),
    Provider("groq-gpt-oss-120b", GROQ_URL, "openai/gpt-oss-120b", "GROQ_API_KEY", Limits(rpm=30, rpd=1000, tpm=8_000, tpd=200_000)),
]}

DEFAULT_CHAINS = {
    "fast": ("gemini-3.5-flash-lite", "groq-llama-70b", "gemini-3.1-flash-lite", "groq-llama-8b"),
    "slow": ("gemma-4-26b", "groq-gpt-oss-120b", "gemini-3.6-flash", "groq-llama-70b"),
}
HEDGE_AFTER_S = {"fast": 2.5, "slow": 7.0}


class AllBusy(Exception):
    """No provider for this role could answer."""


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    latency_s: float
    tokens: int = 0


@dataclass
class Meter:
    """What one provider has used, and whether it is resting."""
    minute: Deque[Tuple[float, int]] = field(default_factory=deque)
    day_start: float = 0.0
    day_requests: int = 0
    day_tokens: int = 0
    rest_until: float = 0.0
    dead: bool = False
    last_latency_s: float = 0.0
    last_error: str = ""
    dropped_extra: bool = False
    dropped_json: bool = False


def retry_after(headers: Dict[str, str], body: str) -> float:
    """How long a 429 asks us to wait."""
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    m = re.search(r"retry in ([\d.]+)\s*s", body, re.I) or re.search(r"retryDelay\W+([\d.]+)s", body) or \
        re.search(r"try again in (?:(\d+)m)?([\d.]+)s", body, re.I)
    if m is None:
        return 30.0
    if len(m.groups()) == 2:
        return float(m.group(1) or 0) * 60 + float(m.group(2))
    return float(m.group(1))


def is_daily_limit(body: str) -> bool:
    return bool(re.search(r"per.?day|\bRPD\b|\bTPD\b|daily", body, re.I))


class Router:
    def __init__(self, chains: Optional[Dict[str, Sequence[str]]] = None, providers: Optional[Dict[str, Provider]] = None,
                 clock: Callable[[], float] = time.time, http=None) -> None:
        self.providers = dict(providers or PROVIDERS)
        self.chains = {k: list(v) for k, v in (chains or _chains_from_env()).items()}
        self.clock = clock
        self.http = http            # an injected transport, for tests: async (provider, payload, timeout) -> (status, headers, body)
        self.meters: Dict[str, Meter] = {name: Meter() for name in self.providers}
        self._session: Optional[aiohttp.ClientSession] = None

    # --- budgets -----------------------------------------------------------------------------------------------
    def _roll(self, m: Meter) -> None:
        now = self.clock()
        while m.minute and now - m.minute[0][0] > 60:
            m.minute.popleft()
        if now - m.day_start >= 86400:
            m.day_start, m.day_requests, m.day_tokens = now, 0, 0

    def has_room(self, name: str, tokens: int) -> bool:
        p, m = self.providers[name], self.meters[name]
        if not p.usable or m.dead or self.clock() < m.rest_until:
            return False
        self._roll(m)
        lim = p.limits
        if lim.rpm is not None and len(m.minute) >= lim.rpm:
            return False
        if lim.tpm is not None and sum(t for _, t in m.minute) + tokens > lim.tpm:
            return False
        if lim.rpd is not None and m.day_requests >= lim.rpd:
            return False
        if lim.tpd is not None and m.day_tokens + tokens > lim.tpd:
            return False
        return True

    def _book(self, name: str, tokens: int) -> None:
        m = self.meters[name]
        self._roll(m)
        m.minute.append((self.clock(), tokens))
        m.day_requests += 1
        m.day_tokens += tokens

    def candidates(self, role: str, tokens: int) -> List[str]:
        return [n for n in self.chains.get(role, []) if n in self.providers and self.has_room(n, tokens)]

    @property
    def available(self) -> bool:
        """Is there any model at all? Without one, Ala runs on code alone."""
        return any(self.providers[n].usable for chain in self.chains.values() for n in chain if n in self.providers)

    def status(self) -> Dict[str, Any]:
        """For /api/health and the panel. Never includes a key."""
        out: Dict[str, Any] = {}
        for role, chain in self.chains.items():
            for name in chain:
                p, m = self.providers.get(name), self.meters.get(name)
                if p is None or name in out:
                    continue
                self._roll(m)
                out[name] = {"configured": p.usable, "requests_today": m.day_requests, "tokens_today": m.day_tokens,
                             "limit_per_day": p.limits.rpd, "resting_s": max(0, round(m.rest_until - self.clock())),
                             "last_latency_s": round(m.last_latency_s, 2), "dead": m.dead, "last_error": m.last_error[:120]}
        return out

    # --- calling -----------------------------------------------------------------------------------------------
    async def _post(self, p: Provider, payload: Dict[str, Any], timeout_s: float) -> Tuple[int, Dict[str, str], str]:
        if self.http is not None:
            return await self.http(p, payload, timeout_s)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        headers = {"Content-Type": "application/json"}
        if p.api_key():
            headers["Authorization"] = "Bearer " + p.api_key()
        async with self._session.post(p.base_url.rstrip("/") + "/chat/completions", json=payload, headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
            return r.status, dict(r.headers), await r.text()

    def _payload(self, p: Provider, messages: List[Dict[str, str]], max_tokens: int, temperature: float) -> Dict[str, Any]:
        m = self.meters[p.name]
        payload: Dict[str, Any] = {"model": p.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if p.json_mode and not m.dropped_json:
            payload["response_format"] = {"type": "json_object"}
        if not m.dropped_extra:
            payload.update(p.extra)
        return payload

    async def _one(self, name: str, messages, max_tokens: int, temperature: float, timeout_s: float, tokens: int) -> Completion:
        p, m = self.providers[name], self.meters[name]
        self._book(name, tokens)
        started = time.monotonic()
        for _ in range(3):
            try:
                status, headers, body = await self._post(p, self._payload(p, messages, max_tokens, temperature), timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:     # a timeout, a dropped connection
                m.last_error = type(exc).__name__
                m.rest_until = self.clock() + 5
                raise RuntimeError("%s: %s" % (name, type(exc).__name__))
            if status == 200:
                data = json.loads(body)
                text = (data["choices"][0]["message"].get("content") or "").strip()
                used = int((data.get("usage") or {}).get("total_tokens") or tokens)
                m.last_latency_s = time.monotonic() - started
                m.last_error = ""
                if used != tokens:
                    m.day_tokens += used - tokens
                return Completion(text, name, p.model, m.last_latency_s, used)
            m.last_error = "%d %s" % (status, body[:100])
            if status == 400 and not m.dropped_extra and p.extra:
                m.dropped_extra = True      # the API doesn't take a setting we added: try without it
                continue
            if status == 400 and not m.dropped_json and p.json_mode:
                m.dropped_json = True
                continue
            if status == 429:
                wait = 3600.0 if is_daily_limit(body) else retry_after(headers, body)
                m.rest_until = self.clock() + wait
                log.warning("%s is out of quota; resting %.0f s", name, wait)
            elif status in (401, 403, 404):
                m.dead = True               # a bad key or a model this key can't use: no retry will fix it
                log.warning("%s refused (%d); not using it again", name, status)
            else:
                m.rest_until = self.clock() + 5
            raise RuntimeError("%s: HTTP %d" % (name, status))
        raise RuntimeError("%s: refused the request" % name)

    async def complete(self, role: str, messages: List[Dict[str, str]], *, max_tokens: int = 300, temperature: float = 0.3,
                       timeout_s: float = 8.0, est_tokens: int = 1600) -> Completion:
        """The first good answer from this role's chain. A provider that errors hands over at once; one that hangs gets a
        backup after HEDGE_AFTER_S, and both stay in the race."""
        names = self.candidates(role, est_tokens)
        if not names:
            raise AllBusy(role)
        deadline = time.monotonic() + timeout_s
        hedge = HEDGE_AFTER_S.get(role, 3.0)
        pending: Dict[asyncio.Task, str] = {}
        errors: List[str] = []
        queue = list(names)

        def launch() -> None:
            name = queue.pop(0)
            left = max(0.5, deadline - time.monotonic())
            pending[asyncio.ensure_future(self._one(name, messages, max_tokens, temperature, left, est_tokens))] = name

        launch()
        try:
            while pending:
                wait_for = min(hedge, max(0.0, deadline - time.monotonic())) if queue else max(0.0, deadline - time.monotonic())
                done, _ = await asyncio.wait(set(pending), timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    if time.monotonic() >= deadline:
                        break
                    if queue:
                        launch()
                    continue
                for task in done:
                    name = pending.pop(task)
                    try:
                        return task.result()
                    except Exception as exc:
                        errors.append(str(exc)[:120])
                if not pending and queue:
                    launch()
        finally:
            for task in pending:
                task.cancel()
        raise AllBusy("%s: %s" % (role, "; ".join(errors) or "timed out"))

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


def _chains_from_env() -> Dict[str, List[str]]:
    """ALA_FAST_CHAIN and ALA_SLOW_CHAIN override the order. ALA_LOCAL_LLM_URL adds a local OpenAI-compatible server,
    such as llama.cpp, in front of both chains."""
    chains = {}
    for role, default in DEFAULT_CHAINS.items():
        raw = os.environ.get("ALA_%s_CHAIN" % role.upper(), "")
        chains[role] = [x.strip() for x in raw.split(",") if x.strip()] or list(default)
    url = os.environ.get("ALA_LOCAL_LLM_URL", "")
    if url:
        PROVIDERS["local"] = Provider("local", url, os.environ.get("ALA_LOCAL_LLM_MODEL", "local"))
        for role in chains:
            chains[role].insert(0, "local")
    return chains


def parse_json(text: str) -> Optional[Dict[str, Any]]:
    """The JSON object in a model's reply, whatever it wrapped it in."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    try:
        out = json.loads(t)
        return out if isinstance(out, dict) else None
    except ValueError:
        pass
    start = t.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            esc = (c == "\\") and not esc
            if c == '"' and not esc:
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    out = json.loads(t[start:i + 1])
                    return out if isinstance(out, dict) else None
                except ValueError:
                    return None
    return None
