"""Runtime settings, read once from the environment.

Every setting that changes behaviour lives here, with a default that works with no keys at all.
Secrets are read from the environment only and are never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- identity -------------------------------------------------------------------------
    agent_name: str = field(default_factory=lambda: _env("ALA_AGENT_NAME", "ala"))
    invite_code: str = field(default_factory=lambda: _env("ALA_INVITE_CODE"))
    max_call_minutes: float = field(default_factory=lambda: _float("ALA_MAX_CALL_MINUTES", 15))

    # --- turn taking: how long Ala waits, in seconds of quiet after the customer's last word ---
    # A cart edit can be corrected later, so it waits less than an order, which can't.
    commit_hold_s: float = field(default_factory=lambda: _float("ALA_COMMIT_HOLD", 0.8))
    order_hold_s: float = field(default_factory=lambda: _float("ALA_ORDER_HOLD", 1.4))
    revising_hold_s: float = field(default_factory=lambda: _float("ALA_REVISING_HOLD", 1.8))
    dangling_hold_s: float = field(default_factory=lambda: _float("ALA_DANGLING_HOLD", 2.2))
    endpoint_min_s: float = field(default_factory=lambda: _float("ALA_ENDPOINT_MIN", 0.5))
    endpoint_max_s: float = field(default_factory=lambda: _float("ALA_ENDPOINT_MAX", 2.4))

    # --- the two minds ----------------------------------------------------------------------
    fast_timeout_s: float = field(default_factory=lambda: _float("ALA_FAST_TIMEOUT", 4.0))
    slow_timeout_s: float = field(default_factory=lambda: _float("ALA_SLOW_TIMEOUT", 12.0))
    bridge_after_s: float = field(default_factory=lambda: _float("ALA_BRIDGE_AFTER", 1.2))

    # --- data -------------------------------------------------------------------------------
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    sqlite_path: str = field(default_factory=lambda: _env("ALA_SQLITE", str(ROOT / "data" / "ala.sqlite")))
    trace_dir: str = field(default_factory=lambda: _env("ALA_TRACE_DIR", str(ROOT / "traces")))


SETTINGS = Settings()
