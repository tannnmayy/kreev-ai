"""Measuring the voice: what each stretch of speech sounded like.

Ported from Duet's tone channel, whose thresholds rest on measurements (CREMA-D actors and real S24 recordings, 21 Oct 2026):
angry speech was brighter by 18.9 dB at the median and higher by 14 semitones, while loudness rose only 2.3 dB because phones
level it. Two of the three cues (brighter, higher, louder), each against the same speaker's earlier speech, marked 9 of 10
angry takes and none of 53 plain lines. A rising pitch at the end proved unreliable, so it isn't used here.

Duet tracked pitch with Praat through Parselmouth, which is GPL-3.0. This repository is meant to be reused, so pitch comes
from a small YIN tracker in numpy instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

SR = 16000
FRAME = 320                     # 20 ms

# thresholds, from the measurements above
EFFORT_TILT_DB = 6.0            # brighter than the speaker's usual
EFFORT_PITCH_ST = 4.0           # higher than the speaker's usual
EFFORT_LOUD_DB = 6.0            # louder than the speaker's usual (often flattened by automatic gain control)
LOUD_ALONE_DB = 8.0             # loud enough to count on its own


def to_16k(pcm: np.ndarray, rate: int) -> np.ndarray:
    """Linear resampling: plenty for level, brightness and pitch."""
    x = np.asarray(pcm, dtype=np.float32)
    if rate == SR or len(x) == 0:
        return x
    n = int(round(len(x) * SR / float(rate)))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def frames(x: np.ndarray, n: int, hop: int) -> np.ndarray:
    count = 1 + (len(x) - n) // hop if len(x) >= n else 0
    if count <= 0:
        return np.zeros((0, n), dtype=np.float32)
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    return x[idx]


def level_db(x: np.ndarray) -> float:
    if len(x) == 0:
        return -120.0
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-9))


def pitch_track(x: np.ndarray, fmin: float = 75.0, fmax: float = 600.0, threshold: float = 0.15,
                hop: int = 160, window: int = 480) -> np.ndarray:
    """YIN (de Cheveigné and Kawahara, 2002), vectorised over frames. Returns Hz per frame, 0 where unvoiced."""
    x = np.asarray(x, dtype=np.float64)
    tau_max = int(SR / fmin)
    tau_min = max(2, int(SR / fmax))
    span = window + tau_max
    fr = frames(x, span, hop).astype(np.float64)
    if len(fr) == 0:
        return np.zeros(0)
    n_fft = 1 << int(np.ceil(np.log2(span + window)))
    a = np.fft.rfft(fr[:, :window], n_fft, axis=1)
    b = np.fft.rfft(fr, n_fft, axis=1)
    corr = np.fft.irfft(np.conj(a) * b, n_fft, axis=1)[:, : tau_max + 1]
    power = np.cumsum(np.pad(fr ** 2, ((0, 0), (1, 0))), axis=1)
    e0 = power[:, window][:, None]
    taus = np.arange(tau_max + 1)
    etau = power[:, taus + window] - power[:, taus]
    diff = np.maximum(e0 + etau - 2 * corr, 0.0)
    cum = np.cumsum(diff[:, 1:], axis=1)
    cmnd = np.ones_like(diff)
    cmnd[:, 1:] = diff[:, 1:] * taus[1:][None, :] / np.maximum(cum, 1e-12)
    out = np.zeros(len(fr))
    quiet = e0[:, 0] / window < 1e-6
    for i in range(len(fr)):
        if quiet[i]:
            continue
        row = cmnd[i]
        below = np.where(row[tau_min:] < threshold)[0]
        if len(below) == 0:
            continue
        t = int(below[0]) + tau_min
        while t + 1 <= tau_max and row[t + 1] < row[t]:
            t += 1
        if 1 <= t < tau_max:        # parabolic interpolation
            l, c, r = row[t - 1], row[t], row[t + 1]
            denom = l - 2 * c + r
            shift = 0.5 * (l - r) / denom if abs(denom) > 1e-12 else 0.0
        else:
            shift = 0.0
        out[i] = SR / (t + shift)
    return out


def median_pitch(x: np.ndarray) -> float:
    track = pitch_track(x)
    voiced = track[track > 0]
    return float(np.median(voiced)) if len(voiced) >= 5 else float("nan")


@dataclass
class Features:
    """One stretch of customer speech, as measured."""
    on: float = 0.0                # wall-clock time of its first speech frame
    off: float = 0.0               # ... and of the end of its last one
    speech_s: float = 0.0          # seconds of speech in it
    loud_db: float = float("nan")  # 90th percentile of the speech frames' level
    tilt_db: float = float("nan")  # median energy at 1-4 kHz against 80 Hz-1 kHz
    f0_hz: float = float("nan")    # median pitch of the voiced frames
    longest_pause_s: float = 0.0   # the longest silence inside it
    delta: Dict[str, float] = field(default_factory=dict)   # against the speaker's usual, once measured
    words: str = ""
    worded: bool = False
    ended: float = 0.0
    measured: bool = True

    def raised(self) -> List[str]:
        return raised(self.delta)

    def summary(self) -> Dict[str, object]:
        def r(v: float, nd: int = 1):
            return None if v is None or np.isnan(v) else round(float(v), nd)
        return {"speech_s": round(self.speech_s, 2), "loud_db": r(self.loud_db), "tilt_db": r(self.tilt_db),
                "f0_hz": r(self.f0_hz, 0), "pause_s": round(self.longest_pause_s, 2),
                "delta": {k: r(v) for k, v in self.delta.items()}}


_MEASURED = ("on", "off", "speech_s", "loud_db", "tilt_db", "f0_hz", "longest_pause_s")


def speech_level(pcm: np.ndarray) -> Optional[float]:
    """How loud the speech in this audio is, by the same measure as Features.loud_db but without the slower analysis, so it can
    be asked the moment a transcript arrives. None when there's too little speech."""
    fr = frames(np.asarray(pcm, dtype=np.float32), FRAME, FRAME)
    if not len(fr):
        return None
    db = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)
    speech = (db > db.max() - 30) & (db > -60)
    return float(np.percentile(db[speech], 90)) if speech.sum() >= 5 else None


def analyse(pcm: np.ndarray, t0: float = 0.0) -> Optional[Features]:
    """Measure one stretch of 16 kHz mono audio starting at wall-clock time t0. None when there's too little speech."""
    x = np.asarray(pcm, dtype=np.float32)
    if len(x) < SR // 4:
        return None
    fr = frames(x, FRAME, FRAME)
    db = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)
    speech = (db > db.max() - 30) & (db > -60)
    if speech.sum() < 5:
        return None
    idx = np.where(speech)[0]
    out = Features(on=t0 + idx[0] * FRAME / SR, off=t0 + (idx[-1] + 1) * FRAME / SR, speech_s=float(speech.sum() * FRAME / SR))
    out.loud_db = float(np.percentile(db[speech], 90))
    spec = np.abs(np.fft.rfft(fr[speech] * np.hanning(FRAME), axis=1)) ** 2
    freqs = np.fft.rfftfreq(FRAME, 1 / SR)
    hi = spec[:, (freqs >= 1000) & (freqs < 4000)].sum(axis=1) + 1e-12
    lo = spec[:, (freqs >= 80) & (freqs < 1000)].sum(axis=1) + 1e-12
    out.tilt_db = float(np.median(10 * np.log10(hi / lo)))
    out.f0_hz = median_pitch(x[idx[0] * FRAME:(idx[-1] + 1) * FRAME])
    gaps = np.diff(idx) - 1
    out.longest_pause_s = float(gaps.max() * FRAME / SR) if len(gaps) else 0.0
    return out


class Baseline:
    """This speaker's usual voice in this conversation: medians of their plain segments. A naturally loud, bright or high
    speaker is then not taken for an urgent one."""

    def __init__(self, keep: int = 12) -> None:
        self.keep = keep
        self.items: List[Features] = []

    def ready(self) -> bool:
        return len(self.items) >= 2

    def add(self, f: Features) -> None:
        self.items = (self.items + [f])[-self.keep:]

    def _median(self, name: str) -> float:
        vals = [getattr(f, name) for f in self.items if not np.isnan(getattr(f, name))]
        return float(np.median(vals)) if vals else float("nan")

    def effort(self, f: Features) -> Dict[str, float]:
        """How far this segment is from the speaker's usual: loudness and brightness in dB, pitch in semitones."""
        out: Dict[str, float] = {}
        if not self.ready():
            return out
        for name, key in (("loud_db", "loud"), ("tilt_db", "tilt")):
            base = self._median(name)
            if not np.isnan(base) and not np.isnan(getattr(f, name)):
                out[key] = getattr(f, name) - base
        base_f0 = self._median("f0_hz")
        if not np.isnan(base_f0) and not np.isnan(f.f0_hz) and base_f0 > 0:
            out["pitch"] = float(12 * np.log2(f.f0_hz / base_f0))
        return out

    def usual_loudness(self) -> Optional[float]:
        v = self._median("loud_db") if self.items else float("nan")
        return None if np.isnan(v) else v


def raised(delta: Dict[str, float]) -> List[str]:
    """The effort cues a segment shows: brighter, higher, louder. A raised voice is two of them, or loud enough alone."""
    found = []
    if delta.get("tilt", 0.0) >= EFFORT_TILT_DB:
        found.append("brighter %+.0f dB" % delta["tilt"])
    if delta.get("pitch", 0.0) >= EFFORT_PITCH_ST:
        found.append("higher %+.0f semitones" % delta["pitch"])
    if delta.get("loud", 0.0) >= EFFORT_LOUD_DB:
        found.append("louder %+.0f dB" % delta["loud"])
    if len(found) >= 2 or delta.get("loud", 0.0) >= LOUD_ALONE_DB:
        return found
    return []
