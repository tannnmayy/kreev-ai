"""The acoustic end-of-turn score, and how long Ala waits.

The rule: the voice decides when, and words can only make Ala wait longer. A September 2026 ablation found
that adding text features to end-of-turn detection increased premature cut-offs (arXiv 2609.11066), so words never shorten
a wait here. The one exception is a short answer to Ala's own question, where the state makes the end of the turn obvious.

Smart Turn v3.2 (BSD-2) reads the last 8 seconds of audio. It isn't listed for Telugu, and on real Tamil calls it scored only
70% unaided (TamilEOT), so it's one input among several, and tools/turn_eval.py measures whether it earns its place.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from ..language import cues

log = logging.getLogger("ala.turn")

REPO = "pipecat-ai/smart-turn-v3"
REVISION = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"
FILE = "smart-turn-v3.2-cpu.onnx"
SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
SECONDS = 8

UNFINISHED_BELOW = 0.35     # the voice sounds mid-sentence
VOICE_HOLD_S = 1.6          # how long to wait when it does


class SmartTurn:
    """One ONNX session shared by every conversation in this process. Without the model, score() returns None and the
    rest of the layer carries on."""

    def __init__(self) -> None:
        self._session = None
        self._features = None
        self._lock = threading.Lock()
        self._tried = False

    def load(self) -> bool:
        with self._lock:
            if self._tried:
                return self._session is not None
            self._tried = True
            try:
                import onnxruntime as ort
                from huggingface_hub import hf_hub_download
                from transformers import WhisperFeatureExtractor
                path = hf_hub_download(REPO, FILE, revision=REVISION)
                with open(path, "rb") as fh:
                    if hashlib.sha256(fh.read()).hexdigest() != SHA256:
                        raise RuntimeError("the Smart Turn model's checksum doesn't match")
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 1
                self._session = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
                self._features = WhisperFeatureExtractor(chunk_length=SECONDS)
                log.info("smart turn ready")
            except Exception as exc:
                log.warning("no Smart Turn model (%s); the other turn-taking signals still work", str(exc)[:160])
            return self._session is not None

    def score(self, pcm16k: np.ndarray) -> Optional[float]:
        """The probability that the speaker has finished, from the last 8 seconds of 16 kHz audio."""
        if self._session is None and not self.load():
            return None
        x = np.asarray(pcm16k, dtype=np.float32)[-SECONDS * 16000:]
        if len(x) < 1600:
            return None
        feats = self._features(x, sampling_rate=16000, return_tensors="np", padding="max_length",
                               max_length=SECONDS * 16000, truncation=True, do_normalize=True).input_features
        out = self._session.run(None, {"input_features": feats.astype(np.float32)})
        return float(np.asarray(out[0]).ravel()[0])


SMART_TURN = SmartTurn()


@dataclass
class TurnAssessment:
    """How long the customer must stay quiet before Ala answers, and why."""
    hold_s: float
    reasons: List[str] = field(default_factory=list)
    p_end: Optional[float] = None


def assess(words: str, *, base_s: float, dangling_s: float, revising_s: float, p_end: Optional[float] = None,
           expecting_answer: bool = False, unsure: bool = False, raised_waiting: bool = False) -> TurnAssessment:
    """Decision 1: has the customer finished? Every rule below can only lengthen the wait."""
    reasons: List[str] = []
    hold = base_s
    short_answer = expecting_answer and (cues.is_yes(words) or cues.is_no(words))
    if short_answer:
        reasons.append("short answer to Ala's own question")
    elif cues.unfinished(words):
        hold = max(hold, dangling_s)
        reasons.append("the words leave the sentence open")
    elif cues.revising(words):
        hold = max(hold, revising_s)
        reasons.append("the customer is correcting themselves")
    if p_end is not None and p_end < UNFINISHED_BELOW and not short_answer:
        hold = max(hold, VOICE_HOLD_S)
        reasons.append("the voice sounds unfinished (%.2f)" % p_end)
    if unsure:
        hold = max(hold, revising_s)
        reasons.append("the customer sounds unsure of a value")
    if raised_waiting:
        hold = max(hold, 4.0)
        reasons.append("a raised voice whose words haven't arrived")
    return TurnAssessment(hold, reasons, p_end)
