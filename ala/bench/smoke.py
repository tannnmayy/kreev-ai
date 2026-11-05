"""A customer in code: join a real room, talk to the real agent, check what comes back.

    uv run python -m ala.bench.smoke [--url http://localhost:8787]

It needs the LiveKit server, the agent and the web server running, and no keys: it types instead of speaking. CI and Docker run
it after the stack starts, so "it works on a clean machine" is something checked rather than claimed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp
from livekit import rtc

SCRIPT = [
    ("అర కిలో రొయ్యలు వేయండి", "₹350"),
    ("శుభ్రం చేసి ఇవ్వండి", "శుభ్రం"),
    ("what's in my cart", "prawns"),
    ("place the order", "Shall I place the order"),
    ("yes", "placed"),
]


class Customer:
    def __init__(self) -> None:
        self.room = rtc.Room()
        self.replies: asyncio.Queue = asyncio.Queue()
        self.state: Dict[str, Any] = {}
        self.ready = asyncio.Event()

    def _on_data(self, packet: rtc.DataPacket) -> None:
        if packet.topic != "ala":
            return
        msg = json.loads(packet.data.decode("utf-8"))
        if msg.get("kind") == "caption" and msg.get("who") == "ala":
            self.replies.put_nowait(msg["text"])
        elif msg.get("kind") == "state":
            self.state = msg
        elif msg.get("kind") == "ready":
            self.ready.set()

    async def join(self, base: str, invite: str) -> None:
        query = urlencode({"name": "", "session": "smoke-%d" % int(time.time()), "invite": invite})
        async with aiohttp.ClientSession() as http:
            async with http.get("%s/api/token?%s" % (base.rstrip("/"), query)) as r:
                r.raise_for_status()
                details = await r.json()
        self.room.on("data_received", self._on_data)
        await self.room.connect(details["url"], details["token"])

    async def say(self, text: str, timeout: float = 15.0) -> str:
        await self.room.local_participant.publish_data(json.dumps({"kind": "text", "text": text}, ensure_ascii=False),
                                                       reliable=True, topic="ala.client")
        return await asyncio.wait_for(self.replies.get(), timeout)


async def run(base: str, invite: str) -> int:
    c = Customer()
    started = time.monotonic()
    await c.join(base, invite)
    await asyncio.wait_for(c.ready.wait(), 60)
    print("agent joined in %.1f s" % (time.monotonic() - started))
    greeting = await asyncio.wait_for(c.replies.get(), 20)
    print("greeting:", greeting)
    failed = 0
    for line, expected in SCRIPT:
        reply = await c.say(line)
        ok = expected in reply
        failed += 0 if ok else 1
        print("%s  %s -> %s" % ("ok  " if ok else "FAIL", line, reply))
    await asyncio.sleep(0.5)
    orders = len(c.state.get("orders", []))
    print("orders on screen:", orders)
    failed += 0 if orders == 1 else 1
    await c.room.disconnect()
    print("smoke test %s" % ("passed" if not failed else "FAILED"))
    return 1 if failed else 0


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8787")
    parser.add_argument("--invite", default="")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.url, args.invite)))


if __name__ == "__main__":
    main()
