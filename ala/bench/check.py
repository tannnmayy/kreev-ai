"""Does this machine reproduce the project? Run by `docker compose up` once the stack is running.

    python -m ala.bench.check

Three things, each needing no keys: the test suite, the real-life deck, and a real call through the running stack.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

from ..config import ROOT
from . import deck, smoke

URL = "http://localhost:8787"


async def wait_for_web(seconds: float = 90) -> bool:
    end = time.monotonic() + seconds
    async with aiohttp.ClientSession() as http:
        while time.monotonic() < end:
            try:
                async with http.get(URL + "/api/health", timeout=aiohttp.ClientTimeout(total=3)) as r:
                    if r.status == 200:
                        return True
            except Exception:
                pass
            await asyncio.sleep(1)
    return False


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    results = {}
    print("== tests", flush=True)
    results["tests"] = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "--no-header"], cwd=ROOT).returncode == 0
    print("== real-life deck", flush=True)
    passed, total, details, skipped = asyncio.run(deck.run_all(None))
    for card, problem in details.items():
        if problem:
            print("FAIL", card, problem)
    print("%d of %d cards passed; %d need a person, a call or a model" % (passed, total, skipped), flush=True)
    results["deck"] = passed == total
    print("== a real call through the running stack", flush=True)
    results["call"] = asyncio.run(wait_for_web()) and asyncio.run(smoke.run(URL, "")) == 0
    out = ROOT / "reports" / "check.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"results": results, "deck": "%d/%d" % (passed, total), "at": time.time()}, indent=1), encoding="utf-8")
    ok = all(results.values())
    print("\n" + "=" * 64)
    print("Ala reproduced on this machine: %s" % ("YES" if ok else "NO"))
    print("   tests: %s   deck: %s   real call: %s" % tuple("pass" if results[k] else "FAIL" for k in ("tests", "deck", "call")))
    print("   open http://localhost:8787 and choose \"Type instead\", or add free keys to .env.local for voice")
    print("=" * 64, flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
