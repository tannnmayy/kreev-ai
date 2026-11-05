"""KREEV-Bench: simulated customers with hidden instructions, in text.

    uv run python -m ala.bench.simulate --runs 3            # every scenario, three times each
    uv run python -m ala.bench.simulate --only te-order

A language model plays a customer who follows a hidden instruction Ala never sees. Success isn't a judge's opinion: the final
cart and orders are checked in code. Consistency is pass^k, which counts a scenario only if all k runs pass. It runs on free
models, so it's spread over days with --sample, and every run is saved under reports/ so the numbers can be recomputed with no
model at all.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from ..config import ROOT
from ..language.detect import classify
from ..language.speakable import violations
from ..minds.brain import Brain
from ..minds.router import AllBusy, Router, parse_json
from ..shop.catalog import Catalog
from ..shop.engine import ShopEngine
from ..shop.state import SessionState
from .deck import check_state, instant_coordinator, spoken_turn

SCENARIOS = ROOT / "scenarios" / "customers.yaml"
MAX_TURNS = 12

CUSTOMER_SYSTEM = """\
You play a customer phoning a voice shopping assistant called Ala at a coastal Andhra market. Stay in character and follow your \
hidden instruction exactly, one step at a time. Don't reveal it, and don't reveal facts before you're asked unless it says to. \
Speak like a real person on the phone: short, natural, sometimes vague. {language}

Answer with JSON only: {{"say": "what you say next", "done": false}}
Set "done" to true, with "say" empty, once your instruction is fully carried out or Ala has clearly failed."""

LANGUAGE = {"en": "Speak English.", "te": "Speak natural spoken Telugu, in Telugu script.",
            "mixed": "Mix Telugu and English the way people in Andhra do, Telugu in Telugu script."}


def load() -> List[Dict[str, Any]]:
    return yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]


@dataclass
class Run:
    scenario: str
    passed: bool
    problems: List[str]
    turns: List[Tuple[str, str]]
    style: List[str] = field(default_factory=list)
    language_ok: int = 0
    language_total: int = 0
    seconds: float = 0.0


async def customer_says(router: Router, scenario: Dict[str, Any], turns: Sequence[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    transcript = "\n".join("%s: %s" % (who, text) for who, text in turns)
    system = CUSTOMER_SYSTEM.format(language=LANGUAGE.get(scenario["lang"], LANGUAGE["en"]))
    user = "Your hidden instruction: %s\n\nThe call so far:\n%s\n\nWhat do you say next?" % (scenario["hidden"], transcript)
    completion = await router.complete("sim", [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                       max_tokens=200, temperature=0.7, timeout_s=15, est_tokens=700)
    return parse_json(completion.text)


async def run_once(scenario: Dict[str, Any], catalog: Catalog, ala: Router, customer: Router) -> Run:
    state = SessionState()
    if scenario.get("setup", {}).get("usual"):
        state.customer.name, state.customer.returning = "Ravi", True
        state.customer.usual = scenario["setup"]["usual"]
    brain = Brain(ShopEngine(catalog, state), ala, instant_coordinator())
    started = time.monotonic()
    turns: List[Tuple[str, str]] = []
    style: List[str] = []
    language_ok = language_total = 0
    line = scenario["opening"]
    reply = ""
    for _ in range(MAX_TURNS):
        said = await spoken_turn(brain, line)
        reply = " ".join(said)
        turns += [("customer", line), ("ala", reply)]
        for s in said:
            style += violations(s, list(brain.spoken)[:-1])
        heard, spoke = classify(line), classify(reply)
        if heard in ("te", "en") and spoke in ("te", "en") and brain.language.pinned is None:
            language_total += 1
            language_ok += heard == spoke
        try:
            nxt = await customer_says(customer, scenario, turns)
        except AllBusy:
            break
        if not nxt or nxt.get("done") or not str(nxt.get("say", "")).strip():
            break
        line = str(nxt["say"]).strip()
    problems = check_state({"check": scenario.get("check") or {}}, brain, reply)
    return Run(scenario["id"], not problems, problems, turns, style, language_ok, language_total, time.monotonic() - started)


def summarise(runs: Sequence[Run], k: int) -> Dict[str, Any]:
    by: Dict[str, List[Run]] = {}
    for r in runs:
        by.setdefault(r.scenario, []).append(r)
    passes = sum(1 for r in runs if r.passed)
    all_pass = sum(1 for v in by.values() if len(v) >= k and all(r.passed for r in v))
    lang_total = sum(r.language_total for r in runs)
    replies = sum(len(r.turns) // 2 for r in runs)
    return {"scenarios": len(by), "runs": len(runs), "pass_rate": round(passes / len(runs), 3) if runs else 0.0,
            "pass_all_k": round(all_pass / len(by), 3) if by else 0.0, "k": k,
            "language_match": round(sum(r.language_ok for r in runs) / lang_total, 3) if lang_total else None,
            "style_violations_per_reply": round(sum(len(r.style) for r in runs) / replies, 3) if replies else 0.0}


async def main_async(args: argparse.Namespace) -> int:
    catalog = Catalog.load()
    scenarios = [s for s in load() if not args.only or s["id"] in args.only]
    if args.sample:
        scenarios = random.Random(args.seed).sample(scenarios, min(args.sample, len(scenarios)))
    ala = Router()
    customer = Router(chains={"sim": list(ala.chains["fast"])})
    if not ala.available:
        print("KREEV-Bench needs at least one model key in .env.local. The real-life deck runs without any.")
        return 2
    runs: List[Run] = []
    for s in scenarios:
        for i in range(args.runs):
            r = await run_once(s, catalog, ala, customer)
            runs.append(r)
            print("%s %-22s run %d  %s" % ("ok  " if r.passed else "FAIL", s["id"], i + 1, "; ".join(r.problems)[:110]), flush=True)
    summary = summarise(runs, args.runs)
    out = ROOT / "reports" / ("kreev-bench-%s.json" % time.strftime("%Y%m%d-%H%M"))
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "runs": [r.__dict__ for r in runs]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print("saved", out)
    return 0


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--sample", type=int, default=0, help="a random subset, to spread free quota over days")
    parser.add_argument("--seed", type=int, default=7)
    sys.exit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
