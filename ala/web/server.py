"""The front door for local runs and Docker: the page, room tokens and the health check.

    uv run python -m ala.web.server        # http://localhost:8787

The public deployment does the same from Vercel (api/). The LiveKit secret stays on the server: the browser only ever holds a
short-lived token for one room.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict

from aiohttp import web
from livekit import api

from ..config import ROOT, SETTINGS
from ..minds.router import Router
from ..shop.store import open_store

log = logging.getLogger("ala.web")
WWW = ROOT / "web"
DEV_URL, DEV_KEY, DEV_SECRET = "ws://localhost:7880", "devkey", "secret"


def livekit_settings() -> Dict[str, str]:
    """LIVEKIT_PUBLIC_URL is what a browser can reach, which in Docker differs from the agent's LIVEKIT_URL."""
    url = os.environ.get("LIVEKIT_URL") or DEV_URL
    return {"url": os.environ.get("LIVEKIT_PUBLIC_URL") or url, "server_url": url,
            "key": os.environ.get("LIVEKIT_API_KEY") or DEV_KEY, "secret": os.environ.get("LIVEKIT_API_SECRET") or DEV_SECRET}


def _clip(value: Any, n: int) -> str:
    return " ".join(str(value or "").split())[:n]


def token_for(query: Dict[str, str]) -> Dict[str, Any]:
    if SETTINGS.invite_code and query.get("invite", "") != SETTINGS.invite_code:
        raise web.HTTPForbidden(text="This demo needs an invite code. It's in the link you were sent.")
    lk = livekit_settings()
    session = re.sub(r"[^\w-]", "", query.get("session", ""))[:40] or secrets.token_hex(8)
    room = "ala-" + secrets.token_hex(4)
    meta = {"session": session, "name": _clip(query.get("name"), 40), "lang": query.get("lang", "")}
    jwt = (api.AccessToken(lk["key"], lk["secret"])
           .with_identity("customer-" + secrets.token_hex(3))
           .with_name(meta["name"] or "Customer")
           .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=True))
           .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=SETTINGS.agent_name,
                                                                                 metadata=json.dumps(meta))]))
           .with_ttl(dt.timedelta(minutes=30))
           .to_jwt())
    return {"url": lk["url"], "token": jwt, "room": room, "session": session}


async def handle_token(request: web.Request) -> web.Response:
    return web.json_response(token_for(dict(request.query)), headers={"Cache-Control": "no-store"})


async def handle_warm(request: web.Request) -> web.Response:
    """Wake a sleeping agent while the customer reads the page: a job that ends at once."""
    lk = livekit_settings()
    try:
        async with api.LiveKitAPI(lk["server_url"].replace("ws", "http", 1), lk["key"], lk["secret"]) as client:
            await client.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
                agent_name=SETTINGS.agent_name, room="warm-" + secrets.token_hex(3), metadata=json.dumps({"warm": True})))
        return web.json_response({"ok": True})
    except Exception as exc:
        return web.json_response({"ok": False, "why": type(exc).__name__})


_health: Dict[str, Any] = {"at": 0.0, "out": None}


async def health() -> Dict[str, Any]:
    """Which services this run can reach. Cached so a monitor can't spend quota. Never includes a key."""
    if _health["out"] is not None and time.monotonic() - _health["at"] < 30:
        return _health["out"]
    router = Router()
    store_ok = False
    try:
        store = open_store()
        store_ok = await asyncio.wait_for(store.ping(), 5)
        await store.close()
    except Exception:
        pass
    out = {"ok": True, "livekit": {"url_set": bool(os.environ.get("LIVEKIT_URL")), "local_dev": not os.environ.get("LIVEKIT_URL")},
           "models": {name: v["configured"] for name, v in router.status().items()},
           "listening": {"sarvam": bool(os.environ.get("SARVAM_API_KEY")), "gladia": bool(os.environ.get("GLADIA_API_KEY"))},
           "voice": {"sarvam": bool(os.environ.get("SARVAM_API_KEY"))}, "store": store_ok,
           "invite_required": bool(SETTINGS.invite_code)}
    _health.update(at=time.monotonic(), out=out)
    return out


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(await health(), headers={"Cache-Control": "no-store"})


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WWW / "index.html")


def app() -> web.Application:
    application = web.Application()
    application.router.add_get("/api/token", handle_token)
    application.router.add_post("/api/warm", handle_warm)
    application.router.add_get("/api/health", handle_health)
    application.router.add_get("/", handle_index)
    application.router.add_static("/", WWW)
    return application


def main() -> None:
    port = int(os.environ.get("ALA_WEB_PORT", "8787"))
    print("Ala: page and token server on http://localhost:%d" % port, flush=True)
    web.run_app(app(), host=os.environ.get("ALA_WEB_HOST", "0.0.0.0"), port=port, print=None)


if __name__ == "__main__":
    main()
