"""Where things outlive a conversation: sessions, customers, orders, stock and flagged moments.

One small key-value table serves everything, so a local SQLite file and a free Neon Postgres behave the same. The agent
can be restarted, or a page refreshed in the middle of an order, and the cart comes back.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from ..config import SETTINGS


class Store:
    async def get(self, ns: str, key: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def put(self, ns: str, key: str, value: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def delete(self, ns: str, key: str) -> None:
        raise NotImplementedError

    async def all(self, ns: str, limit: int = 200) -> List[Tuple[str, Dict[str, Any]]]:
        raise NotImplementedError

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass

    # --- what the agent uses ---------------------------------------------------------------------
    async def load_stock(self) -> Dict[str, float]:
        return {k: float(v["qty"]) for k, v in await self.all("stock", 500)}

    async def set_stock(self, sku: str, qty: float) -> None:
        await self.put("stock", sku, {"qty": qty})

    async def load_session(self, key: str) -> Optional[Dict[str, Any]]:
        return await self.get("session", key)

    async def save_session(self, key: str, state: Dict[str, Any]) -> None:
        await self.put("session", key, state)

    async def load_profile(self, name: str) -> Optional[Dict[str, Any]]:
        return await self.get("profile", profile_key(name))

    async def save_profile(self, name: str, profile: Dict[str, Any]) -> None:
        await self.put("profile", profile_key(name), profile)

    async def forget_profile(self, name: str) -> None:
        await self.delete("profile", profile_key(name))

    async def save_order(self, order_id: str, order: Dict[str, Any]) -> None:
        await self.put("order", order_id, order)

    async def save_flag(self, key: str, flag: Dict[str, Any]) -> None:
        await self.put("flag", key, flag)


def profile_key(name: str) -> str:
    return " ".join((name or "").lower().split())


class MemoryStore(Store):
    """For tests, and for a call when no database can be reached."""

    def __init__(self) -> None:
        self.data: Dict[Tuple[str, str], str] = {}

    async def get(self, ns, key):
        raw = self.data.get((ns, key))
        return json.loads(raw) if raw is not None else None

    async def put(self, ns, key, value):
        self.data[(ns, key)] = json.dumps(value, ensure_ascii=False)

    async def delete(self, ns, key):
        self.data.pop((ns, key), None)

    async def all(self, ns, limit=200):
        return [(k, json.loads(v)) for (n, k), v in list(self.data.items()) if n == ns][:limit]


_SCHEMA = ("CREATE TABLE IF NOT EXISTS kv (ns TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
           "updated DOUBLE PRECISION NOT NULL, PRIMARY KEY (ns, key))")


class SqliteStore(Store):
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(_SCHEMA)
        self.db.commit()
        self.lock = asyncio.Lock()

    async def _run(self, sql: str, args: tuple = (), fetch: bool = False):
        async with self.lock:
            def work():
                cur = self.db.execute(sql, args)
                rows = cur.fetchall() if fetch else None
                self.db.commit()
                return rows
            return await asyncio.to_thread(work)

    async def get(self, ns, key):
        rows = await self._run("SELECT value FROM kv WHERE ns=? AND key=?", (ns, key), True)
        return json.loads(rows[0][0]) if rows else None

    async def put(self, ns, key, value):
        await self._run("INSERT INTO kv (ns, key, value, updated) VALUES (?,?,?,?) "
                        "ON CONFLICT(ns, key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                        (ns, key, json.dumps(value, ensure_ascii=False), time.time()))

    async def delete(self, ns, key):
        await self._run("DELETE FROM kv WHERE ns=? AND key=?", (ns, key))

    async def all(self, ns, limit=200):
        rows = await self._run("SELECT key, value FROM kv WHERE ns=? ORDER BY updated DESC LIMIT ?", (ns, limit), True)
        return [(k, json.loads(v)) for k, v in rows]

    async def close(self):
        self.db.close()


class PostgresStore(Store):
    def __init__(self, url: str) -> None:
        self.url = url
        self.pool = None
        self.lock = asyncio.Lock()

    async def _pool(self):
        async with self.lock:
            if self.pool is None:
                import asyncpg
                # Neon's free tier sleeps when idle, so the first connection may take a moment
                self.pool = await asyncpg.create_pool(self.url, min_size=0, max_size=4, timeout=10, command_timeout=8,
                                                      statement_cache_size=0)
                async with self.pool.acquire() as c:
                    await c.execute(_SCHEMA)
            return self.pool

    async def get(self, ns, key):
        pool = await self._pool()
        row = await pool.fetchrow("SELECT value FROM kv WHERE ns=$1 AND key=$2", ns, key)
        return json.loads(row["value"]) if row else None

    async def put(self, ns, key, value):
        pool = await self._pool()
        await pool.execute("INSERT INTO kv (ns, key, value, updated) VALUES ($1,$2,$3,$4) "
                           "ON CONFLICT (ns, key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                           ns, key, json.dumps(value, ensure_ascii=False), time.time())

    async def delete(self, ns, key):
        pool = await self._pool()
        await pool.execute("DELETE FROM kv WHERE ns=$1 AND key=$2", ns, key)

    async def all(self, ns, limit=200):
        pool = await self._pool()
        rows = await pool.fetch("SELECT key, value FROM kv WHERE ns=$1 ORDER BY updated DESC LIMIT $2", ns, limit)
        return [(r["key"], json.loads(r["value"])) for r in rows]

    async def ping(self):
        pool = await self._pool()
        return (await pool.fetchval("SELECT 1")) == 1

    async def close(self):
        if self.pool is not None:
            await self.pool.close()


def open_store() -> Store:
    """Postgres when DATABASE_URL is set, otherwise a local SQLite file."""
    if SETTINGS.database_url:
        return PostgresStore(SETTINGS.database_url)
    return SqliteStore(SETTINGS.sqlite_path)
